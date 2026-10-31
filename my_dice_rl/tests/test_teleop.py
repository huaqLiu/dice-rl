"""ROS tests use domain 187 and isolated topic/action names, never the real arm."""
import time

import numpy as np
import pytest
import rclpy
import yaml
from action_msgs.msg import GoalStatus
from franka_msgs.action import Grasp, Move
from franka_msgs.msg import FrankaRobotState
from rclpy.action import ActionServer, GoalResponse
from rclpy.duration import Duration
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from sensor_msgs.msg import JointState, Joy
from std_srvs.srv import Trigger, SetBool

from my_dice_rl.env.real.teleop import FrankaSpaceMouseTeleop


@pytest.fixture
def node():
    rclpy.init(domain_id=187, args=[
        '--ros-args', '-r', '__ns:=/dice_test',
        '-r', '/cartesian_impedance_target:=/dice_test/target',
        '-p', 'gripper_namespace:=/dice_test/gripper'])
    n = FrankaSpaceMouseTeleop()
    n.timer.cancel()
    n.recording_timer.cancel()
    n._controller_active = True
    n._controller_checked = time.monotonic()
    n._get_actual_pose = lambda: (np.zeros(3), np.array([0., 0., 0., 1.]))
    state(n)
    try:
        yield n
    finally:
        n.destroy_node()
        rclpy.shutdown()


def state(n, force=0.):
    m = FrankaRobotState()
    m.robot_mode = 1
    m.header.stamp = n.get_clock().now().to_msg()
    m.o_f_ext_hat_k = [force, 0., 0., 0., 0., 0.]
    n._robot_state_callback(m)


def joy(n, left=0, right=0, axis=0.):
    m = Joy()
    m.axes = [axis, 0., 0., 0., 0., 0.]
    m.buttons = [left, right]
    n._joy_callback(m)


def width(n, value):
    m = JointState()
    m.header.stamp = n.get_clock().now().to_msg()
    m.name = ['panda_finger_joint1', 'panda_finger_joint2']
    m.position = [value / 2, value / 2]
    n._gripper_state_callback(m)


def test_starts_paused_left_edges_enable_pause_and_reanchor(node):
    assert not node.enabled
    joy(node, left=1)  # Held at startup is not a click.
    joy(node, left=1)
    assert not node.enabled
    joy(node)
    joy(node, left=1)
    assert node.enabled
    for _ in range(3):
        joy(node, left=1)
    assert node.enabled
    joy(node)
    joy(node, left=1)
    assert not node.enabled and node._target_position is None


def test_non_neutral_enable_rejected_and_motion_rate_bounded(node):
    joy(node, axis=.8)
    assert not node._set_enabled(True)[0]
    joy(node)
    assert node._set_enabled(True)[0]
    joy(node, axis=1.)
    node._last_update_time = node.get_clock().now() - Duration(seconds=.02)
    node._control_step()
    assert 0 < node._target_position[0] <= node.max_linear_speed * .025
    node._target_position = np.array([1., 0., 0.])
    node._control_step()
    assert not node.enabled and node._safety_latched


def test_disconnect_never_auto_resumes(node):
    joy(node)
    assert node._set_enabled(True)[0]
    node._last_joy_time = node.get_clock().now() - Duration(seconds=1.)
    node._control_step()
    assert not node.enabled
    joy(node, left=1)
    assert not node.enabled
    joy(node)
    state(node)
    joy(node, left=1)
    assert node.enabled


@pytest.mark.parametrize('rotation', [False, True])
def test_tracking_pause_reports_error_values_and_reenable_clears_reason(node, rotation):
    joy(node)
    assert node._set_enabled(True)[0]
    node._target_position = np.array([0., 0., 0.03 if not rotation else 0.])
    angle = .3 if rotation else 0.
    node._target_orientation = np.array([0., 0., np.sin(angle/2), np.cos(angle/2)])
    node._control_step()
    assert not node.enabled and node._safety_latched
    assert '位姿跟踪误差超限' in node._pause_reason
    assert '位置偏差 XYZ=' in node._pause_reason
    assert ('17.2/14.3' if rotation else '30.0/25.0') in node._pause_reason
    assert node._set_enabled(True)[0]
    assert not node._pause_reason


