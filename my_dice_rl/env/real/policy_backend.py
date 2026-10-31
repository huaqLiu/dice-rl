"""ROS I/O and exclusive control handover. No neural inference runs in callbacks."""
import time
import uuid

import numpy as np
from action_msgs.msg import GoalStatus
from controller_manager_msgs.srv import ListControllers
from franka_msgs.action import Grasp, Move
from geometry_msgs.msg import PoseStamped
from rcl_interfaces.srv import GetParameters
from rclpy.action import ActionClient
from std_srvs.srv import SetBool

from my_dice_rl.env.real.ros_backend import CollectorNode
from my_dice_rl.util.recording import policy_image


class PolicyROSNode(CollectorNode):
    def __init__(self, collector, execution, buffers):
        super().__init__({**collector, 'max_age_s': execution['max_age_s']}, buffers,
                         'dice_dp_executor_'+uuid.uuid4().hex[:8])
        self.execution = execution
        self.controller_command_interface = execution['controller_command_interface']
        if self.controller_command_interface not in {'effort', 'position'}:
            raise ValueError('controller_command_interface must be effort or position')
        self.publisher = None
        self.has_lock, self.command_error = False, ''
        self.controller_active, self.controller_checked = False, 0.
        self.controller_frames, self.frames_checked = None, 0.
        self.controller_future, self.params_future = None, None
        self.controllers = self.create_client(ListControllers, execution['controller_service'])
        self.parameters = self.create_client(GetParameters,
            '/'+execution['controller_name'].strip('/')+'/get_parameters')
        self.policy_lock = self.create_client(SetBool, execution['teleop_lock_service'])
        namespace = collector['gripper_topic'].rsplit('/', 1)[0]
        self.move_client = ActionClient(self, Move, namespace+'/move')
        self.grasp_client = ActionClient(self, Grasp, namespace+'/grasp')
        self.gripper_busy, self.gripper_handle, self.cancel_requested = False, None, False
        self.gripper_target, self.gripper_mode = 0., 0
        self.gripper_requested_at, self.last_gripper_time = 0., 0.
        self.create_timer(.5, self.check_controller)

    def image(self, message, key):
        # Same 224x224 center crop as dataset conversion and DP training.
        from my_dice_rl.env.real.ros_backend import decode_image
        source = self.fresh(message.header.stamp)
        received = time.monotonic()
        self.buffers.put(key, policy_image(decode_image(message)), source, received)

    def robot(self, message):
        source = self.fresh(message.header.stamp)
        q, dq, wrench = np.asarray(message.q), np.asarray(message.dq), np.asarray(message.o_f_ext_hat_k)
        if q.shape != (7,) or dq.shape != (7,) or wrench.shape != (6,) or not np.isfinite(np.r_[q, dq, wrench]).all():
            raise ValueError('机器人状态无效')
        errors = any(getattr(message.current_errors, name)
                     for name in message.current_errors.get_fields_and_field_types())
        collision = any(message.cartesian_collision) or any(message.joint_collision)
        self.buffers.put('robot', dict(q=q, dq=dq, wrench=wrench,
            mode=int(message.robot_mode), unsafe=bool(errors or collision)), source)

    def check_controller(self):
        if self.controller_future is None and self.controllers.service_is_ready():
            self.controller_future = self.controllers.call_async(ListControllers.Request())
            self.controller_future.add_done_callback(self._controllers_done)
        if self.params_future is None and self.parameters.service_is_ready():
            self.params_future = self.parameters.call_async(GetParameters.Request(names=['base_link', 'ee_link']))
            self.params_future.add_done_callback(self._parameters_done)

    def _controllers_done(self, future):
        self.controller_future = None
        try:
            self.controller_active = any(c.name == self.execution['controller_name'] and c.state == 'active'
                                         and set(c.claimed_interfaces) == {
                                             f'panda_joint{i}/{self.controller_command_interface}' for i in range(1, 8)}
                                         for c in future.result().controller)
            self.controller_checked = time.monotonic()
        except Exception:
            self.controller_active = False

    def _parameters_done(self, future):
        self.params_future = None
        try:
            self.controller_frames = tuple(v.string_value for v in future.result().values)
            self.frames_checked = time.monotonic()
        except Exception:
            self.controller_frames = None

    def _other_publishers(self):
        own_name = self.get_fully_qualified_name()
        return [info.node_namespace.rstrip('/')+'/'+info.node_name
                for info in self.get_publishers_info_by_topic(self.config['target_topic'])
                if info.node_namespace.rstrip('/')+'/'+info.node_name != own_name]

    def check_ready(self, require_lock=True):
        now = time.monotonic()
        if not self.controller_active or now-self.controller_checked > 2.5:
            raise ValueError(f'控制器未 active、未占用七个 {self.controller_command_interface} 接口或状态过旧')
        expected = (self.config['base_frame'], self.config['ee_frame'])
        if self.controller_frames != expected or now-self.frames_checked > 2.5:
            raise ValueError('控制器 base_link/ee_link 与模型坐标定义未核对通过')
        others = self._other_publishers()
        if any(name != self.execution['teleop_node'] for name in others) or len(others) > 1:
            raise ValueError('末端目标话题存在其他策略/未知发布者')
        if others and (not self.policy_lock.service_is_ready() or require_lock and not self.has_lock):
            raise ValueError('SpaceMouse 未完成控制权交接；需使用支持 policy_lock 的新遥操作节点')
        if self.has_lock:
            telemetry = self.buffers.latest('teleop')
            if (telemetry is None or now-telemetry.received > self.execution['max_age_s']
                    or 'teleop' in self.buffers.errors
                    or not telemetry.value.get('policy_locked') or telemetry.value.get('enabled')):
                raise ValueError('SpaceMouse 控制锁状态丢失')
        if not self.move_client.server_is_ready() or not self.grasp_client.server_is_ready():
            raise ValueError('Franka Hand move/grasp 服务未就绪')
        if self.command_error:
            raise ValueError(self.command_error)
        if self.gripper_busy and now-self.gripper_requested_at > self.execution['gripper_timeout_s']:
            self.command_error = '夹爪操作超时，等待取消确认'
            raise ValueError(self.command_error)

    def acquire(self):
        self.command_error = ''
        self.check_ready(require_lock=False)
        if self.gripper_busy:
            raise ValueError('等待夹爪空闲')
        return (self.policy_lock.call_async(SetBool.Request(data=True))
                if self.policy_lock.service_is_ready() else None)

    def acquired(self):
        self.has_lock = self.policy_lock.service_is_ready()

    def activate(self):
        self.check_ready()
        if self.publisher is not None:
            raise ValueError('策略发布者已激活')
        self.gripper_target, self.gripper_mode = 0., 0  # Human confirmed an existing grasp.
        self.publisher = self.create_publisher(PoseStamped, self.config['target_topic'], 1)

    def publish(self, pose):
        if self.publisher is None:
            raise RuntimeError('策略尚未取得控制权')
        if any(name != self.execution['teleop_node'] for name in self._other_publishers()):
            raise RuntimeError('检测到其他控制者，停止发布策略目标')
        message = PoseStamped()
        message.header.stamp = self.get_clock().now().to_msg()
        message.header.frame_id = self.config['base_frame']
        p, q = message.pose.position, message.pose.orientation
        p.x, p.y, p.z = map(float, pose[:3])
        q.x, q.y, q.z, q.w = map(float, pose[3:])
        self.publisher.publish(message)

    def send_gripper(self, target):
        now = time.monotonic()
        target = 0. if target <= self.execution['grasp_threshold'] else float(target)
        if self.gripper_busy or abs(target-self.gripper_target) < self.execution['gripper_width_change']:
            return
        if now-self.last_gripper_time < self.execution['gripper_min_interval_s']:
            return
        client = self.grasp_client if target == 0 else self.move_client
        if not client.server_is_ready():
            raise ValueError('夹爪 action server 断开')
        if target == 0:
            goal = Grasp.Goal(width=0., speed=self.execution['gripper_speed'], force=self.execution['gripper_force'])
            goal.epsilon.inner, goal.epsilon.outer = .005, .08
        else:
            goal = Move.Goal(width=target, speed=self.execution['gripper_speed'])
        self.gripper_busy, self.cancel_requested = True, False
        self.gripper_requested_at = self.last_gripper_time = now
        try:
            future = client.send_goal_async(goal)
        except Exception as exc:
            self.gripper_busy, self.command_error = False, str(exc)
            raise
        future.add_done_callback(lambda f: self._gripper_accepted(f, target))

    def _gripper_accepted(self, future, target):
        try:
            handle = future.result()
            if not handle.accepted:
                raise ValueError('夹爪指令被拒绝')
            self.gripper_handle = handle
            self.gripper_target, self.gripper_mode = target, 2 if target == 0 else 1
            handle.get_result_async().add_done_callback(self._gripper_result)
            if self.cancel_requested:
                handle.cancel_goal_async()
        except Exception as exc:
            self.command_error, self.gripper_busy = str(exc), False

    def _gripper_result(self, future):
        try:
            response = future.result()
            if not self.cancel_requested and (response.status != GoalStatus.STATUS_SUCCEEDED or not response.result.success):
                self.command_error = response.result.error or '夹爪动作失败'
        except Exception as exc:
            self.command_error = str(exc)
        finally:
            self.gripper_busy, self.gripper_handle = False, None

    def cancel(self):
        self.cancel_requested = True
        if self.gripper_handle is not None:
            self.gripper_handle.cancel_goal_async()

    def deactivate(self):
        if self.publisher is not None:
            self.destroy_publisher(self.publisher)
            self.publisher = None

    def release(self):
        if self.gripper_busy:
            raise ValueError('夹爪尚未确认停止，不能交回人工控制')
        self.deactivate()
        if self.has_lock and self.policy_lock.service_is_ready():
            self.policy_lock.call_async(SetBool.Request(data=False))
        self.has_lock = False

