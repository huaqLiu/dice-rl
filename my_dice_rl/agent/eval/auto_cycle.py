"""Explicitly armed automatic collection; never opens the gripper or launches a learner."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import json
import time

import numpy as np

from my_dice_rl.agent.eval.dp_executor import DPExecutor
from my_dice_rl.agent.eval.process_policy import ProcessPolicy
from my_dice_rl.agent.eval.rl_policy import load_execution_policy
from my_dice_rl.util.auto_cycle_config import auto_cycle_config
from my_dice_rl.util.config import PROJECT_ROOT
from my_dice_rl.util.reset_motion import ResetTrajectory, load_reset_pose
from my_dice_rl.util.rl_coordination import DeviceLease
from my_dice_rl.util.rotation import quaternion_angle


class AutoCycleExecutor(DPExecutor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.auto_config = auto_cycle_config(self.config.get('auto_cycle'), self.config)
        self.auto_active, self.auto_count, self.auto_phase = False, 0, 'idle'
        self.auto_batch, self.auto_log_path = [], None
        self.audit = ThreadPoolExecutor(max_workers=1, thread_name_prefix='auto_audit')

    def _event(self, name, **values):
        if self.auto_log_path is None:
            return
        row = dict(event=name, wall_time=time.time(), episode_count=self.auto_count, **values)
        path = self.auto_log_path
        def append():
            with path.open('a') as file:
                file.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+'\n')
        self.audit.submit(append)

    def _at_goal(self, actual, goal):
        c = self.auto_config
        return (np.linalg.norm(actual[:3]-goal[:3]) <= c['position_tolerance_m']
                and quaternion_angle(actual[3:], goal[3:]) <= c['orientation_tolerance_rad'])

    def _request(self, operation, data, now):
        if operation == 'auto_start':
            if self.auto_config is None or self.auto_active or self.closing:
                raise ValueError('自动循环未配置或正在运行')
            if self.state != 'RESET' or self.future is not None or self.policy is None:
                raise ValueError('先完成标注保存并加载策略')
            if not data.get('held_confirmed'):
                raise ValueError('请确认同一个齿轮始终夹持、轴竖直且已完成首次复位')
            ref = self.config.get('deployment_review') or str(PROJECT_ROOT/'cfg/rl_review.yaml')
            position, orientation = load_reset_pose(ref, self.collector['base_frame'], self.collector['ee_frame'])
            self.auto_goal = np.r_[position, orientation]
            actual = np.asarray(self.snapshot(now)['eef'].value)
            if not self._at_goal(actual, self.auto_goal) or actual[2] <= self.auto_config['success_z_m']:
                raise ValueError('请先人工返回固定起点；自动循环要求实际位置/姿态已收敛')
            stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S_%f')
            directory = Path(self.config['data_root'])/'_auto_sessions'
            directory.mkdir(parents=True, exist_ok=True)
            self.auto_log_path = directory/(stamp+'.jsonl')
            super()._request('ready', data, now)
            self.auto_active, self.auto_count, self.auto_phase = True, 0, 'policy_ready'
            self.ready_started = now
            self.auto_batch = []
            self._event('armed', config=self.auto_config, reset_pose=self.auto_goal.tolist())
            return
        if operation in ('stop', 'close'):
            was_auto = self.auto_active
            self.auto_active = False
            if was_auto:
                self._event('operator_stop', operation=operation, state=self.state)
            if self.state == 'AUTO_LABEL':
                self.state, self.message = 'LABEL', '自动循环已暂停，请人工标注本条'
                if operation == 'close':
                    super()._request(operation, data, now)
                return
            if self.state.startswith('AUTO_'):
                self.closing |= operation == 'close'
                self._halt_motion(now, '自动循环已由操作员停止')
                return
        elif self.auto_active and operation in ('load', 'ready', 'start', 'manual_enable', 'manual_reset', 'label'):
            raise ValueError('自动循环中请先按 2 / 0 / Esc 停止')
        super()._request(operation, data, now)

    def _send_gripper(self, target):
        if self.auto_active and target > self.config['grasp_threshold']:
            raise ValueError('自动循环禁止策略松爪：请检查夹爪动作')
        super()._send_gripper(target)

    def _stop(self, now, reason, unsafe=False, diagnostics=None):
        if unsafe and self.auto_active:
            self.auto_active = False
            self._event('policy_fault', reason=reason)
        super()._stop(now, reason, unsafe, diagnostics)

    def _control(self, now):
        if self.auto_active:
            frame = self.snapshot(now)
            self.backend.check_ready()
            if now-self.last_tick > self.config['control_timeout_s']:
                raise ValueError('控制循环调度超时')
            if float(frame['eef'].value[2]) < self.auto_config['success_z_m']:
                self._event('success_height_reached', z_m=float(frame['eef'].value[2]))
                self._stop(now, 'automatic height success')
                return
        super()._control(now)

    def _stopping(self, now):
        super()._stopping(now)
        if not self.auto_active or self.state != 'LABEL':
            return
        self.auto_label_started = now
        self._automatic_label(now)

    def _automatic_label(self, now):
        if self.writer.error:
            self.auto_active = False
            self.state, self.message = 'SAVING', '自动循环写盘失败，等待停止保存线程'
            return
        if self.writer.frames < 2:
            if now-self.auto_label_started > self.auto_config['label_wait_timeout_s']:
                self.auto_active = False
                self.state, self.message = 'LABEL', '等待有效观测写盘超时，请人工处理本条'
            else:
                self.state, self.message = 'AUTO_LABEL', '机械臂已停止，等待观测写盘后自动标注'
            return
        try:
            frame = self.snapshot(now)
        except Exception as exc:
            self.auto_active = False
            self.writer.finish('aborted', str(exc), self.notes, unsafe=True,
                               diagnostics=getattr(exc, 'diagnostics', None))
            self.state, self.message = 'SAVING', '停止时观测异常，已终止自动循环'
            return
        z = float(frame['eef'].value[2])
        outcome = 'success' if z < self.auto_config['success_z_m'] else 'failed'
        if self.stop_reason == 'automatic height success' and outcome != 'success':
            self.auto_active = False
            self.state = 'LABEL'
            self.message = '停止后高度回到60mm以上，请人工复核本条；自动循环已暂停'
            self._event('height_rebounded', z_m=z)
            return
        if self.stop_reason not in ('automatic height success', 'timeout'):
            self.auto_active = False
            self.state = 'LABEL'
            return
        diagnostic = dict(automatic_label=dict(rule='actual_tcp_z_strictly_less',
            threshold_m=self.auto_config['success_z_m'], stopped_z_m=z,
            base_frame=self.collector['base_frame'], ee_frame=self.collector['ee_frame']))
        self.writer.finish(outcome, self.stop_reason, self.notes, diagnostics=diagnostic)
        self.auto_phase, self.state = 'saving', 'SAVING'
        self.message = '正在自动保存 '+outcome
        self._event('automatic_label', outcome=outcome, z_m=z)

    def _begin_motion(self, now, retract):
        actual = np.asarray(self.snapshot(now)['eef'].value)
        goal = actual.copy() if retract else self.auto_goal.copy()
        if retract:
            goal[2] = max(actual[2], self.auto_goal[2])
        c = self.auto_config
        self.motion = ResetTrajectory((actual[:3], actual[3:]), (goal[:3], goal[3:]),
            c['linear_speed'], c['angular_speed'], c['linear_acceleration'], c['angular_acceleration'],
            c['max_distance_m'], self.config['max_rotation_from_reset'])
        if self.motion.duration+c['settle_s'] >= c['motion_timeout_s']:
            raise ValueError('自动退轴/复位预计超过超时上限')
        self.motion_started, self.motion_tick, self.motion_settled = now, now, None
        self.motion_goal = goal
        self.state = 'AUTO_RETRACT' if retract else 'AUTO_RETURN'
        self.message = '保持夹持，沿基坐标+Z退轴' if retract else '已退出，返回初始XY与姿态'
        self._event(self.state, actual=actual.tolist(), goal=goal.tolist())

    def _motion_tick(self, now):
        c = self.auto_config
        frame = self.snapshot(now)
        self.backend.check_ready()
        if now-self.motion_tick > self.config['control_timeout_s']:
            raise ValueError('自动复位控制调度超时')
        self.motion_tick = now
        elapsed = now-self.motion_started
        if elapsed > c['motion_timeout_s']:
            raise ValueError('自动退轴/复位超时')
        actual = np.asarray(frame['eef'].value)
        position, orientation = self.motion.sample(elapsed)
        candidate = np.r_[position, orientation]
        if (np.linalg.norm(candidate[:3]-actual[:3]) > c['tracking_position_error_m']
                or quaternion_angle(candidate[3:], actual[3:]) > c['tracking_orientation_error_rad']):
            raise ValueError('自动退轴/复位跟踪误差超限')
        self.backend.publish(candidate)
        if elapsed < self.motion.duration or not self._at_goal(actual, self.motion_goal):
            self.motion_settled = None
        elif self.motion_settled is None:
            self.motion_settled = now
        elif now-self.motion_settled >= c['settle_s']:
            if self.state == 'AUTO_RETRACT':
                self._begin_motion(now, retract=False)
            else:
                self.backend.deactivate()
                self._event('reset_reached', actual=actual.tolist())
                self._after_return(now)

    def _after_return(self, now):
        if self.auto_count >= self.auto_config['max_episodes']:
            self._finish('自动循环已完成设定轮数')
        elif self.auto_config['wait_for_training'] and len(self.auto_batch) >= self.auto_config['batch_episodes']:
            self.state, self.auto_phase = 'AUTO_WAIT', 'training'
            self.wait_started, self.next_index_poll, self.pending_step = now, now, None
            self.seen_index = None
            self.message = '已复位，等待现有 --watch learner 导入本批轨迹并更新'
            self._event('waiting_for_training', episodes=list(self.auto_batch))
        else:
            self._next_episode(now)

    def _next_episode(self, now):
        self.backend.release()
        self.state = 'RESET'
        self.auto_phase = 'policy_ready'
        self.ready_started = now
        super()._request('ready', dict(held_confirmed=True), now)

    def _load_when_idle(self, path, deadline):
        lease = DeviceLease(self.config['device_lock']) if self.config.get('device_lock') else None
        try:
            while self.auto_active and not self.closing and time.monotonic() < deadline:
                if lease is None or lease.acquire():
                    loader = ProcessPolicy if self.config.get('inference_backend', 'process') == 'process' else load_execution_policy
                    return loader(path, self.config['device'], self.collector, not self.simulated, self.config)
                time.sleep(.1)
            raise RuntimeError('自动加载已停止或等待GPU超时')
        finally:
            if lease:
                lease.release()

    def _wait_tick(self, now):
        c = self.auto_config
        actual = np.asarray(self.snapshot(now)['eef'].value)
        self.backend.check_ready()
        if not self._at_goal(actual, self.auto_goal):
            raise ValueError('等待训练时实际位姿离开起点')
        if now-self.wait_started > c['training_wait_timeout_s']:
            raise ValueError('等待训练超时，请检查 --watch 进程及训练上限')
        if self.pending_step is not None and self.future is None:
            if self.policy is None:
                raise ValueError('自动策略加载失败')
            trained = set(self.policy.identity.get('trained_rollouts', []))
            covered = set(self.auto_batch) & trained
            if (self.policy.identity.get('global_step', -1) >= self.pending_step and covered
                    and self.policy.identity.get('training_round_complete', True)):
                self.auto_batch = [path for path in self.auto_batch if path not in trained]
                self._event('new_policy_loaded', path=self.policy.path,
                            step=self.policy.identity['global_step'], pending_episodes=len(self.auto_batch))
                self.pending_step = None
                # The learner may already have unconsumed manual episodes at
                # session start. Carry the remaining paths into the next batch
                # instead of waiting forever for fewer than min_new_episodes.
                self._next_episode(now)
                return
            self.pending_step = None
            self.message = '新策略尚未包含本批全部轨迹，继续等待learner'
        if self.future is not None or now < self.next_index_poll:
            return
        self.next_index_poll = now+c['poll_s']
        path = Path(c['latest_policy_index'])
        if not path.exists():
            return
        index = json.loads(path.read_text())
        version = (index.get('global_step', -1), index.get('sha256'))
        if version == self.seen_index or version[0] <= self.policy.identity.get('global_step', 0):
            return
        self.seen_index, self.pending_step = version, version[0]
        self.future = self.pool.submit(self._load_when_idle, str(path),
                                      time.monotonic()+c['training_wait_timeout_s'])
        self.future_kind = 'load'

    def _finish(self, message):
        self.auto_active, self.auto_phase = False, 'idle'
        self.backend.release()
        self.state, self.message = 'RESET', message
        self._event('finished', message=message)

    def _halt_motion(self, now, message):
        self.auto_active = False
        self._event('halted', reason=message, state=self.state)
        self.backend.cancel()
        self.auto_stop_started = now
        self.state, self.message = 'AUTO_STOPPING', message

    def _halt_tick(self, now):
        latest = self.buffer.latest('eef')
        if latest is not None and 0 <= now-latest.received <= self.config['max_age_s']:
            try:
                self.backend.publish(np.asarray(latest.value))
            except RuntimeError:
                pass  # Waiting for training has no active target publisher.
        if self.backend.gripper_busy:
            if now-self.auto_stop_started > self.config['cancel_timeout_s']:
                self.state, self.message = 'FAULT', '夹爪取消未确认，保留控制锁；请现场处理'
            return
        if now-self.auto_stop_started >= .1:
            self._finish(self.message)

    def tick(self, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            super().tick(now)
            try:
                if self.state == 'AUTO_STOPPING':
                    self._halt_tick(now)
                elif self.auto_active:
                    if self.state == 'READY':
                        if self.auto_phase == 'policy_ready':
                            actual = np.asarray(self.snapshot(now)['eef'].value)
                            self.backend.check_ready()
                            if not self._at_goal(actual, self.auto_goal):
                                raise ValueError('自动启动前实际位姿离开起点')
                            if now-self.ready_started > self.auto_config['training_wait_timeout_s']:
                                raise ValueError('等待GPU启动下一轮超时')
                            if self.device_lease and not self.device_lease.acquire():
                                self.message = '在起点保持暂停，等待learner释放GPU'
                                return
                            super()._request('start', {}, now)
                            self.auto_phase = 'executing'
                        elif self.auto_phase == 'motion_ready':
                            self.backend.activate()
                            self._begin_motion(now, retract=True)
                    elif self.state == 'RESET' and self.auto_phase == 'saving' and self.future is None:
                        if self.writer.error or not self.writer.meta.get('eligible_for_rl'):
                            raise ValueError('自动轨迹保存失败或不具备训练资格')
                        self.auto_count += 1
                        self.auto_batch.append(str((self.last_path/'rollout.h5').resolve()))
                        self.auto_phase = 'motion_ready'
                        super()._request('ready', dict(held_confirmed=True), now)
                    elif self.state == 'RESET' and self.auto_phase in ('policy_ready', 'motion_ready'):
                        raise ValueError('自动循环控制权交接失败，请检查状态后重新启用')
                    elif self.state in ('AUTO_RETRACT', 'AUTO_RETURN'):
                        self._motion_tick(now)
                    elif self.state == 'AUTO_LABEL':
                        self._automatic_label(now)
                    elif self.state == 'AUTO_WAIT':
                        self._wait_tick(now)
            except Exception as exc:
                if self.state == 'RUNNING':
                    self._stop(now, str(exc), unsafe=True)
                elif self.state == 'SAVING':
                    self.auto_active = False
                    self.message = str(exc)
                else:
                    self._halt_motion(now, str(exc))

    def status(self):
        result = super().status()
        result['auto'] = dict(available=self.auto_config is not None, active=self.auto_active,
            episodes=self.auto_count, phase=self.auto_phase,
            max_episodes=self.auto_config['max_episodes'] if self.auto_config else 0)
        return result

    def shutdown(self):
        self.auto_active = False
        super().shutdown()
        self.audit.shutdown(wait=True)