def test_force_stop_requires_fresh_safe_state(node):
    joy(node)
    assert node._set_enabled(True)[0]
    state(node, force=40.)
    node._control_step()
    assert not node.enabled and node._safety_latched
    assert not node._set_enabled(True)[0]
    state(node)
    node._last_robot_state_time = node.get_clock().now() - Duration(seconds=1.)
    assert not node._set_enabled(True)[0]
    state(node)
    assert node._set_enabled(True)[0]


def test_register_existing_hold_sends_no_action(node):
    assert not node._register_hold(Trigger.Request(), Trigger.Response()).success
    width(node, .035)
    result = node._register_hold(Trigger.Request(), Trigger.Response())
    assert result.success
    assert node._recording_gripper_mode == 'hold_existing'
    assert node._recording_gripper_width == .035
    assert node._gripper_closed and not node._gripper_busy


def test_policy_handover_locks_arm_and_gripper_without_auto_resume(node):
    joy(node)
    width(node, .035)
    assert node._set_enabled(True)[0]
    response = node._policy_lock(SetBool.Request(data=True), SetBool.Response())
    assert response.success and node._policy_locked and not node.enabled
    assert not node._set_enabled(True)[0]
    joy(node, right=1)
    assert not node._gripper_busy
    node._command_gripper(False)
    assert not node._gripper_busy
    response = node._policy_lock(SetBool.Request(data=False), SetBool.Response())
    assert response.success and not node.enabled and not node._policy_locked


def test_handover_rejects_inflight_manual_gripper(node):
    node._gripper_busy = True
    response = node._policy_lock(SetBool.Request(data=True), SetBool.Response())
    assert not response.success and not node._policy_locked


@pytest.mark.parametrize('reject', [False, True])
def test_right_button_actions_acceptance_failure_and_no_repeats(node, reject):
    fake = Node('fake_hand')
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    executor.add_node(fake)
    received = []

    def execute(handle, action):
        received.append(handle.request)
        handle.succeed()
        return action.Result(success=True, error='')

    servers = [ActionServer(fake, action, node.gripper_namespace + '/' + name,
        execute_callback=lambda h, a=action: execute(h, a),
        goal_callback=lambda _: GoalResponse.REJECT if reject else GoalResponse.ACCEPT)
        for action, name in [(Move, 'move'), (Grasp, 'grasp')]]
    try:
        assert node.gripper_move_client.wait_for_server(timeout_sec=3.)
        assert node.gripper_grasp_client.wait_for_server(timeout_sec=3.)
        width(node, .08)
        joy(node)
        joy(node, right=1)
        for _ in range(5):
            joy(node, right=1)
        deadline = time.monotonic() + 3
        while node._gripper_busy and time.monotonic() < deadline:
            executor.spin_once(timeout_sec=.02)
        assert not node._gripper_busy
        if reject:
            assert not received
            assert node._recording_gripper_width is None
            assert node._recording_gripper_error
        else:
            assert len(received) == 1 and isinstance(received[0], Grasp.Goal)
            assert node._recording_gripper_mode == 'grasp'
            width(node, .035)
            joy(node)
            joy(node, right=1)
            deadline = time.monotonic() + 3
            while node._gripper_busy and time.monotonic() < deadline:
                executor.spin_once(timeout_sec=.02)
            assert len(received) == 2 and isinstance(received[1], Move.Goal)
            assert node._recording_gripper_mode == 'move'
    finally:
        for server in servers:
            server.destroy()
        executor.shutdown()
        fake.destroy_node()


def test_configured_controller_required_and_loss_pauses(node):
    joy(node)
    node._controller_active = False
    assert not node._set_enabled(True)[0]
    node._controller_active = True
    node._controller_checked = time.monotonic()
    assert node._set_enabled(True)[0]
    node._controller_active = False
    node._control_step()
    assert not node.enabled


@pytest.mark.parametrize('configured', ['effort', 'position'])
def test_only_configured_command_interface_passes_check(node, configured):
    from concurrent.futures import Future
    from controller_manager_msgs.msg import ControllerState
    from controller_manager_msgs.srv import ListControllers
    node.controller_command_interface = configured
    for interface in ['effort', 'position']:
        expected = interface == configured
        response = ListControllers.Response()
        response.controller = [ControllerState(name=node.controller_name, state='active',
            claimed_interfaces=[f'panda_joint{i}/{interface}' for i in range(1, 8)])]
        future = Future()
        future.set_result(response)
        node._controller_done(future)
        assert node._controller_ready() is expected


