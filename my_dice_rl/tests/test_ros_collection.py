"""Exercise the actual ROS sensor/TF/teleop/recorder pipeline with fake devices."""
import json
import time

import h5py
import numpy as np
import rclpy
from franka_msgs.msg import FrankaRobotState
from geometry_msgs.msg import TransformStamped
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from sensor_msgs.msg import Image, JointState, Joy
from std_srvs.srv import Trigger
from tf2_ros import TransformBroadcaster
import yaml
from pathlib import Path

from my_dice_rl.env.real.ros_backend import CollectorNode
from my_dice_rl.env.real.teleop import FrankaSpaceMouseTeleop
from my_dice_rl.util.recording import Recorder, StreamBuffer


def test_fake_ros_devices_record_real_backend(tmp_path):
    rclpy.init(domain_id=187, args=['--ros-args', '-r', '__ns:=/dice_pipeline_test',
        '-r', '/cartesian_impedance_target:=/dice_pipeline_test/target',
        '-r', '/dice_spacemouse/joy:=/dice_pipeline_test/joy',
        '-r', '/franka_robot_state_broadcaster/robot_state:=/dice_pipeline_test/state',
        '-p', 'gripper_namespace:=/dice_pipeline_test/gripper'])
    config = yaml.safe_load((Path(__file__).parents[1] / 'cfg/collect.yaml').read_text())
    config.update(data_root=str(tmp_path), min_free_gb=0,
        min_episode_frames=2,
        wrist_topic='/dice_pipeline_test/wrist/image_raw',
        external_topic='/dice_pipeline_test/external/image_raw',
        target_topic='/dice_pipeline_test/target',
        gripper_topic='/dice_pipeline_test/gripper/joint_states',
        robot_state_topic='/dice_pipeline_test/state',
        teleop_topic='/dice_pipeline_test/dice_spacemouse_teleop/recording_state')
    buffers = StreamBuffer()
    fake = Node('fake_devices')
    teleop = FrankaSpaceMouseTeleop()
    collector = CollectorNode(config, buffers)
    tf = TransformBroadcaster(fake)
    images = [fake.create_publisher(Image, config[k + '_topic'], 10) for k in ['wrist', 'external']]
    joints = fake.create_publisher(JointState, config['gripper_topic'], 10)
    state = fake.create_publisher(FrankaRobotState, config['robot_state_topic'], 10)
    joystick = fake.create_publisher(Joy, '/dice_pipeline_test/joy', 10)
    executor = SingleThreadedExecutor()
    for node in [fake, teleop, collector]:
        executor.add_node(node)
    recorder = Recorder(config, buffers)

    def publish():
        stamp = fake.get_clock().now().to_msg()
        transform = TransformStamped()
        transform.header.stamp, transform.header.frame_id = stamp, config['base_frame']
        transform.child_frame_id = config['ee_frame']
        transform.transform.translation.x = .45
        transform.transform.translation.z = .3
        transform.transform.rotation.w = 1.
        tf.sendTransform(transform)
        for index, publisher in enumerate(images):
            message = Image()
            message.header.stamp = stamp
            message.width, message.height = 160, 120
            message.encoding, message.step = 'rgb8', 160 * 3
            message.data = np.full((120, 160, 3), 70 + index * 60, np.uint8).tobytes()
            publisher.publish(message)
        message = JointState()
        message.header.stamp = stamp
        message.name = config['gripper_joint_names']
        message.position = [.02, .02]
        joints.publish(message)
        message = FrankaRobotState()
        message.header.stamp = stamp
        state.publish(message)
        message = Joy()
        message.axes, message.buttons = [0.] * 6, [0, 0]
        joystick.publish(message)

    fake.create_timer(.02, publish)
    try:
        deadline = time.monotonic() + 4.
        last_reason = 'not attempted'
        while time.monotonic() < deadline:
            executor.spin_once(timeout_sec=.01)
            if not teleop.enabled:
                hold = teleop._register_hold(Trigger.Request(), Trigger.Response())
                if hold.success:
                    _, last_reason = teleop._set_enabled(True)
            try:
                recorder.ready()
                break
            except ValueError:
                continue
        assert teleop.enabled, (last_reason, buffers.errors, fake.get_topic_names_and_types())
        recorder.ready()
        recorder.start('gear_insertion')
        deadline = time.monotonic() + .5
        while time.monotonic() < deadline:
            executor.spin_once(timeout_sec=.005)
        recorder.stop('success')
        recorder.thread.join(timeout=2.)
        assert not recorder.active
        metadata = json.loads((recorder.last_path / 'metadata.json').read_text())
        assert metadata['status'] == 'success', metadata
        with h5py.File(recorder.last_path / 'trajectory.h5') as file:
            assert len(file['timestamp']) >= 2
            np.testing.assert_allclose(file['robot_eef_pose'][:, 0], .45)
            assert np.all(file['gripper_mode'][:] == 0)
            assert file['external_wrench'].shape[1] == 6
    finally:
        if recorder.active:
            recorder.stop('failed')
            recorder.thread.join(timeout=2.)
        executor.shutdown()
        for node in [collector, teleop, fake]:
            node.destroy_node()
        rclpy.shutdown()
