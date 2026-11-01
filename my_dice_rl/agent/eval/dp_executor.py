"""Single control-thread state machine; inference and disk writing run independently."""
from concurrent.futures import ThreadPoolExecutor
import queue
import shutil
import threading
import time

import numpy as np

from my_dice_rl.agent.eval.command_limits import check_observation, limit_command
from my_dice_rl.agent.eval.rl_policy import load_execution_policy
from my_dice_rl.agent.eval.process_policy import ProcessPolicy
from my_dice_rl.env.real.policy_observation import ObservationSyncError
from my_dice_rl.util.rl_coordination import DeviceLease
from my_dice_rl.util.rl_recording import RolloutWriter
from my_dice_rl.util.rotation import quaternion_angle


class DPExecutor:
    def __init__(self, config, collector, buffer, backend, simulated=False, policy=None):
        self.config, self.collector, self.buffer, self.backend = config, collector, buffer, backend
        self.simulated, self.policy = simulated, policy
        self.state, self.message = 'RESET', '请人工复位并加载正式 DP checkpoint'
        self.lock, self.requests = threading.RLock(), queue.SimpleQueue()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='dp_inference')
        self.future, self.future_kind, self.generation = None, '', 0
        self.lock_future, self.cancel_lock = None, False
        self.writer, self.last_path, self.plan = None, None, None
        self.commands, self.plan_id, self.last_tick = 0, -1, None
        self.stop_reason, self.unsafe, self.notes = '', False, ''
        self.stop_diagnostics = None
        self.next_record, self.last_observation_tick = 0., -float('inf')
        self.closing = False
        self.inference_timing = {}
        self.manual_future, self.manual_stop_future = None, None
        self.manual_pause_pending = False
        self.device_lease = DeviceLease(config['device_lock']) if config.get('device_lock') else None

    def _load_policy(self, path):
        loader = ProcessPolicy if self.config.get('inference_backend', 'process') == 'process' else load_execution_policy
        if self.config.get('device_lock'):
            with DeviceLease(self.config['device_lock']):
                return loader(path, self.config['device'], self.collector, not self.simulated, self.config)
        return loader(path, self.config['device'], self.collector, not self.simulated, self.config)

    @staticmethod
    def _close_policy(policy):
        close = getattr(policy, 'close', None)
        if close is not None:
            close()

    def submit(self, operation, **kwargs):
        self.requests.put((operation, kwargs))

    def _manual_status(self):
        sample = self.buffer.latest('teleop')
        fresh = (sample is not None and 'teleop' not in self.buffer.errors
                 and 0 <= time.monotonic()-sample.received <= self.config['max_age_s'])
        telemetry = sample.value if fresh else {}
        available = hasattr(self.backend, 'request_control') and (self.simulated or fresh)
        locked = self.backend.has_lock or bool(telemetry.get('policy_locked')) or bool(telemetry.get('recording_locked'))
        resetting = bool(telemetry.get('resetting'))
        busy = self.manual_future is not None or self.manual_stop_future is not None or self.manual_pause_pending
        manual = self.state == 'RESET' and self.future is None and not self.closing and not locked
        return dict(available=available, enabled=bool(telemetry.get('enabled', getattr(self.backend, 'manual_enabled', False))),
                    resetting=resetting, busy=busy, locked=locked,
                    can_enable=manual and available and not busy and not resetting,
                    can_reset=manual and available and not busy and not resetting,
                    can_pause=self.state == 'RESET' and not self.closing and hasattr(self.backend, 'request_control'),
                    message=telemetry.get('reset_message') or getattr(self.backend, 'control_message', ''))

    def _manual_call(self, action):
        future = self.backend.request_control(action)
        if future is None:
            raise ValueError(getattr(self.backend, 'control_message', '') or '遥操作服务未就绪')
        return future

    def _pause_manual(self):
        # Send pause now, then repeat after an earlier enable/reset acknowledges:
        # a delayed service response must never re-enable motion after Stop.
        self.manual_pause_pending = self.manual_future is not None
        self.manual_stop_future = self._manual_call('pause')
        self.message = '正在暂停 SpaceMouse / 停止复位……'

    def _poll_manual(self):
        if self.manual_future is not None and self.manual_future.done():
            future, self.manual_future = self.manual_future, None
            try:
                response = future.result()
                if not self.manual_pause_pending:
                    self.message = response.message
            except Exception as exc:
                self.message = '遥操作请求失败：'+str(exc)
            if self.manual_pause_pending:
                self.manual_pause_pending = False
                try:
                    self.manual_stop_future = self._manual_call('pause')
                except Exception as exc:
                    self.message = str(exc)
        if self.manual_stop_future is not None and self.manual_stop_future.done():
            future, self.manual_stop_future = self.manual_stop_future, None
            try:
                self.message = future.result().message
            except Exception as exc:
                self.message = '暂停请求失败：'+str(exc)

    def snapshot(self, now):
        try:
            frame = self.buffer.snapshot(now, self.config['max_age_s'], self.config['max_sensor_skew_s'],
                                         self.config.get('max_gripper_age_s'))
        except ObservationSyncError as exc:
            exc.diagnostics['query_kind'] = 'current'
            raise
        check_observation(frame, self.config)
        return frame

    def _history(self, now):
        return self.buffer.history(now, self.policy.obs_steps, self.policy.dt,
                                   self.config['max_age_s'], self.config['max_sensor_skew_s'],
                                   self.config.get('max_gripper_age_s'))

    def _request(self, operation, data, now):
        if self.closing and operation not in ('stop', 'label', 'close'):
            raise ValueError('窗口正在关闭，不能开始新的控制请求')
        if operation in ('manual_enable', 'manual_reset'):
            manual = self._manual_status()
            if not manual['can_enable' if operation == 'manual_enable' else 'can_reset']:
                raise ValueError('先停止策略、完成标注保存并交回控制权；复位或其他遥操作请求进行中时请等待')
            action = 'enable' if operation == 'manual_enable' else 'reset'
            self.manual_future = self._manual_call(action)
            self.message = '等待遥操作响应……' if action == 'enable' else '请求回到保存的初始位姿……'
        elif operation == 'load':
            if self.state != 'RESET' or self.future is not None:
                raise ValueError('请先结束本轮并等待推理退出')
            self.future = self.pool.submit(self._load_policy, data['path'])
            self.future_kind = 'load'
            self.message = '正在加载 EMA 模型与预热……'
        elif operation == 'ready':
            if self.state != 'RESET' or self.policy is None or self.future is not None:
                raise ValueError('需加载模型、结束上一轮标注并等待推理空闲')
            manual = self._manual_status()
            if manual['busy'] or manual['resetting']:
                raise ValueError('请等待复位完成和遥操作响应，再确认策略就绪')
            if not data.get('held_confirmed'):
                raise ValueError('请确认齿轮已夹稳且人工复位完成')
            self.snapshot(now)
            self._history(now)
            self.lock_future = self.backend.acquire()
            self.cancel_lock, self.lock_started, self.state = False, now, 'LOCKING'
            self.message = '正在暂停 SpaceMouse 并核对控制权……'
        elif operation == 'start':
            if self.state != 'READY':
                raise ValueError('每轮都需要先确认复位就绪')
            frame = self.snapshot(now)
            self.backend.check_ready()
            self._history(now)
            actual = np.asarray(frame['eef'].value)
            if (np.linalg.norm(actual[:3]-self.reset_pose[:3]) > self.config['reset_drift_m']
                    or quaternion_angle(actual[3:], self.reset_pose[3:]) > self.config['reset_drift_rad']):
                raise ValueError('确认后末端发生移动，请取消就绪并重新确认')
            root = self.config['data_root']
            import os
            os.makedirs(root, exist_ok=True)
            if shutil.disk_usage(root).free < self.config['min_free_gb'] * 1e9:
                raise ValueError('磁盘空间不足')
            writer_config = {**self.config, 'collector': self.collector,
                             'camera_info': dict(self.buffer.camera_info), 'policy_dt': self.policy.dt}
            if self.device_lease and not self.device_lease.acquire():
                raise ValueError('训练进程正在使用 GPU，请稍后再按开始')
            writer = None
            try:
                writer = RolloutWriter(root, self.config['task'], writer_config,
                                       self.policy.identity, self.simulated)
                self.backend.activate()
            except Exception:
                if writer is not None:
                    writer.finish('aborted', 'activation failed', unsafe=True)
                if self.device_lease:
                    self.device_lease.release()
                raise
            self.writer = writer
            self.generation += 1
            self.commands, self.plan_id, self.plan = 0, -1, None
            self.inference_timing = {}
            self.last_command = actual.copy()
            self.started, self.next_inference, self.last_tick = now, now, now
            self.next_record, self.last_observation_tick = now+self.policy.dt, now
            self.writer.observation(frame, 0)
            self.command_times = [(now, 0)]
            self.state, self.message, self.notes = 'RUNNING', '策略执行中', data.get('notes', '')
        elif operation == 'stop':
            if self.state == 'RESET' and hasattr(self.backend, 'request_control'):
                self._pause_manual()
            else:
                self._stop(now, 'manual stop')
        elif operation == 'label':
            if self.state != 'LABEL' or self.writer.frames < 2:
                raise ValueError('先停止执行，且至少需要两帧有效观测')
            if data['outcome'] not in ('success', 'failed', 'aborted'):
                raise ValueError('Unknown outcome')
            self.writer.finish(data['outcome'], self.stop_reason,
                               data.get('notes', self.notes), unsafe=self.unsafe)
            self.state, self.message = 'SAVING', '正在保存标注和轨迹……'
        elif operation == 'close':
            self.closing = True
            if self.state == 'RESET' and hasattr(self.backend, 'request_control'):
                self._pause_manual()
            if self.state == 'LABEL':
                self.writer.finish('aborted', 'window closed without label', unsafe=True)
                self.state = 'SAVING'
            else:
                self._stop(now, 'window closed', unsafe=True)

    def _stop(self, now, reason, unsafe=False, diagnostics=None):
        if self.state == 'LOCKING':
            self.cancel_lock = True
            return
        if self.state == 'READY':
            self.backend.release()
            self.state, self.message = 'RESET', '就绪已取消，请重新人工复位'
            return
        if self.state != 'RUNNING':
            return
        self.generation += 1  # A late inference result can never activate a subsequent episode.
        self.plan = None
        self.stop_reason, self.unsafe = reason, unsafe
        self.stop_diagnostics = diagnostics
        self.stop_started, self.state = now, 'STOPPING'
        self.backend.cancel()
        self.message = '停止中：保持新鲜实测位姿，等待夹爪取消确认'

    def _poll(self, now):
        if self.future is None or not self.future.done():
            return
        future, kind = self.future, self.future_kind
        self.future = None
        try:
            value = future.result()
            if kind == 'load':
                self._close_policy(self.policy)
                self.policy, self.message = value, '模型已加载；请人工复位并确认就绪'
                if self.config['max_plan_age_s'] >= self.policy.horizon*self.policy.dt:
                    raise ValueError('max_plan_age_s 必须小于模型预测时长')
            elif kind == 'predict':
                self.inference_timing = dict(getattr(self.policy, 'last_timing', {}))
                if self.inference_timing:
                    self.inference_timing['poll_ms'] = max(0., (now-self.inference_timing['received'])*1000)
                if self.prediction_generation != self.generation or self.state != 'RUNNING':
                    return
                latency = now-self.prediction_history[-1]['tick']
                if latency > self.config['max_inference_latency_s']:
                    raise ValueError(f'推理结果过迟：{latency:.3f}s')
                actions, noise = value
                if actions.shape != (self.policy.horizon, 10) or not np.isfinite(actions).all():
                    raise ValueError('模型返回无效动作序列')
                self.plan_id += 1
                self.plan = dict(tick=self.prediction_history[-1]['tick'], actions=actions,
                                 latency=latency, id=self.plan_id)
                history = self.prediction_history
                values = dict(tick=np.float64(self.plan['tick']),
                    latency=np.float64(latency), actions=actions, init_noise=noise,
                    history_ticks=np.asarray([f['tick'] for f in history]),
                    actual_pose=np.stack([f['eef'].value for f in history]),
                    actual_gripper=np.asarray([f['gripper'].value for f in history]))
                values.update({f'inference_{key}': np.float64(self.inference_timing[key])
                               for key in ('queue_ms', 'compute_ms', 'return_ms', 'poll_ms')
                               if key in self.inference_timing})
                self.writer.put('plans', values,
                    {key: np.stack([f[key].value for f in history]) for key in ('wrist', 'external')})
        except Exception as exc:
            if self.state == 'RUNNING':
                self._stop(now, str(exc), unsafe=True, diagnostics=getattr(exc, 'diagnostics', None))
            else:
                self.message = str(exc)
                if kind == 'load':
                    self._close_policy(self.policy)
                    self.policy = None

    def _handover(self, now):
        if self.lock_future is not None and not self.lock_future.done():
            if now-self.lock_started > self.config['handover_timeout_s']:
                self.cancel_lock = True
                self.message = '交接超时，等待响应以释放可能取得的锁'
            return
        if self.lock_future is not None:
            try:
                response = self.lock_future.result()
            except Exception as exc:
                self.lock_future = None
                self.backend.acquired()
                self.backend.release()
                self.state, self.message = 'RESET', '控制权交接失败：'+str(exc)
                return
            self.lock_future = None
            if not response.success:
                self.state, self.message = 'RESET', response.message
                return
        self.backend.acquired()
        if self.cancel_lock:
            self.backend.release()
            self.state = 'RESET'
            return
        try:
            self.backend.check_ready()
            self.reset_pose = np.asarray(self.snapshot(now)['eef'].value).copy()
            self.state, self.message = 'READY', '人工复位已确认；按开始执行本轮'
        except Exception as exc:
            if now-self.lock_started > self.config['handover_timeout_s']:
                self.backend.release()
                self.state, self.message = 'RESET', str(exc)

    def _control(self, now):
        if now-self.last_tick > self.config['control_timeout_s']:
            raise ValueError('控制循环调度超时')
        dt = max(0., min(now-self.last_tick, 1/self.config['control_hz']*2))
        self.last_tick = now
        frame = self.snapshot(now)
        self.backend.check_ready()
        if now-self.started >= self.config['max_episode_s']:
            self._stop(now, 'timeout')
            return
        if self.writer.error:
            raise ValueError(self.writer.error)
        if (self.future is not None and self.future_kind == 'predict'
                and now-self.prediction_history[-1]['tick'] > self.config['max_inference_latency_s']):
            raise ValueError('推理超时；已停止等待，迟到结果将被丢弃')
        if self.future is None and now >= self.next_inference:
            self.prediction_history = self._history(now)
            self.prediction_generation = self.generation
            self.future = self.pool.submit(self.policy.predict, self.prediction_history)
            self.future_kind = 'predict'
            self.next_inference = now+self.policy.act_steps*self.policy.dt
        from my_dice_rl.util.rotation import pose7_to_pose9
        requested = np.r_[pose7_to_pose9(frame['eef'].value), self.backend.gripper_target]
        plan_id, action_index = -1, -1
        if self.plan is not None:
            age = now-self.plan['tick']
            action_index = int(age/self.policy.dt)
            if age > self.config['max_plan_age_s'] or action_index >= self.policy.horizon:
                raise ValueError('动作计划过旧')
            requested, plan_id = self.plan['actions'][action_index], self.plan['id']
            self.last_command = limit_command(requested, self.last_command,
                np.asarray(frame['eef'].value), self.reset_pose, dt, self.config)
            self.backend.send_gripper(float(requested[9]))
        self.backend.publish(self.last_command)
        self.writer.put('commands', dict(tick=np.float64(now), requested_action=requested,
            pose=self.last_command.copy(), gripper_target=np.float32(self.backend.gripper_target),
            gripper_mode=np.uint8(self.backend.gripper_mode), gripper_busy=np.uint8(self.backend.gripper_busy),
            plan_id=np.int64(plan_id), action_index=np.int64(action_index)))
        self.commands += 1
        self.command_times.append((now, self.commands))
        if now >= self.next_record:
            if now-self.next_record > self.policy.dt:
                raise ValueError('观测记录漏采一个周期')
            try:
                record = self.buffer.snapshot(self.next_record, self.config['max_age_s'], self.config['max_sensor_skew_s'],
                                              self.config.get('max_gripper_age_s'))
            except ObservationSyncError as exc:
                exc.diagnostics['query_kind'] = 'record'
                raise
            index = next(count for tick, count in reversed(self.command_times) if tick <= self.next_record)
            self.writer.observation(record, index)
            self.last_observation_tick = self.next_record
            self.next_record += self.policy.dt

    def _stopping(self, now):
        try:
            latest = self.buffer.latest('eef')
            if latest is not None and now-latest.received <= self.config['max_age_s']:
                self.backend.publish(np.asarray(latest.value))
                if not self.writer.error:
                    from my_dice_rl.util.rotation import pose7_to_pose9
                    self.writer.put('commands', dict(tick=np.float64(now),
                        requested_action=np.r_[pose7_to_pose9(latest.value), self.backend.gripper_target],
                        pose=np.asarray(latest.value), gripper_target=np.float32(self.backend.gripper_target),
                        gripper_mode=np.uint8(self.backend.gripper_mode), gripper_busy=np.uint8(self.backend.gripper_busy),
                        plan_id=np.int64(-1), action_index=np.int64(-1)))
                    self.commands += 1
            else:
                self.unsafe = True
        except Exception:
            self.unsafe = True
        if self.backend.gripper_busy and now-self.stop_started > self.config['cancel_timeout_s']:
            self.unsafe = True
            self.writer.finish('aborted', self.stop_reason+'; gripper cancellation unconfirmed', unsafe=True,
                               diagnostics=self.stop_diagnostics)
            self.state = 'FAULT'
            self.message = '夹爪取消未确认，保留遥操作锁并禁止下一轮；请现场确认设备状态，可关闭窗口退出'
            return
        if now-self.stop_started < .1 or self.backend.gripper_busy:
            return
        try:
            record = self.buffer.snapshot(now, self.config['max_age_s'], self.config['max_sensor_skew_s'],
                                          self.config.get('max_gripper_age_s'))
            if record['tick'] > self.last_observation_tick and not self.writer.error:
                self.writer.observation(record, self.commands)
        except Exception:
            self.unsafe = True
        self.backend.deactivate()
        if self.unsafe or self.closing:
            self.writer.finish('aborted', self.stop_reason, self.notes, unsafe=True,
                               diagnostics=self.stop_diagnostics)
            self.state = 'SAVING'
        else:
            self.state, self.message = 'LABEL', '执行已停止；请标记成功、失败或放弃本条'

    def tick(self, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            self._poll(now)
            self._poll_manual()
            while not self.requests.empty():
                operation, data = self.requests.get()
                try:
                    self._request(operation, data, now)
                except Exception as exc:
                    self.message = str(exc)
            try:
                if self.state == 'LOCKING':
                    self._handover(now)
                elif self.state == 'RUNNING':
                    self._control(now)
                elif self.state == 'STOPPING':
                    self._stopping(now)
                elif self.state == 'SAVING' and self.writer.closed:
                    self.backend.release()
                    self.last_path = self.writer.path
                    self.state, self.message = 'RESET', ('保存失败：'+self.writer.error if self.writer.error
                        else f'已保存 {self.writer.meta["outcome"]}；请重新人工复位')
            except Exception as exc:
                if self.state == 'RUNNING':
                    self._stop(now, str(exc), unsafe=True, diagnostics=getattr(exc, 'diagnostics', None))
                else:
                    self.message = str(exc)
            if self.device_lease and self.state not in ('RUNNING', 'STOPPING') and self.future is None:
                self.device_lease.release()

    def status(self):
        with self.lock:
            return dict(state=self.state, message=self.message, model=self.policy.path if self.policy else '',
                busy=self.future is not None, frames=self.writer.frames if self.writer else 0,
                manual=self._manual_status(),
                inference_timing=dict(self.inference_timing),
                path=str(self.writer.path) if self.writer else '', stop_reason=self.stop_reason,
                can_close=(self.state == 'RESET' or self.state == 'FAULT' and self.writer.closed)
                          and self.future is None and self.manual_future is None
                          and self.manual_stop_future is None and not self.manual_pause_pending)

    def shutdown(self):
        self.pool.shutdown(wait=False, cancel_futures=True)
        def cleanup(future=None):
            self._close_policy(self.policy)
            if future is not None and self.future_kind == 'load' and not future.cancelled():
                try:
                    self._close_policy(future.result())
                except Exception:
                    pass
            if self.device_lease:
                self.device_lease.release()
        if self.future is not None and not self.future.done():
            self.future.add_done_callback(cleanup)
        else:
            cleanup(self.future)