def prepare_reset(node, tmp_path):
    reference = tmp_path / 'reset.yaml'
    reference.write_text(yaml.safe_dump(dict(reset_base_frame=node.base_frame, reset_ee_frame=node.ee_frame,
        reset_position_m=[.01, 0., 0.], reset_orientation_xyzw=[0., 0., 0., 1.])))
    node.reset_reference_file = reference
    joy(node)
    state(node)


def test_reset_and_recording_are_mutually_exclusive(node, tmp_path):
    prepare_reset(node, tmp_path)
    assert node._recording_lock(SetBool.Request(data=True), SetBool.Response()).success
    assert not node._start_reset(Trigger.Request(), Trigger.Response()).success
    assert node._recording_lock(SetBool.Request(data=False), SetBool.Response()).success
    assert node._start_reset(Trigger.Request(), Trigger.Response()).success
    assert not node.enabled and node._reset_plan is not None
    assert not node._recording_lock(SetBool.Request(data=True), SetBool.Response()).success
    assert not node._policy_lock(SetBool.Request(data=True), SetBool.Response()).success
    assert not node._set_enabled(True)[0]


@pytest.mark.parametrize('failure', ['force', 'stale_state', 'tracking', 'controller', 'joy_timeout', 'tf', 'cycle'])
def test_reset_fault_stops_without_resuming(node, tmp_path, failure):
    prepare_reset(node, tmp_path)
    assert node._start_reset(Trigger.Request(), Trigger.Response()).success
    if failure == 'force':
        state(node, force=100.)
    elif failure == 'stale_state':
        node._last_robot_state_time = node.get_clock().now() - Duration(seconds=1.)
    elif failure == 'tracking':
        node._get_actual_pose = lambda: (np.ones(3), np.array([0., 0., 0., 1.]))
    elif failure == 'controller':
        node._controller_active = False
    elif failure == 'joy_timeout':
        node._last_joy_time = node.get_clock().now() - Duration(seconds=1.)
    elif failure == 'tf':
        node._get_actual_pose = lambda: None
    elif failure == 'cycle':
        node._last_update_time = node.get_clock().now() - Duration(seconds=1.)
    node._control_step()
    assert node._reset_plan is None and not node.enabled
    assert node._safety_latched and node._reset_message


@pytest.mark.parametrize('stop', ['pause', 'axis', 'button'])
def test_reset_can_be_cancelled_without_gripper_action(node, tmp_path, stop):
    prepare_reset(node, tmp_path)
    width(node, .035)
    assert node._start_reset(Trigger.Request(), Trigger.Response()).success
    if stop == 'pause':
        node._set_enabled(False)
    else:
        joy(node, axis=.8 if stop == 'axis' else 0., right=int(stop == 'button'))
    assert node._reset_plan is None and not node.enabled and not node._gripper_busy
    if stop != 'pause':
        np.testing.assert_allclose(node._target_position, np.zeros(3))


def test_reset_completes_only_after_actual_arrival_and_stays_paused(node, tmp_path):
    prepare_reset(node, tmp_path)
    actual = [np.zeros(3), np.array([0., 0., 0., 1.])]
    node._get_actual_pose = lambda: tuple(part.copy() for part in actual)
    published = []
    node._publish_target = lambda stamp: published.append(node._target_position.copy())
    assert node._start_reset(Trigger.Request(), Trigger.Response()).success
    duration = node._reset_plan.duration
    base_time = node.get_clock().now()
    # A deterministic clock lets this test exercise the full controller at 50 Hz.
    class Clock:
        current = base_time
        def now(self):
            return self.current
    clock = Clock()
    original = node.get_clock
    node.get_clock = lambda: clock
    try:
        for elapsed in np.arange(0., duration + 1., .02):
            clock.current = base_time + Duration(seconds=float(elapsed))
            state(node)
            joy(node)
            node._controller_checked = time.monotonic()
            node._control_step()
            actual[:] = [node._target_position.copy(), node._target_orientation.copy()]
            if node._reset_plan is None:
                break
        assert node._reset_plan is None and '完成' in node._reset_message
        assert not node.enabled
        np.testing.assert_allclose(actual[0], [.01, 0., 0.], atol=1e-9)
        assert np.max(np.linalg.norm(np.diff(published, axis=0), axis=1)) <= .02 * .02 * 1.001
    finally:
        node.get_clock = original
