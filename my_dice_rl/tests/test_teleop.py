"""ROS tests use domain 187 and isolated topic/action names, never the real arm."""
import time

import numpy as np
import pytest
import rclpy
from action_msgs.msg import GoalStatus
from franka_msgs.action import Grasp, Move
from franka_msgs.msg import FrankaRobotState
from rclpy.action import ActionServer, GoalResponse
from rclpy.duration import Duration
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from sensor_msgs.msg import JointState, Joy
from std_srvs.srv import Trigger

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
    n._get_actual_pose = lambda: (np.zeros(3), np.array([0., 0., 0., 1.]))
    state(n)
    try:
        yield n
    finally:
        n.destroy_node()
        rclpy.shutdown()


def state(n, force=0.):
    m = FrankaRobotState()
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
