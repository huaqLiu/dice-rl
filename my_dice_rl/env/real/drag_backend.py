"""拖拽示教 ROS 后端：只请求拖拽/保持，实际位姿同时作为动作标签。"""
from concurrent.futures import Future
import json
import time
from types import SimpleNamespace

import numpy as np
from std_msgs.msg import Empty, String
from std_srvs.srv import SetBool

from my_dice_rl.env.real.ros_backend import CollectorNode


class DragCollectorNode(CollectorNode):
    def __init__(self, config, buffers):
        super().__init__(config, buffers, node_name='dice_drag_collector')
        self.drag = config['drag']
        self.controller_state = None
        self.state_received = 0.
        self.gui_received = time.monotonic()
        self.desired = False
        self.enable_deadline = 0.
        self.drag_confirmed = False
        self.pending = None
        self.registered_width = None
        self.origin = None
        self.recording_locked = False
        self.pause_reason = ''
        self.reset_controller = None
        if config.get('reset') is not None:
            from my_dice_rl.env.real.drag_reset import DragReset
            self.reset_controller = DragReset(self, config['reset'])
        self.heartbeat = self.create_publisher(Empty, self.drag['heartbeat_topic'], 1)
        self.create_subscription(String, self.drag['state_topic'], self.drag_state, 1)
        self.create_timer(.05, self.tick_drag)

    # Ignore stale SpaceMouse/DP targets and telemetry. No pose command publisher exists here.
    def target(self, message):
        pass

    def teleop(self, message):
        pass

    def eef(self):
        super().eef()
        sample = self.buffers.latest('eef')
        # Preserve original TF timestamp and receive time, never restamp stale observations.
        self.buffers.put('target', sample.value, sample.source, sample.received)

    def drag_state(self, message):
        try:
            state = json.loads(message.data)
            age = self.get_clock().now().nanoseconds*1e-9 - state['stamp']
            if not -.1 <= age <= self.config['max_age_s']:
                raise ValueError('拖拽控制器状态时间戳异常')
            if type(state['active']) is not bool or state['mode'] not in (0, 1, 2, 3):
                raise ValueError('拖拽控制器状态无效')
            self.controller_state, self.state_received = state, time.monotonic()
        except Exception as exc:
            self.controller_state = None
            self.pause_reason = str(exc)

    def gui_tick(self):
        self.gui_received = time.monotonic()

    def fresh_sample(self, key):
        sample = self.buffers.latest(key)
        if sample is None or time.monotonic()-sample.received > self.config['max_age_s']:
            raise ValueError(key + '：数据超时')
        with self.buffers.lock:
            if key in self.buffers.errors:
                raise ValueError(key + '：' + self.buffers.errors[key])
        return sample

    def safety_check(self, require_drag=True):
        if self.controller_state is None or time.monotonic()-self.state_received > self.config['max_age_s']:
            raise ValueError('拖拽控制器状态超时')
        if require_drag and not self.controller_state['active']:
            raise ValueError('请先激活 panda_drag_controller')
        pose = np.asarray(self.fresh_sample('eef').value)
        if pose.shape != (7,) or not np.isfinite(pose).all() or abs(np.linalg.norm(pose[3:])-1.) > .02:
            raise ValueError('实际TCP位姿无效')
        robot = self.fresh_sample('robot').value
        self.fresh_sample('gripper')
        if robot.get('unsafe', False):
            raise ValueError('机器人错误或碰撞保护已触发')
        if np.max(np.abs(robot['dq'])) > self.drag['max_joint_velocity']:
            raise ValueError('拖拽关节速度超限')
        if np.linalg.norm(robot['wrench'][:3]) > self.drag['max_force_n']:
            raise ValueError('拖拽外力超限')
        if self.origin is not None and np.linalg.norm(pose[:3]-self.origin) > self.drag['max_displacement_m']:
            raise ValueError('超出本次启用拖拽的位移范围')

    def robot(self, message):
        super().robot(message)
        sample = self.buffers.latest('robot')
        errors = any(getattr(message.current_errors, name)
                     for name in message.current_errors.get_fields_and_field_types())
        value = dict(sample.value, unsafe=(int(message.robot_mode) not in (1, 2)
            or errors or any(message.cartesian_collision) or any(message.joint_collision)))
        self.buffers.put('robot', value, sample.source, sample.received)

    @staticmethod
    def completed(success, message):
        future = Future()
        future.set_result(SimpleNamespace(success=success, message=message))
        return future

    def request_control(self, action):
        if action in ('reset', 'save_reset'):
            try:
                if self.reset_controller is None:
                    raise ValueError('尚未配置拖拽复位')
                message = (self.reset_controller.start() if action == 'reset'
                           else self.reset_controller.save_reference())
                self.control_message = message
                return self.completed(True, message)
            except Exception as exc:
                self.control_message = str(exc)
                return self.completed(False, str(exc))
        if self.reset_controller is not None and self.reset_controller.busy:
            if action == 'pause':
                self.reset_controller.cancel('复位已由操作员停止')
                return self.completed(True, self.reset_controller.message)
            return self.completed(False, '正在复位，请先按2停止')
        if action == 'hold':
            try:
                if self.desired or self.recording_locked or (self.controller_state or {}).get('mode') == 1:
                    raise ValueError('先暂停拖拽并结束录制，再登记已有夹持')
                self.registered_width = float(self.fresh_sample('gripper').value)
                if not 0 <= self.registered_width <= .08:
                    self.registered_width = None
                    raise ValueError('夹爪开度超出0–80毫米，无法登记')
                self.control_message = '已登记现有夹持；未向夹爪发送指令'
                return self.completed(True, self.control_message)
            except Exception as exc:
                self.control_message = str(exc)
                return self.completed(False, str(exc))
        if action not in ('enable', 'pause'):
            self.control_message = '拖拽模式请用手复位，不执行自动返回'
            return self.completed(False, self.control_message)
        enabling = action == 'enable'
        if enabling and self.desired:
            return self.completed(True, '拖拽已请求启用，等待/保持控制器确认')
        if enabling:
            try:
                self.origin = None
                self.safety_check()
                if self.registered_width is None:
                    raise ValueError('请先按3登记已有夹持')
                self.origin = np.asarray(self.fresh_sample('eef').value[:3]).copy()
            except Exception as exc:
                self.control_message = str(exc)
                return self.completed(False, str(exc))
        self.desired = enabling
        if enabling:
            self.pause_reason = ''
            self.enable_deadline = time.monotonic()+.6
            self.drag_confirmed = False
        if not self.enable_client.service_is_ready():
            self.desired = False
            self.control_message = '拖拽服务未就绪；停止心跳后控制器自动保持'
            return self.completed(False, self.control_message)
        future = self.enable_client.call_async(SetBool.Request(data=enabling))
        self.pending = future
        def done(result):
            try:
                response = result.result()
                if not response.success:
                    self.desired = False
                self.control_message = (('已请求启用拖拽' if enabling else '已请求当前位置保持')
                    if response.success else response.message)
            except Exception as exc:
                self.desired = False
                self.control_message = str(exc)
        future.add_done_callback(done)
        return future

    def request_recording_lock(self, locked):
        try:
            if locked:
                if self.reset_controller is not None and self.reset_controller.busy:
                    raise ValueError('复位期间禁止录制')
                self.safety_check()
                if not self.desired or self.controller_state['mode'] != 1:
                    raise ValueError('拖拽尚未启用')
            self.recording_locked = locked
            return self.completed(True, '录制互锁已更新')
        except Exception as exc:
            return self.completed(False, str(exc))

    def tick_drag(self):
        now = time.monotonic()
        gui_alive = now-self.gui_received < .4
        if self.desired:
            try:
                if not gui_alive:
                    raise ValueError('采集界面心跳中断')
                self.safety_check()
                if self.controller_state['mode'] == 1:
                    self.drag_confirmed = True
                if self.controller_state['mode'] in (2, 3) and (self.drag_confirmed or now > self.enable_deadline):
                    raise ValueError('控制器心跳/速度保护；需重新按1启用')
                if now > self.enable_deadline and self.controller_state['mode'] != 1:
                    raise ValueError('控制器未进入拖拽模式')
            except Exception as exc:
                self.pause_reason = str(exc)
                self.request_control('pause')
        # Heartbeats maintain an armed session; they never request enable.
        resetting = self.reset_controller is not None and self.reset_controller.busy
        if gui_alive and (self.desired or resetting or (self.controller_state or {}).get('mode') != 1):
            self.heartbeat.publish(Empty())
        state = self.controller_state or {}
        fresh = now-self.state_received <= self.config['max_age_s']
        enabled = bool(self.desired and fresh and state.get('active') and state.get('mode') == 1)
        grip = self.buffers.latest('gripper')
        robot = self.buffers.latest('robot')
        if self.registered_width is not None and grip is not None and abs(grip.value-self.registered_width) > .003:
            self.pause_reason = '夹爪开度改变，请暂停检查夹持并重新登记'
            self.request_control('pause')
            self.registered_width = None
            enabled = False
        telemetry = dict(base_frame=self.config['base_frame'], ee_frame=self.config['ee_frame'],
            enabled=enabled, resetting=resetting, safety_latched=bool(self.pause_reason),
            reset_message=self.reset_controller.message if resetting else '',
            reset_goal=self.reset_controller.goal.tolist() if self.reset_controller is not None and
                self.reset_controller.goal is not None else None,
            pause_reason=self.pause_reason or ('' if enabled else '拖拽未启用'),
            gripper_known=self.registered_width is not None,
            gripper_target_width=self.registered_width or 0., gripper_busy=False,
            gripper_error='', gripper_command_mode='hold_existing', gripper_speed=0., gripper_force=0.,
            collection_mode='kinesthetic', target_semantics='measured_eef_pose',
            force=list(robot.value['wrench'][:3]) if robot is not None else [0., 0., 0.],
            controller_mode=state.get('mode'), stamp=self.get_clock().now().nanoseconds*1e-9)
        self.buffers.put('teleop', telemetry, telemetry['stamp'], now)
