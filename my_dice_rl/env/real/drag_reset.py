"""拖拽页面的异步随机复位；只在按8后切换控制器，不操作夹爪。"""
import json
import time
import threading

import numpy as np
from controller_manager_msgs.srv import ListControllers, LoadController, ConfigureController, SwitchController
from geometry_msgs.msg import PoseStamped

from my_dice_rl.util.config import project_path
from my_dice_rl.util.drag_reset import random_reset_goal, reset_waypoints
from my_dice_rl.util.recording import atomic_json
from my_dice_rl.util.reset_motion import ResetTrajectory, load_reset_pose
from my_dice_rl.util.rotation import quaternion_angle


class DragReset:
    def __init__(self, owner, config):
        self.owner, self.config = owner, config
        self.lock = threading.RLock()
        self.reference_path = project_path(config['reference_file'])
        manager = config['controller_manager'].rstrip('/')
        self.clients = {name: owner.create_client(cls, manager+'/'+name) for name, cls in (
            ('list_controllers', ListControllers), ('load_controller', LoadController),
            ('configure_controller', ConfigureController), ('switch_controller', SwitchController))}
        self.publisher = owner.create_publisher(PoseStamped, config['command_topic'], 1)
        self.busy, self.stopping = False, False
        self.pending, self.monitor = None, None
        self.goal = None
        self.phase, self.motion, self.motion_goal = 'IDLE', None, None
        self.checked = None
        self.diagnostics = {}
        self.message = '8随机复位；9登记当前为原始位姿'
        self.rng = np.random.default_rng()
        self.audit = project_path(config.get('audit_file', 'log_dir/collection/drag_reset_events.jsonl'))
        owner.create_timer(.02, self.tick)

    def reference(self):
        return load_reset_pose(self.reference_path, self.owner.config['base_frame'], self.owner.config['ee_frame'])

    def event(self, kind, **values):
        self.audit.parent.mkdir(parents=True, exist_ok=True)
        with self.audit.open('a') as stream:
            stream.write(json.dumps(dict(event=kind, wall_time=time.time(), **values), allow_nan=False)+'\n')

    def save_reference(self):
        with self.lock:
            return self._save_reference()

    def _save_reference(self):
        if self.busy or self.owner.recording_locked or self.owner.desired:
            raise ValueError('先暂停拖拽并结束保存，再登记原始位姿')
        if (self.owner.controller_state or {}).get('mode') == 1:
            raise ValueError('等待控制器进入保持状态')
        self.owner.safety_check()
        pose = np.asarray(self.owner.fresh_sample('eef').value)
        if pose.shape != (7,) or abs(np.linalg.norm(pose[3:])-1.) > .001:
            raise ValueError('实际位姿无效')
        atomic_json(self.reference_path, dict(reset_base_frame=self.owner.config['base_frame'],
            reset_ee_frame=self.owner.config['ee_frame'], reset_position_m=pose[:3].tolist(),
            reset_orientation_xyzw=pose[3:].tolist()))
        self.event('save_reference', pose=pose.tolist())
        self.message = '已保存独立原始位姿；不会修改RL复位配置'
        return self.message

    def trajectory(self, start, goal):
        c = self.config
        return ResetTrajectory((start[:3], start[3:]), (goal[:3], goal[3:]),
            c['linear_speed'], c['angular_speed'], c['linear_acceleration'], c['angular_acceleration'],
            c['max_distance_m'], c['max_angle_rad'])

    def start(self):
        # Qt invokes start while the ROS executor invokes tick. Publish a fully
        # initialized state, never a busy flag before CHECK exists.
        with self.lock:
            return self._start()

    def _start(self):
        if self.busy or self.owner.recording_locked:
            raise ValueError('请先结束录制和保存，或等待当前复位结束')
        self.owner.safety_check()
        if self.owner.registered_width is None:
            raise ValueError('请先按3登记已有夹持')
        if self.owner.count_publishers(self.config['command_topic']) != 1:
            raise ValueError('存在其他末端目标发布者，请关闭SpaceMouse/DP/RL执行程序')
        if any(not client.service_is_ready() for client in self.clients.values()):
            raise ValueError('等待controller_manager服务')
        reference = self.reference()
        goal = random_reset_goal(reference, self.config['position_noise_m'],
                                 self.config['rotation_noise_deg'], self.rng)
        actual = np.asarray(self.owner.fresh_sample('eef').value)
        waypoints = reset_waypoints(actual, reference, goal, self.config['position_noise_m'], self.config['clearance_m'])
        duration, previous = 0., actual
        for _, waypoint in waypoints:
            duration += self.trajectory(previous, waypoint).duration+self.config['settle_s']
            previous = waypoint
        if duration+8. >= self.config['timeout_s']:
            raise ValueError('预计复位时间超限，请先人工移近原始位姿')
        self.owner.request_control('pause')
        self.owner.pause_reason = ''
        self.owner.origin = None
        self.goal, self.waypoints = goal, waypoints
        self.reference_pose, self.stage_index = reference, 0
        self.started, self.last_tick = time.monotonic(), time.monotonic()
        self.stopping, self.stop_reason = False, ''
        self.monitor = None
        self.phase, self.motion, self.motion_goal = 'PREPARING', None, None
        self.checked = None
        self.diagnostics = {}
        self.next_progress = self.started
        self.message = '正在准备随机复位控制器'
        self.event('random_goal', reference=np.r_[reference[0], reference[1]].tolist(), goal=goal.tolist(),
                   delta_xyz_m=(goal[:3]-reference[0]).tolist(),
                   delta_angle_deg=float(np.rad2deg(quaternion_angle(goal[3:], reference[1]))))
        self.call('CHECK', 'list_controllers', ListControllers.Request())
        self.last_tick = time.monotonic()
        self.busy = True
        return self.message

    def call(self, kind, name, request):
        if not self.clients[name].service_is_ready():
            raise ValueError('控制器服务不可用：'+name)
        self.pending = (kind, self.clients[name].call_async(request), time.monotonic())

    def switch(self, back=False):
        request = SwitchController.Request()
        cart, drag = self.config['controller_name'], self.owner.config['controller_name']
        request.activate_controllers = [drag if back else cart]
        request.deactivate_controllers = [cart if back else drag]
        request.strictness = SwitchController.Request.STRICT
        request.activate_asap = True
        request.timeout.sec = 2
        self.call('BACK' if back else 'CART', 'switch_controller', request)

    def controllers(self, response, expected):
        controllers = {item.name: item for item in response.controller}
        selected = controllers.get(expected)
        interfaces = {f'panda_joint{i}/effort' for i in range(1, 8)}
        if selected is None or selected.state != 'active' or set(selected.claimed_interfaces) != interfaces:
            raise ValueError('控制器未占用七个effort接口：'+expected)
        for name, item in controllers.items():
            if name != expected and item.state == 'active' and any(
                    value.startswith('panda_joint') and value.rsplit('/', 1)[-1] in ('effort', 'position', 'velocity')
                    for value in item.claimed_interfaces):
                raise ValueError('另一个运动控制器处于active：'+name)
        return controllers

    def publish(self, pose):
        msg = PoseStamped()
        msg.header.stamp = self.owner.get_clock().now().to_msg()
        msg.header.frame_id = self.owner.config['base_frame']
        p, q = msg.pose.position, msg.pose.orientation
        p.x, p.y, p.z = map(float, pose[:3])
        q.x, q.y, q.z, q.w = map(float, pose[3:])
        self.publisher.publish(msg)

    def cancel(self, reason):
        with self.lock:
            if self.busy and not self.stopping:
                self.stopping, self.stop_reason = True, reason
                self.message = reason+'；正在停止并恢复拖拽保持控制器'
                self.event('stop_requested', message=reason, phase=self.phase, diagnostics=self.diagnostics)

    def finish(self):
        self.busy = False
        self.owner.desired = False
        self.owner.pause_reason = self.stop_reason
        self.message = self.stop_reason or '随机复位完成，保持暂停；按1启用拖拽，按4开始新示教'
        if self.stop_reason and self.diagnostics:
            self.message += (f"；停止于{self.diagnostics['stage']}，距本段目标 "
                f"{self.diagnostics['position_error_m']*1000:.1f} mm / "
                f"{np.rad2deg(self.diagnostics['orientation_error_rad']):.1f}°")
        self.owner.control_message = self.message
        self.event('stopped' if self.stop_reason else 'completed', message=self.message,
                   goal=self.goal.tolist() if self.goal is not None else None,
                   phase=self.phase, diagnostics=self.diagnostics)
        self.phase = 'IDLE'

    def response(self, kind, response, now):
        cart, drag = self.config['controller_name'], self.owner.config['controller_name']
        if kind == 'BACK':
            if not response.ok:
                raise ValueError('恢复拖拽控制器失败，需现场检查控制器状态')
            self.call('VERIFY_BACK', 'list_controllers', ListControllers.Request())
        elif kind == 'VERIFY_BACK':
            self.controllers(response, drag)
            self.finish()
        elif self.stopping:
            if kind == 'STOP_CHECK':
                states = {item.name: item for item in response.controller}
                if states.get(drag) is not None and states[drag].state == 'active':
                    self.controllers(response, drag)
                    self.finish()
                elif states.get(cart) is not None and states[cart].state == 'active':
                    self.controllers(response, cart)
                    self.switch(back=True)
                else:
                    raise ValueError('无法确认运动控制器，需现场检查')
            else:
                self.call('STOP_CHECK', 'list_controllers', ListControllers.Request())
        elif kind == 'CHECK':
            states = self.controllers(response, drag)
            if cart not in states:
                self.call('LOAD', 'load_controller', LoadController.Request(name=cart))
            elif states[cart].state == 'unconfigured':
                self.call('CONFIGURE', 'configure_controller', ConfigureController.Request(name=cart))
            elif states[cart].state == 'inactive':
                self.switch()
            else:
                raise ValueError('复位控制器状态不正确')
        elif kind == 'LOAD':
            if not response.ok:
                raise ValueError('加载复位阻抗控制器失败')
            self.call('CONFIGURE', 'configure_controller', ConfigureController.Request(name=cart))
        elif kind == 'CONFIGURE':
            if not response.ok:
                raise ValueError('配置复位阻抗控制器失败')
            self.switch()
        elif kind == 'CART':
            if not response.ok:
                raise ValueError('切换到复位控制器失败')
            self.call('VERIFY_CART', 'list_controllers', ListControllers.Request())
        elif kind == 'VERIFY_CART':
            self.controllers(response, cart)
            self.checked, self.next_check = now, now+.2
            self.sync_until = now+.2
            self.motion = None
            self.phase = 'SYNC'

    def motion_tick(self, now):
        cart = self.config['controller_name']
        if self.monitor is not None and self.monitor.done():
            self.controllers(self.monitor.result(), cart)
            self.checked, self.monitor = now, None
        if self.checked is None or now-self.checked > .5:
            raise ValueError('复位控制器核对超时')
        if now >= self.next_check and self.monitor is None:
            self.monitor = self.clients['list_controllers'].call_async(ListControllers.Request())
            self.next_check = now+.2
        actual = np.asarray(self.owner.fresh_sample('eef').value)
        if self.phase == 'SYNC':
            self.publish(actual)
            if now < self.sync_until:
                return
            self.phase = 'MOTION'
        if self.motion is None:
            if not self.waypoints:
                self.switch(back=True)
                return
            self.stage_name, self.motion_goal = self.waypoints.pop(0)
            if self.stage_index == 0:
                # Retraction keeps the actual post-switch XY and orientation exactly.
                self.motion_goal = np.r_[actual[:2], max(actual[2], self.motion_goal[2]), actual[3:]]
            self.stage_index += 1
            self.motion = self.trajectory(actual, self.motion_goal)
            self.motion_started, self.settled = now, None
            self.event('stage_started', stage=self.stage_name, actual=actual.tolist(),
                       target=self.motion_goal.tolist(), planned_duration_s=self.motion.duration)
        position, orientation = self.motion.sample(now-self.motion_started)
        candidate = np.r_[position, orientation]
        c = self.config
        position_error = float(np.linalg.norm(self.motion_goal[:3]-actual[:3]))
        angle_error = quaternion_angle(self.motion_goal[3:], actual[3:])
        elapsed = now-self.motion_started
        self.diagnostics = dict(stage=self.stage_name, actual=actual.tolist(),
            target=self.motion_goal.tolist(), command=candidate.tolist(), elapsed_s=elapsed,
            planned_duration_s=self.motion.duration, position_error_m=position_error,
            orientation_error_rad=angle_error)
        waiting = '，等待实际到位' if elapsed >= self.motion.duration else ''
        self.message = (f'{self.stage_name}：距本段目标 {position_error*1000:.1f} mm / '
                        f'{np.rad2deg(angle_error):.1f}°{waiting}')
        if now >= self.next_progress:
            self.event('progress', **self.diagnostics)
            self.next_progress = now+1.
        if (np.linalg.norm(candidate[:3]-actual[:3]) > c['tracking_position_error_m'] or
                quaternion_angle(candidate[3:], actual[3:]) > c['tracking_orientation_error_rad']):
            raise ValueError('复位跟踪误差超限')
        self.publish(candidate)
        reached = (now-self.motion_started >= self.motion.duration and
            position_error <= c['position_tolerance_m'] and
            angle_error <= c['orientation_tolerance_rad'])
        if not reached:
            self.settled = None
        elif self.settled is None:
            self.settled = now
        elif now-self.settled >= c['settle_s']:
            self.event('stage_reached', **self.diagnostics)
            self.motion = None

    def tick(self):
        with self.lock:
            self._tick()

    def _tick(self):
        if not self.busy:
            return
        now = time.monotonic()
        try:
            if not self.stopping:
                if now-self.owner.gui_received > .4 or now-self.last_tick > .15:
                    raise ValueError('界面/复位控制周期超时')
                if now-self.started > self.config['timeout_s']:
                    raise ValueError('复位超时')
                self.owner.safety_check(require_drag=False)
                wrench = self.owner.fresh_sample('robot').value['wrench']
                if (np.linalg.norm(wrench[:3]) > self.config['max_force_n'] or
                        np.linalg.norm(wrench[3:]) > self.config['max_torque_nm']):
                    raise ValueError('复位力/力矩超限')
                if self.owner.count_publishers(self.config['command_topic']) != 1:
                    raise ValueError('复位期间出现其他目标发布者')
            self.last_tick = now
            if self.stopping:
                try:
                    self.publish(self.owner.fresh_sample('eef').value)
                except ValueError:
                    pass  # No stale TF is republished; switch back still attempts to hold measured joints.
            if self.pending is not None:
                kind, future, started = self.pending
                if not future.done():
                    if now-started > 4.:
                        self.cancel('控制器操作未确认，保持停止并等待响应')
                    return  # Never discard an in-flight switch and later resume motion.
                self.pending = None
                self.response(kind, future.result(), now)
                return
            if self.stopping:
                self.call('STOP_CHECK', 'list_controllers', ListControllers.Request())
            else:
                if self.phase not in ('SYNC', 'MOTION'):
                    raise ValueError('复位控制器尚未确认，禁止发送运动目标')
                self.motion_tick(now)
        except Exception as exc:
            if self.stopping:
                self.stop_reason += '；恢复未确认：'+str(exc)
                self.finish()
            else:
                self.cancel(str(exc))
