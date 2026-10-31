#!/usr/bin/env python3
import json
import threading
import time
from typing import Optional, Tuple

import numpy as np
import rclpy
from action_msgs.msg import GoalStatus
from controller_manager_msgs.srv import ListControllers
from geometry_msgs.msg import PoseStamped, TwistStamped
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.time import Time
from sensor_msgs.msg import JointState, Joy
from std_srvs.srv import SetBool
from std_srvs.srv import Trigger
from std_msgs.msg import String
from tf2_ros import Buffer, TransformException, TransformListener

from franka_msgs.msg import FrankaRobotState
from franka_msgs.action import Grasp, Move
from my_dice_rl.util.config import project_path
from my_dice_rl.util.reset_motion import ResetTrajectory, load_reset_pose

from my_dice_rl.util.spacemouse_math import (
    apply_deadband,
    clamp_vector_norm,
    quaternion_multiply,
    rotate_vector,
    rotvec_to_quaternion,
    quaternion_inverse,
    quaternion_to_rotvec,
)


class FrankaSpaceMouseTeleop(Node):
    """Convert SpaceMouse input into Cartesian pose targets for the configured controller."""

    def __init__(self) -> None:
        super().__init__("dice_spacemouse_teleop")

        self.declare_parameter("joy_topic", "/dice_spacemouse/joy")
        self.declare_parameter("target_topic", "/cartesian_impedance_target")
        self.declare_parameter("controller_name", "panda_cartesian_impedance_controller")
        self.declare_parameter("controller_command_interface", "effort")
        self.declare_parameter("controller_service", "/controller_manager/list_controllers")
        self.declare_parameter(
            "robot_state_topic", "/franka_robot_state_broadcaster/robot_state"
        )
        self.declare_parameter("base_frame", "panda_link0")
        self.declare_parameter("ee_frame", "panda_hand_tcp")
        self.declare_parameter("command_frame", "base")
        self.declare_parameter("control_rate", 50.0)
        self.declare_parameter("max_linear_speed", 0.025)
        self.declare_parameter("max_angular_speed", 0.15)
        self.declare_parameter("deadband", 0.03)
        self.declare_parameter("response_exponent", 1.0)
        self.declare_parameter("axis_indices", [0, 1, 2, 3, 4, 5])
        self.declare_parameter("axis_signs", [1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
        self.declare_parameter("enable_button", 0)
        self.declare_parameter("gripper_button", 1)
        self.declare_parameter("gripper_namespace", "/panda_gripper")
        self.declare_parameter("gripper_joint_names", ["panda_finger_joint1", "panda_finger_joint2"])
        self.declare_parameter("gripper_open_width", 0.08)
        self.declare_parameter("gripper_closed_width", 0.0)
        self.declare_parameter("gripper_speed", 0.03)
        self.declare_parameter("gripper_force", 20.0)
        self.declare_parameter("gripper_state_timeout", 1.0)
        self.declare_parameter("joy_timeout", 0.25)
        self.declare_parameter("enable_wrench_stop", True)
        self.declare_parameter("robot_state_timeout", 0.25)
        self.declare_parameter("max_force", 15.0)
        self.declare_parameter("max_torque", 3.0)
        self.declare_parameter("max_position_error", 0.025)
        self.declare_parameter("max_orientation_error", 0.25)
        self.declare_parameter("enabled_on_startup", False)
        self.declare_parameter('reset_reference_file', 'cfg/rl_review.yaml')
        reset_defaults = dict(linear_speed=.02, angular_speed=.10, linear_acceleration=.05,
            angular_acceleration=.30, max_distance=.15, max_angle=.60, position_tolerance=.003,
            orientation_tolerance=.03, tracking_position_error=.025,
            tracking_orientation_error=.25, timeout=30.)
        self.reset_limits = {}
        for key, default in reset_defaults.items():
            self.declare_parameter('reset_' + key, default)
            self.reset_limits[key] = float(self.get_parameter('reset_' + key).value)
        self.reset_reference_file = project_path(self.get_parameter('reset_reference_file').value)

        self.joy_topic = str(self.get_parameter("joy_topic").value)
        self.target_topic = str(self.get_parameter("target_topic").value)
        self.controller_name = str(self.get_parameter("controller_name").value)
        self.controller_command_interface = str(self.get_parameter("controller_command_interface").value)
        self.robot_state_topic = str(self.get_parameter("robot_state_topic").value)
        self.base_frame = str(self.get_parameter("base_frame").value)
        self.ee_frame = str(self.get_parameter("ee_frame").value)
        self.command_frame = str(self.get_parameter("command_frame").value).lower()
        self.control_rate = float(self.get_parameter("control_rate").value)
        self.max_linear_speed = float(self.get_parameter("max_linear_speed").value)
        self.max_angular_speed = float(self.get_parameter("max_angular_speed").value)
        self.deadband = float(self.get_parameter("deadband").value)
        self.response_exponent = float(self.get_parameter("response_exponent").value)
        self.axis_indices = [int(x) for x in self.get_parameter("axis_indices").value]
        self.axis_signs = np.asarray(
            self.get_parameter("axis_signs").value, dtype=np.float64
        )
        self.enable_button = int(self.get_parameter("enable_button").value)
        self.gripper_button = int(self.get_parameter("gripper_button").value)
        self.max_position_error = float(self.get_parameter("max_position_error").value)
        self.max_orientation_error = float(self.get_parameter("max_orientation_error").value)
        self.gripper_namespace = str(self.get_parameter("gripper_namespace").value).rstrip("/")
        self.gripper_joint_names = list(self.get_parameter("gripper_joint_names").value)
        self.gripper_open_width = float(self.get_parameter("gripper_open_width").value)
        self.gripper_closed_width = float(self.get_parameter("gripper_closed_width").value)
        self.gripper_speed = float(self.get_parameter("gripper_speed").value)
        self.gripper_force = float(self.get_parameter("gripper_force").value)
        self.gripper_state_timeout = float(self.get_parameter("gripper_state_timeout").value)
        self.joy_timeout = float(self.get_parameter("joy_timeout").value)
        self.enable_wrench_stop = bool(
            self.get_parameter("enable_wrench_stop").value
        )
        self.robot_state_timeout = float(
            self.get_parameter("robot_state_timeout").value
        )
        self.max_force = float(self.get_parameter("max_force").value)
        self.max_torque = float(self.get_parameter("max_torque").value)
        self.enabled = bool(self.get_parameter("enabled_on_startup").value)

        self._validate_parameters()

        self._lock = threading.Lock()
        self._axes = np.zeros(6, dtype=np.float64)
        self._buttons = []
        self._last_joy_time: Optional[Time] = None
        self._last_robot_state_time: Optional[Time] = None
        self._reset_robot_ok = False
        self._last_update_time = self.get_clock().now()
        self._was_commanding = False
        self._gripper_busy = False
        self._recording_gripper_width = None
        self._recording_gripper_mode = "unknown"
        self._recording_gripper_error = ""
        self._gripper_closed: Optional[bool] = None
        self._gripper_width: Optional[float] = None
        self._last_gripper_state_time: Optional[Time] = None
        self._target_position: Optional[np.ndarray] = None
        self._target_orientation: Optional[np.ndarray] = None
        self._latest_force = np.zeros(3, dtype=np.float64)
        self._latest_torque = np.zeros(3, dtype=np.float64)
        self._safety_latched = False
        self._pause_reason = ""
        self._hold_requested = False
        self._policy_locked = False
        self._recording_locked = False
        self._reset_plan = None
        self._reset_message = ''
        self._reset_settled_since = None
        self._controller_active = False
        self._controller_checked = 0.0
        self._controller_future = None
        self.controller_client = self.create_client(
            ListControllers, str(self.get_parameter("controller_service").value))
        self.controller_timer = self.create_timer(0.5, self._check_controller)

        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)

        self.target_publisher = self.create_publisher(PoseStamped, self.target_topic, 10)
        self.recording_publisher = self.create_publisher(String, "~/recording_state", 10)
        self.recording_timer = self.create_timer(1.0 / self.control_rate, self._publish_recording_state)
        self.twist_publisher = self.create_publisher(
            TwistStamped, "~/command_twist", 10
        )
        self.joy_subscription = self.create_subscription(
            Joy, self.joy_topic, self._joy_callback, 20
        )
        self.robot_state_subscription = self.create_subscription(
            FrankaRobotState, self.robot_state_topic, self._robot_state_callback, 20
        )
        self.enable_service = self.create_service(
            SetBool, "~/enable", self._enable_callback
        )
        self.hold_service = self.create_service(Trigger, "~/register_hold", self._register_hold)
        self.policy_lock_service = self.create_service(SetBool, "~/policy_lock", self._policy_lock)
        self.reset_service = self.create_service(Trigger, '~/reset', self._start_reset)
        self.recording_lock_service = self.create_service(SetBool, '~/recording_lock', self._recording_lock)
        self.gripper_move_client = ActionClient(self, Move, f"{self.gripper_namespace}/move")
        self.gripper_grasp_client = ActionClient(self, Grasp, f"{self.gripper_namespace}/grasp")
        self.gripper_state_subscription = self.create_subscription(
            JointState, f"{self.gripper_namespace}/joint_states", self._gripper_state_callback, 10
        )
        self.timer = self.create_timer(1.0 / self.control_rate, self._control_step)

        initial_state = "ENABLED" if self.enabled else "DISABLED"
        self.get_logger().info(
            f"SpaceMouse teleop {initial_state}. Button {self.enable_button}: enable/pause; "
            f"button {self.gripper_button}: toggle gripper. GUI controls are also available."
        )
        self.get_logger().info(
            f"Command frame={self.command_frame}, linear={self.max_linear_speed:.3f} m/s, "
            f"angular={self.max_angular_speed:.3f} rad/s, target={self.target_topic}"
        )

    def _validate_parameters(self) -> None:
        if any(not np.isfinite(value) or value <= 0 for value in self.reset_limits.values()):
            raise ValueError('Reset limits must be finite and positive')
        if self.controller_command_interface not in {"effort", "position"}:
            raise ValueError("controller_command_interface must be 'effort' or 'position'")
        if self.command_frame not in {"base", "tool"}:
            raise ValueError("command_frame must be 'base' or 'tool'")
        if self.control_rate <= 0.0:
            raise ValueError("control_rate must be positive")
        if len(self.axis_indices) != 6 or len(self.axis_signs) != 6:
            raise ValueError("axis_indices and axis_signs must each contain 6 values")
        if sorted(self.axis_indices) != list(range(6)):
            raise ValueError("axis_indices must be a permutation of [0, 1, 2, 3, 4, 5]")
        if not 0.0 <= self.deadband < 1.0:
            raise ValueError("deadband must be in [0, 1)")
        if {self.enable_button, self.gripper_button} != {0, 1}:
            raise ValueError("enable_button and gripper_button must be 0 and 1")
        if min(self.max_position_error, self.max_orientation_error) <= 0:
            raise ValueError("Pose error limits must be positive")
        if not 0.0 <= self.gripper_closed_width < self.gripper_open_width <= 0.08:
            raise ValueError("Gripper widths must satisfy 0 <= closed < open <= 0.08 m")
        if not 0.0 < self.gripper_speed <= 0.2 or not 0.0 < self.gripper_force <= 70.0:
            raise ValueError("Gripper speed must be in (0, 0.2] m/s and force in (0, 70] N")
        if len(self.gripper_joint_names) != 2 or len(set(self.gripper_joint_names)) != 2:
            raise ValueError("gripper_joint_names must contain two distinct names")
        if min(self.joy_timeout, self.robot_state_timeout, self.gripper_state_timeout) <= 0:
            raise ValueError("State timeouts must be positive")

    def _joy_callback(self, message: Joy) -> None:
        if (len(message.axes) < 6 or not np.all(np.isfinite(message.axes))
                or len(message.buttons) <= max(self.enable_button, self.gripper_button)):
            self.get_logger().error(
                "Invalid SpaceMouse message: expected six finite axes and both buttons",
                throttle_duration_sec=2.0,
            )
            return
        mapped = np.array(
            [message.axes[index] for index in self.axis_indices], dtype=np.float64
        )
        mapped *= self.axis_signs
        mapped = np.array(
            [
                apply_deadband(value, self.deadband, self.response_exponent)
                for value in mapped
            ],
            dtype=np.float64,
        )
        now = self.get_clock().now()
        with self._lock:
            previous_buttons = self._buttons
            previous_time = self._last_joy_time
            self._axes = mapped
            self._buttons = list(message.buttons)
            self._last_joy_time = now
        # Seed button levels on startup/reconnect. A held button is not a click.
        if self._reset_plan is not None:
            if np.linalg.norm(mapped) > .01 or any(message.buttons):
                self._cancel_reset('SpaceMouse 输入：复位已停止')
            return
        if previous_time is None:
            return
        if (now - previous_time).nanoseconds * 1e-9 > self.joy_timeout:
            self._set_enabled(False)
            self.get_logger().warn("SpaceMouse reconnected; re-enable from GUI.")
            return
        if self._policy_locked:
            return
        if all(message.buttons[i] for i in (self.enable_button, self.gripper_button)):
            return  # A simultaneous two-button press is ambiguous.
        if (self._button_pressed(message.buttons, self.enable_button)
                and not self._button_pressed(previous_buttons, self.enable_button)):
            success, reason = self._set_enabled(not self.enabled)
            self.get_logger().info(reason) if success else self.get_logger().warn(reason)
        if (self._button_pressed(message.buttons, self.gripper_button)
                and not self._button_pressed(previous_buttons, self.gripper_button)):
            self._toggle_gripper()

    def _robot_state_callback(self, message: FrankaRobotState) -> None:
        age = (self.get_clock().now().nanoseconds * 1e-9
               - message.header.stamp.sec - message.header.stamp.nanosec * 1e-9)
        if not -0.1 <= age <= self.robot_state_timeout:
            return
        force_torque = np.asarray(message.o_f_ext_hat_k, dtype=np.float64)
        if force_torque.size >= 6 and np.all(np.isfinite(force_torque[:6])):
            self._latest_force = force_torque[:3]
            self._latest_torque = force_torque[3:6]
            errors = any(getattr(message.current_errors, name)
                         for name in message.current_errors.get_fields_and_field_types())
            self._reset_robot_ok = (message.robot_mode in (1, 2) and not errors
                and not any(message.cartesian_collision) and not any(message.joint_collision))
            self._last_robot_state_time = self.get_clock().now()

    def _enable_callback(
        self, request: SetBool.Request, response: SetBool.Response
    ) -> SetBool.Response:
        response.success, response.message = self._set_enabled(bool(request.data))
        self.get_logger().info(response.message)
        return response

    def _check_controller(self) -> None:
        if self._controller_future is None and self.controller_client.service_is_ready():
            self._controller_future = self.controller_client.call_async(ListControllers.Request())
            self._controller_future.add_done_callback(self._controller_done)

    def _controller_done(self, future) -> None:
        self._controller_future = None
        try:
            expected = {f"panda_joint{i}/{self.controller_command_interface}" for i in range(1, 8)}
            self._controller_active = any(
                c.name == self.controller_name and c.state == "active"
                and set(c.claimed_interfaces) == expected
                for c in future.result().controller)
            self._controller_checked = time.monotonic()
        except Exception:
            self._controller_active = False

    def _controller_ready(self) -> bool:
        return self._controller_active and time.monotonic() - self._controller_checked < 2.0

    def _set_enabled(self, enabled: bool) -> Tuple[bool, str]:
        if self._reset_plan is not None:
            if enabled:
                return False, '正在复位；先按暂停或移动 SpaceMouse 停止复位'
            self._cancel_reset('复位已由操作员停止')
        if enabled and self._policy_locked:
            return False, "Policy owns the robot; finish the policy episode before manual reset."
        if enabled:
            if not self._controller_ready():
                return False, f"等待 {self.controller_name} active，并占用七个 {self.controller_command_interface} 接口。"
            if self.count_publishers(self.target_topic) > 1:
                return False, "Another target publisher is running; stop the other teleop/policy first."
            if not self._wrench_is_safe(self.get_clock().now()):
                return False, "Cannot reset: wait for fresh robot state and force below limits."
            if self._get_actual_pose() is None or self._last_joy_time is None:
                return False, "Waiting for fresh TF and SpaceMouse input."
            if (self.get_clock().now() - self._last_joy_time).nanoseconds * 1e-9 > self.joy_timeout:
                return False, "SpaceMouse input is stale."
            if np.linalg.norm(self._axes) > 0.01:
                return False, "Release SpaceMouse to neutral before enabling."
            self._safety_latched = False
            self._reset_message = ''
        self._hold_requested = self._hold_requested or (self.enabled and not enabled)
        self._pause_reason = ""
        self.enabled = enabled
        self._was_commanding = False
        self._target_position = None
        self._target_orientation = None
        return True, "teleoperation enabled" if enabled else "teleoperation disabled"

    def _policy_lock(self, request, response):
        if request.data and (self._reset_plan is not None or self._recording_locked):
            response.success, response.message = False, '正在复位或录制示范，无法交接策略控制权'
            return response
        if request.data and self._gripper_busy:
            response.success, response.message = False, 'Wait until the manual gripper action finishes.'
            return response
        if request.data:
            pose = self._get_actual_pose()
            if self.enabled and pose is None:
                response.success, response.message = False, 'Cannot hand over without fresh measured pose.'
                return response
            was_enabled = self.enabled
            self._set_enabled(False)
            if was_enabled and pose is not None:
                self._reset_target_to_actual(*pose)
                self._publish_target(self.get_clock().now())
            self._hold_requested = False
        self._policy_locked = bool(request.data)
        response.success = True
        response.message = 'Policy lock acquired.' if request.data else 'Manual reset available; arm stays paused.'
        return response

    def _recording_lock(self, request, response):
        if request.data and (self._policy_locked or self._reset_plan is not None):
            response.success, response.message = False, '策略执行或复位期间禁止录制示范'
        else:
            self._recording_locked = bool(request.data)
            response.success, response.message = True, '录制锁已更新'
        return response

    def _start_reset(self, request, response):
        now = self.get_clock().now()
        if self._recording_locked or self._policy_locked or self._reset_plan is not None:
            response.success, response.message = False, '请先结束录制/策略执行，或等待当前复位结束'
            return response
        if (not self._controller_ready() or self.count_publishers(self.target_topic) > 1
                or self._gripper_busy):
            response.success, response.message = False, '等待控制器就绪、唯一目标发布者且夹爪空闲'
            return response
        if (self._last_joy_time is None
                or (now - self._last_joy_time).nanoseconds * 1e-9 > self.joy_timeout
                or np.linalg.norm(self._axes) > .01 or any(self._buttons)):
            response.success, response.message = False, '请释放 SpaceMouse，等待新鲜中位输入'
            return response
        # Reset always requires fresh safe state, even if manual wrench checking was disabled.
        if not self._reset_state_safe(now):
            response.success, response.message = False, '机器人状态过旧或力/力矩超限，无法复位'
            return response
        actual = self._get_actual_pose()
        try:
            if actual is None:
                raise ValueError('等待新鲜实际 TCP 位姿')
            goal = load_reset_pose(self.reset_reference_file, self.base_frame, self.ee_frame)
            limits = self.reset_limits
            plan = ResetTrajectory(actual, goal, *(limits[key] for key in (
                'linear_speed', 'angular_speed', 'linear_acceleration', 'angular_acceleration',
                'max_distance', 'max_angle')))
            if plan.duration + 1. >= limits['timeout']:
                raise ValueError('预计复位时间超过配置上限，请先人工移近')
        except (ValueError, OSError, TypeError, KeyError) as exc:
            response.success, response.message = False, str(exc)
            return response
        self._set_enabled(False)
        self._hold_requested = False
        self._safety_latched = False
        self._reset_plan, self._reset_goal = plan, goal
        self._reset_started = now
        self._last_update_time = now
        self._reset_settled_since = None
        self._reset_message = '正在直线复位；2 / 0 / Esc 或 SpaceMouse 输入可停止'
        self._pause_reason = self._reset_message
        self._reset_target_to_actual(*actual)
        self._publish_target(now)
        response.success, response.message = True, self._reset_message
        return response

    def _reset_state_safe(self, now):
        return (self._last_robot_state_time is not None
                and self._reset_robot_ok
                and 0 <= (now - self._last_robot_state_time).nanoseconds * 1e-9 <= self.robot_state_timeout
                and np.linalg.norm(self._latest_force) <= self.max_force
                and np.linalg.norm(self._latest_torque) <= self.max_torque)

    def _cancel_reset(self, reason, fault=False, keep_goal=False):
        self._reset_plan = None
        self.enabled = False
        self._was_commanding = False
        self._hold_requested = False
        self._safety_latched = fault
        self._reset_message = self._pause_reason = reason
        if not keep_goal:
            actual = self._get_actual_pose()
            if actual is not None:
                self._reset_target_to_actual(*actual)
                self._publish_target(self.get_clock().now())
        self._publish_twist(self.get_clock().now(), np.zeros(3), np.zeros(3))

    def _reset_control_step(self, now, raw_dt, joy_fresh, axes):
        actual = self._get_actual_pose()
        reason = None
        if raw_dt < 0 or raw_dt > .15 or not joy_fresh or np.linalg.norm(axes) > .01:
            reason = '输入/控制周期异常，复位已停止'
        elif (not self._controller_ready() or self.count_publishers(self.target_topic) > 1
              or self._policy_locked or self._recording_locked):
            reason = '控制权或控制器异常，复位已停止'
        elif not self._reset_state_safe(now) or actual is None:
            reason = '状态/TF 超时或力/力矩超限，复位已停止'
        elapsed = (now - self._reset_started).nanoseconds * 1e-9
        if elapsed > self.reset_limits['timeout']:
            reason = '复位超时，请检查实际位置和受力'
        if reason:
            self._cancel_reset(reason, fault=True)
            return
        candidate = self._reset_plan.sample(elapsed)
        error = np.linalg.norm(candidate[0] - actual[0])
        angle = np.linalg.norm(quaternion_to_rotvec(quaternion_multiply(candidate[1], quaternion_inverse(actual[1]))))
        if (error > self.reset_limits['tracking_position_error']
                or angle > self.reset_limits['tracking_orientation_error']):
            self._cancel_reset(f'复位跟踪误差超限：{error*1000:.1f} mm / {np.degrees(angle):.1f} 度', fault=True)
            return
        self._target_position, self._target_orientation = candidate
        self._publish_target(now)
        reached = (elapsed >= self._reset_plan.duration
            and np.linalg.norm(self._reset_goal[0] - actual[0]) <= self.reset_limits['position_tolerance']
            and angle <= self.reset_limits['orientation_tolerance'])
        if not reached:
            self._reset_settled_since = None
        elif self._reset_settled_since is None:
            self._reset_settled_since = now
        elif (now - self._reset_settled_since).nanoseconds * 1e-9 >= .3:
            self._cancel_reset('复位完成，保持暂停；按 1 启用遥操作', keep_goal=True)

    def _gripper_state_callback(self, message: JointState) -> None:
        age = (self.get_clock().now().nanoseconds * 1e-9
               - message.header.stamp.sec - message.header.stamp.nanosec * 1e-9)
        if not -0.1 <= age <= self.gripper_state_timeout:
            return
        positions = dict(zip(message.name, message.position))
        if not all(name in positions for name in self.gripper_joint_names):
            return
        width = sum(positions[name] for name in self.gripper_joint_names)
        if not np.isfinite(width) or not 0.0 <= width <= 0.085:
            return
        self._gripper_width = width
        self._last_gripper_state_time = self.get_clock().now()

    def _register_hold(self, request, response):
        """Record an existing grasp without sending a movement/grasp command."""
        now = self.get_clock().now()
        if self._policy_locked:
            response.success, response.message = False, 'Policy owns the gripper.'
        elif (self._gripper_busy or self._last_gripper_state_time is None
                or (now - self._last_gripper_state_time).nanoseconds * 1e-9 > self.gripper_state_timeout):
            response.success, response.message = False, "等待新鲜夹爪状态且夹爪空闲"
        elif self._gripper_width >= self.gripper_open_width - 0.005:
            response.success, response.message = False, "夹爪接近全开，请先确认齿轮已夹持"
        else:
            self._recording_gripper_width = self._gripper_width
            self._recording_gripper_mode = "hold_existing"
            self._recording_gripper_error = ""
            self._gripper_closed = True
            response.success = True
            response.message = "已登记现有夹持；未发送夹爪指令"
        return response

    def _command_gripper(self, closing: bool) -> None:
        if self._policy_locked or self._reset_plan is not None:
            return
        # Gripper operation is independent of arm-motion enable.
        if self._gripper_busy:
            self.get_logger().info("Gripper is moving; additional click ignored.")
            return
        now = self.get_clock().now()
        if (self._last_gripper_state_time is None
                or (now - self._last_gripper_state_time).nanoseconds * 1e-9 > self.gripper_state_timeout):
            self.get_logger().warn(f"Waiting for gripper state on {self.gripper_namespace}/joint_states")
            return
        client = self.gripper_grasp_client if closing else self.gripper_move_client
        if not client.server_is_ready():
            self.get_logger().warn(f"Gripper action unavailable; start Franka with load_gripper:=true.")
            return
        if closing:
            goal = Grasp.Goal()
            goal.width = self.gripper_closed_width
            goal.force = self.gripper_force
            goal.epsilon.inner = 0.005
            # Accept contact with an object at any width within the configured opening.
            goal.epsilon.outer = self.gripper_open_width - self.gripper_closed_width
        else:
            goal = Move.Goal()
            goal.width = self.gripper_open_width
        goal.speed = self.gripper_speed
        self._gripper_busy = True
        try:
            future = client.send_goal_async(goal)
            future.add_done_callback(lambda result: self._gripper_goal_response(result, closing))
            self.get_logger().info("Gripper closing (grasp)." if closing else "Gripper opening.")
        except Exception as error:
            self._gripper_failed(str(error))

    def _toggle_gripper(self):
        closed = self._gripper_closed
        if closed is None:
            if self._gripper_width is None:
                self.get_logger().warn("Waiting for measured gripper width.")
                return
            closed = self._gripper_width < self.gripper_open_width - 0.005
        self._command_gripper(not closed)

    def _gripper_failed(self, reason: str) -> None:
        self._recording_gripper_width = None
        self._recording_gripper_mode = "unknown"
        self._recording_gripper_error = reason
        self._gripper_busy = False
        self._gripper_closed = None
        self.get_logger().error(f"Gripper command failed: {reason}. Click again to retry from measured state.")

    def _gripper_goal_response(self, future, closing: bool) -> None:
        try:
            handle = future.result()
            if not handle.accepted:
                self._gripper_failed("goal rejected")
                return
            self._recording_gripper_width = (
                self.gripper_closed_width if closing else self.gripper_open_width
            )
            self._recording_gripper_mode = "grasp" if closing else "move"
            self._recording_gripper_error = ""
            handle.get_result_async().add_done_callback(
                lambda result: self._gripper_result(result, closing)
            )
        except Exception as error:
            self._gripper_failed(str(error))

    def _gripper_result(self, future, closing: bool) -> None:
        try:
            response = future.result()
            if response.status != GoalStatus.STATUS_SUCCEEDED or not response.result.success:
                self._gripper_failed(response.result.error or f"action status {response.status}")
                return
            self._gripper_closed = closing
            self._gripper_busy = False
            self.get_logger().info("Gripper grasp completed." if closing else "Gripper open.")
        except Exception as error:
            self._gripper_failed(str(error))

    def _publish_recording_state(self) -> None:
        """Telemetry keeps accepted targets distinct from a registered existing hold."""
        message = String()
        message.data = json.dumps({
            "stamp": self.get_clock().now().nanoseconds * 1e-9,
            "enabled": self.enabled,
            "policy_locked": self._policy_locked,
            "resetting": self._reset_plan is not None,
            "reset_message": self._reset_message,
            "recording_locked": self._recording_locked,
            "safety_latched": self._safety_latched,
            "pause_reason": self._pause_reason,
            "base_frame": self.base_frame,
            "ee_frame": self.ee_frame,
            "gripper_known": self._recording_gripper_width is not None,
            "gripper_target_width": self._recording_gripper_width,
            "gripper_command_mode": self._recording_gripper_mode,
            "gripper_busy": self._gripper_busy,
            "gripper_error": self._recording_gripper_error,
            "gripper_speed": self.gripper_speed,
            "gripper_force": self.gripper_force,
            "force": self._latest_force.tolist(),
            "torque": self._latest_torque.tolist(),
            "axes": self._axes.tolist(),
        })
        self.recording_publisher.publish(message)

    def _get_actual_pose(self) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        try:
            transform = self.tf_buffer.lookup_transform(
                self.base_frame, self.ee_frame, Time()
            )
        except TransformException as error:
            self.get_logger().warn(
                f"Waiting for TF {self.base_frame} -> {self.ee_frame}: {error}",
                throttle_duration_sec=2.0,
            )
            return None

        translation = transform.transform.translation
        rotation = transform.transform.rotation
        age = (self.get_clock().now().nanoseconds * 1e-9
               - transform.header.stamp.sec - transform.header.stamp.nanosec * 1e-9)
        if not -0.1 <= age <= self.robot_state_timeout:
            return None
        position = np.array([translation.x, translation.y, translation.z], dtype=np.float64)
        orientation = np.array(
            [rotation.x, rotation.y, rotation.z, rotation.w], dtype=np.float64
        )
        if (not np.all(np.isfinite(position)) or not np.all(np.isfinite(orientation))
                or abs(np.linalg.norm(orientation) - 1) > .02):
            return None
        return position, orientation

    def _button_pressed(self, buttons, index: int) -> bool:
        return 0 <= index < len(buttons) and bool(buttons[index])

    def _wrench_is_safe(self, now: Time) -> bool:
        if not self.enable_wrench_stop:
            return True
        if (
            self._last_robot_state_time is None
            or (now - self._last_robot_state_time).nanoseconds * 1e-9
            > self.robot_state_timeout
        ):
            self.get_logger().warn(
                f"Waiting for fresh robot state on {self.robot_state_topic}; motion blocked.",
                throttle_duration_sec=2.0,
            )
            return False
        force = np.linalg.norm(self._latest_force)
        torque = np.linalg.norm(self._latest_torque)
        if force <= self.max_force and torque <= self.max_torque:
            return True
        if not self._safety_latched:
            self.get_logger().error(
                f"Wrench limit exceeded: {force:.2f} N, {torque:.2f} Nm. "
                "Teleoperation disabled."
            )
        self._safety_latched = True
        self.enabled = False
        return False

    def _reset_target_to_actual(
        self, actual_position: np.ndarray, actual_orientation: np.ndarray
    ) -> None:
        self._target_position = actual_position.copy()
        self._target_orientation = actual_orientation.copy()

    def _control_step(self) -> None:
        now = self.get_clock().now()
        raw_dt = (now - self._last_update_time).nanoseconds * 1e-9
        dt = max(0.0, min(raw_dt, 0.05))
        self._last_update_time = now

        with self._lock:
            axes = self._axes.copy()
            last_joy_time = self._last_joy_time
        joy_fresh = (
            last_joy_time is not None
            and (now - last_joy_time).nanoseconds * 1e-9 <= self.joy_timeout
        )
        if self._reset_plan is not None:
            self._reset_control_step(now, raw_dt, joy_fresh, axes)
            return
        if not joy_fresh and last_joy_time is not None and self.enabled:
            self._set_enabled(False)
            self.get_logger().warn("SpaceMouse timeout; re-enable from GUI after reconnecting.")
        if self.enabled and not self._controller_ready():
            self._set_enabled(False)
            self.get_logger().error("Configured controller unavailable or stale; teleoperation paused.")

        actual_pose = self._get_actual_pose()
        if actual_pose is None:
            if self._was_commanding:
                self._set_enabled(False)
            self._publish_twist(now, np.zeros(3), np.zeros(3))
            return
        actual_position, actual_orientation = actual_pose

        # Detect a conflicting teleop/policy launched after this node was enabled.
        if self.enabled and self.count_publishers(self.target_topic) > 1:
            self._set_enabled(False)
            self.get_logger().error("Another Cartesian target publisher detected; paused.")

        if self._target_position is None or self._target_orientation is None:
            self._reset_target_to_actual(actual_position, actual_orientation)

        commanding = (
            self.enabled
            and joy_fresh
            and self._wrench_is_safe(now)
        )

        if not commanding:
            hold = self._hold_requested or self._was_commanding
            if self._was_commanding:
                self.enabled = False
            if self._was_commanding or not self.enabled:
                self._reset_target_to_actual(actual_position, actual_orientation)
            self._was_commanding = False
            if hold:
                self._reset_target_to_actual(actual_position, actual_orientation)
                self._publish_target(now)
                self._hold_requested = False
            self._publish_twist(now, np.zeros(3), np.zeros(3))
            return

        linear_velocity = (
            clamp_vector_norm(axes[:3], 1.0) * self.max_linear_speed
        )
        angular_velocity = (
            clamp_vector_norm(axes[3:], 1.0) * self.max_angular_speed
        )

        if self.command_frame == "tool":
            linear_velocity_base = rotate_vector(self._target_orientation, linear_velocity)
            angular_velocity_base = rotate_vector(
                self._target_orientation, angular_velocity
            )
            delta_orientation = rotvec_to_quaternion(angular_velocity * dt)
            candidate_orientation = quaternion_multiply(
                self._target_orientation, delta_orientation
            )
        else:
            linear_velocity_base = linear_velocity
            angular_velocity_base = angular_velocity
            delta_orientation = rotvec_to_quaternion(angular_velocity * dt)
            candidate_orientation = quaternion_multiply(
                delta_orientation, self._target_orientation
            )

        candidate_position = self._target_position + linear_velocity_base * dt

        error = np.linalg.norm(candidate_position - actual_position)
        orientation_error = np.linalg.norm(quaternion_to_rotvec(quaternion_multiply(
            candidate_orientation, quaternion_inverse(actual_orientation))))
        if error > self.max_position_error or orientation_error > self.max_orientation_error:
            self._safety_latched = True
            self._set_enabled(False)
            self._reset_target_to_actual(actual_position, actual_orientation)
            self._publish_target(now)
            self._hold_requested = False
            delta_mm = (candidate_position - actual_position) * 1000.0
            self._pause_reason = (
                f"位姿跟踪误差超限：位置 {error * 1000:.1f}/{self.max_position_error * 1000:.1f} mm，"
                f"姿态 {np.degrees(orientation_error):.1f}/{np.degrees(self.max_orientation_error):.1f} 度；"
                f"位置偏差 XYZ=[{delta_mm[0]:.1f}, {delta_mm[1]:.1f}, {delta_mm[2]:.1f}] mm"
            )
            self.get_logger().error(self._pause_reason + "; teleoperation paused.")
            return

        self._target_position = candidate_position
        self._target_orientation = candidate_orientation
        self._was_commanding = True
        self._publish_target(now)
        self._publish_twist(now, linear_velocity_base, angular_velocity_base)

    def _publish_target(self, stamp: Time) -> None:
        if self._target_position is None or self._target_orientation is None:
            return
        message = PoseStamped()
        message.header.stamp = stamp.to_msg()
        message.header.frame_id = self.base_frame
        message.pose.position.x = float(self._target_position[0])
        message.pose.position.y = float(self._target_position[1])
        message.pose.position.z = float(self._target_position[2])
        message.pose.orientation.x = float(self._target_orientation[0])
        message.pose.orientation.y = float(self._target_orientation[1])
        message.pose.orientation.z = float(self._target_orientation[2])
        message.pose.orientation.w = float(self._target_orientation[3])
        self.target_publisher.publish(message)

    def _publish_twist(
        self,
        stamp: Time,
        linear_velocity: np.ndarray,
        angular_velocity: np.ndarray,
    ) -> None:
        message = TwistStamped()
        message.header.stamp = stamp.to_msg()
        message.header.frame_id = self.base_frame
        message.twist.linear.x = float(linear_velocity[0])
        message.twist.linear.y = float(linear_velocity[1])
        message.twist.linear.z = float(linear_velocity[2])
        message.twist.angular.x = float(angular_velocity[0])
        message.twist.angular.y = float(angular_velocity[1])
        message.twist.angular.z = float(angular_velocity[2])
        self.twist_publisher.publish(message)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = FrankaSpaceMouseTeleop()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except Exception:
        # ROS signal handling can close the context while spin is waking up.
        # Preserve real runtime errors; only a context already shut down is expected.
        if rclpy.ok():
            raise
    finally:
        if rclpy.ok():
            node._set_enabled(False)
            node._control_step()
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
