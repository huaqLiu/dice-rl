"""Real ROS messages/actions with fake devices, isolated on DDS domain 187."""
import time

import numpy as np
import rclpy
from controller_manager_msgs.msg import ControllerState
from controller_manager_msgs.srv import ListControllers
from franka_msgs.action import Grasp, Move
from franka_msgs.msg import FrankaRobotState
from geometry_msgs.msg import PoseStamped, TransformStamped
from rcl_interfaces.msg import ParameterValue
from rcl_interfaces.srv import GetParameters
from rclpy.action import ActionServer
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from sensor_msgs.msg import Image, JointState, Joy
from tf2_ros import TransformBroadcaster

from my_dice_rl.agent.eval.dp_executor import DPExecutor
from my_dice_rl.agent.eval.dp_policy import DemoPolicy
from my_dice_rl.env.real.policy_backend import PolicyROSNode
from my_dice_rl.env.real.policy_observation import ObservationBuffer
from my_dice_rl.env.real.teleop import FrankaSpaceMouseTeleop
from my_dice_rl.script.rl_collect_gui import load_config
from my_dice_rl.util.config import PROJECT_ROOT


def test_ros_handover_policy_gripper_and_labelled_rollout(tmp_path):
    rclpy.init(domain_id=187, args=['--ros-args', '-r', '__ns:=/policy_test',
        '-r', '/cartesian_impedance_target:=/policy_test/target',
        '-r', '/dice_spacemouse/joy:=/policy_test/joy',
        '-r', '/franka_robot_state_broadcaster/robot_state:=/policy_test/state',
        '-p', 'controller_name:=policy_test_controller',
        '-p', 'controller_service:=/policy_test/list_controllers',
        '-p', 'gripper_namespace:=/policy_test/gripper'])
    config, collector = load_config(PROJECT_ROOT/'cfg/execute_dp.yaml')
    config.update(data_root=str(tmp_path), min_free_gb=0.,device_lock=str(tmp_path/'device.lock'),controller_name='policy_test_controller',
        controller_service='/policy_test/list_controllers',
        teleop_node='/policy_test/dice_spacemouse_teleop',
        teleop_lock_service='/policy_test/dice_spacemouse_teleop/policy_lock')
    collector.update(wrist_topic='/policy_test/wrist/image_raw', external_topic='/policy_test/external/image_raw',
        target_topic='/policy_test/target', robot_state_topic='/policy_test/state',
        gripper_topic='/policy_test/gripper/joint_states',
        teleop_topic='/policy_test/dice_spacemouse_teleop/recording_state',
        teleop_enable_service='/policy_test/dice_spacemouse_teleop/enable',
        reset_service='/policy_test/dice_spacemouse_teleop/reset')
    buffer = ObservationBuffer()
    teleop = FrankaSpaceMouseTeleop()
    backend = PolicyROSNode(collector, config, buffer)
    fake = Node('fake_devices')
    arm = dict(pose=np.array([.45, 0., .3, 1., 0., 0., 0.]), width=.035)
    targets, gripper_goals = [], []

    def receive_target(message):
        p, q = message.pose.position, message.pose.orientation
        arm['pose'] = np.array([p.x, p.y, p.z, q.x, q.y, q.z, q.w])
        targets.append(arm['pose'].copy())

    fake.create_subscription(PoseStamped, collector['target_topic'], receive_target, 10)
    tf = TransformBroadcaster(fake)
    images = [fake.create_publisher(Image, collector[key+'_topic'], 10) for key in ('wrist', 'external')]
    joints = fake.create_publisher(JointState, collector['gripper_topic'], 10)
    states = fake.create_publisher(FrankaRobotState, collector['robot_state_topic'], 10)
    joystick = fake.create_publisher(Joy, '/policy_test/joy', 10)

    def controllers(request, response):
        response.controller = [ControllerState(name=config['controller_name'], state='active',
            claimed_interfaces=[f"panda_joint{i}/{config['controller_command_interface']}" for i in range(1, 8)])]
        return response

    def parameters(request, response):
        response.values = [ParameterValue(type=4, string_value=collector[key])
                           for key in ('base_frame', 'ee_frame')]
        return response

    fake.create_service(ListControllers, config['controller_service'], controllers)
    fake.create_service(GetParameters, '/policy_test_controller/get_parameters', parameters)

    def gripper(handle, action):
        gripper_goals.append(handle.request)
        arm['width'] = .035 if action is Grasp else handle.request.width
        handle.succeed()
        return action.Result(success=True, error='')

    servers = [ActionServer(fake, action, '/policy_test/gripper/'+name,
               execute_callback=lambda handle, a=action: gripper(handle, a))
               for action, name in ((Move, 'move'), (Grasp, 'grasp'))]

    def publish():
        stamp = fake.get_clock().now().to_msg()
        pose = arm['pose']
        transform = TransformStamped()
        transform.header.stamp, transform.header.frame_id = stamp, collector['base_frame']
        transform.child_frame_id = collector['ee_frame']
        p, q = transform.transform.translation, transform.transform.rotation
        p.x, p.y, p.z = map(float, pose[:3])
        q.x, q.y, q.z, q.w = map(float, pose[3:])
        tf.sendTransform(transform)
        for publisher in images:
            message = Image()
            message.header.stamp = stamp
            message.width, message.height, message.step = 160, 120, 160*3
            message.encoding = 'rgb8'
            message.data = bytes([70])*(160*120*3)
            publisher.publish(message)
        message = JointState()
        message.header.stamp = stamp
        message.name = collector['gripper_joint_names']
        message.position = [arm['width']/2]*2
        joints.publish(message)
        message = FrankaRobotState()
        message.header.stamp, message.robot_mode = stamp, 2
        states.publish(message)
        message = Joy()
        message.axes, message.buttons = [0.]*6, [0, 0]
        joystick.publish(message)

    class OpenThenClose(DemoPolicy):
        act_steps = 1
        calls = 0
        def predict(self, history):
            self.calls += 1
            actions, noise = super().predict(history)
            actions[:, 9] = .08 if self.calls <= 3 else 0.
            return actions, noise

    engine = DPExecutor(config, collector, buffer, backend, simulated=True, policy=OpenThenClose())
    fake.create_timer(.02, publish)
    backend.create_timer(.02, engine.tick)
    executor = SingleThreadedExecutor()
    for node in (fake, teleop, backend):
        executor.add_node(node)

    def spin_until(predicate, timeout=5.):
        end = time.monotonic()+timeout
        while time.monotonic() < end:
            executor.spin_once(timeout_sec=.01)
            if predicate():
                return
        raise AssertionError(engine.status())

    try:
        spin_until(lambda: backend.controller_active and backend.controller_frames is not None
                   and buffer.latest('gripper') is not None and teleop._last_joy_time is not None)
        end = time.monotonic()+.25
        while time.monotonic() < end:
            executor.spin_once(timeout_sec=.01)
        engine.submit('ready', held_confirmed=True)
        spin_until(lambda: engine.state == 'READY')
        assert teleop._policy_locked and not teleop.enabled
        engine.submit('start')
        spin_until(lambda: len(gripper_goals) >= 2)
        assert isinstance(gripper_goals[0], Move.Goal)
        assert isinstance(gripper_goals[1], Grasp.Goal)
        assert targets
        engine.submit('stop')
        spin_until(lambda: engine.state == 'LABEL')
        assert teleop._policy_locked
        assert not teleop.enabled and backend.publisher is None
        engine.submit('label', outcome='failed')
        spin_until(lambda: engine.state == 'RESET')
        spin_until(lambda: not teleop._policy_locked)
        assert engine.writer.meta['eligible_for_rl']
        assert engine.writer.meta['outcome'] == 'failed'
        spin_until(lambda: engine.status()['manual']['can_enable'])
        engine.submit('manual_enable')
        spin_until(lambda: teleop.enabled and not engine.status()['manual']['busy'])
        engine.submit('stop')
        spin_until(lambda: not teleop.enabled and not engine.status()['manual']['busy'])
        engine.submit('manual_reset')
        spin_until(lambda: teleop._reset_plan is not None)
        engine.submit('stop')
        spin_until(lambda: teleop._reset_plan is None and not engine.status()['manual']['busy'])
    finally:
        engine.submit('close')
        try:
            spin_until(lambda: engine.status()['can_close'], 3.)
        finally:
            engine.shutdown()
            executor.shutdown()
            for server in servers:
                server.destroy()
            for node in (backend, teleop, fake):
                node.destroy_node()
            rclpy.shutdown()
