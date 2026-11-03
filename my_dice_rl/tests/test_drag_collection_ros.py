"""Real ROS subscriptions/services, fake devices on isolated DDS domain 187."""
import pytest
import json
import time

import h5py
import numpy as np
import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from geometry_msgs.msg import TransformStamped, PoseStamped
from franka_msgs.msg import FrankaRobotState
from sensor_msgs.msg import Image, JointState
from std_msgs.msg import String, Empty
from std_srvs.srv import SetBool
from tf2_ros import TransformBroadcaster

from my_dice_rl.env.real.drag_backend import DragCollectorNode
from my_dice_rl.tests.test_drag_collection import drag_config
from my_dice_rl.util.recording import Recorder, StreamBuffer


@pytest.mark.parametrize('camera_count', [2,3])
def test_drag_ros_record_and_safety_pause_without_pose_commands(tmp_path, camera_count):
    rclpy.init(domain_id=187)
    config = drag_config(tmp_path)
    config['min_episode_frames'] = 2
    config['camera_names'] = ['wrist','external'] + (['side'] if camera_count==3 else [])
    if camera_count==3: config['side_topic']='/dice_drag_test/side/image_raw'
    prefix = '/dice_drag_test'
    for key in ('wrist_topic', 'external_topic', 'gripper_topic', 'robot_state_topic', 'target_topic',
                'teleop_topic', 'teleop_enable_service'):
        config[key] = prefix+'/'+key
    config['drag'].update(heartbeat_topic=prefix+'/heartbeat', state_topic=prefix+'/state')
    config['reset'].update(command_topic=prefix+'/reset_target', audit_file=str(tmp_path/'reset_events.jsonl'))
    buffers = StreamBuffer(config)
    backend = DragCollectorNode(config, buffers)
    fake = Node('drag_fake_devices')
    executor = SingleThreadedExecutor()
    for node in (fake, backend): executor.add_node(node)
    mode, force = [0], [0.]
    heartbeat_count = [0]
    reset_commands = []
    fake.create_subscription(PoseStamped, config['reset']['command_topic'], reset_commands.append, 1)
    def enable(request, response):
        mode[0] = 1 if request.data else 0
        response.success = True
        return response
    fake.create_service(SetBool, config['teleop_enable_service'], enable)
    fake.create_subscription(Empty, config['drag']['heartbeat_topic'],
        lambda msg: heartbeat_count.__setitem__(0, heartbeat_count[0]+1), 1)
    tf = TransformBroadcaster(fake)
    images = [fake.create_publisher(Image, config[key+'_topic'], 1) for key in buffers.camera_names]
    grip = fake.create_publisher(JointState, config['gripper_topic'], 1)
    robot = fake.create_publisher(FrankaRobotState, config['robot_state_topic'], 1)
    state = fake.create_publisher(String, config['drag']['state_topic'], 1)
    stale_target = fake.create_publisher(PoseStamped, config['target_topic'], 1)
    def publish():
        stamp = fake.get_clock().now().to_msg()
        t = TransformStamped()
        t.header.stamp, t.header.frame_id = stamp, config['base_frame']
        t.child_frame_id = config['ee_frame']
        t.transform.translation.x, t.transform.translation.z = .45, .15+.001*np.sin(time.monotonic())
        t.transform.rotation.x = 1.
        tf.sendTransform(t)
        for pub in images:
            msg = Image()
            msg.header.stamp = stamp
            msg.width, msg.height, msg.step, msg.encoding = 64, 48, 192, 'rgb8'
            msg.data = np.zeros((48, 64, 3), np.uint8).tobytes()
            pub.publish(msg)
        msg = JointState()
        msg.header.stamp, msg.name, msg.position = stamp, config['gripper_joint_names'], [.02, .02]
        grip.publish(msg)
        msg = FrankaRobotState()
        msg.header.stamp, msg.robot_mode = stamp, 2
        msg.o_f_ext_hat_k = [force[0], 0., 0., 0., 0., 0.]
        robot.publish(msg)
        state.publish(String(data=json.dumps(dict(active=True, mode=mode[0],
            stamp=fake.get_clock().now().nanoseconds*1e-9))))
        msg = PoseStamped()
        msg.header.stamp, msg.header.frame_id = stamp, config['base_frame']
        msg.pose.position.x, msg.pose.orientation.w = .9, 1.
        stale_target.publish(msg)
    fake.create_timer(.02, publish)
    def pump(seconds, gui=True):
        deadline = time.monotonic()+seconds
        while time.monotonic() < deadline:
            if gui: backend.gui_tick()
            executor.spin_once(timeout_sec=.01)
    recorder = Recorder(config, buffers)
    try:
        pump(.7)
        assert not backend.request_control('enable').result().success
        assert backend.request_control('hold').result().success
        backend.request_control('enable')
        pump(.4)
        assert buffers.latest('teleop').value['enabled']
        recorder.start('gear_insertion')
        pump(.65)
        recorder.stop('success')
        backend.request_control('pause')
        pump(.3)
        recorder.thread.join(3.)
        metadata = json.loads((recorder.last_path/'metadata.json').read_text())
        assert metadata['status'] == 'success' and metadata['frames'] >= 2
        with h5py.File(recorder.last_path/'trajectory.h5') as file:
            np.testing.assert_array_equal(file['robot_eef_pose'][:], file['target_eef_pose'][:])
            assert np.max(file['target_eef_pose'][:, 0]) < .5
        backend.request_control('enable')
        pump(.3)
        force[0] = 100.
        pump(.3)
        assert not backend.desired and mode[0] == 0
        assert '外力' in backend.pause_reason
        force[0] = 0.
        pump(.1)
        backend.request_control('enable')
        pump(.3)
        pump(.65, gui=False)
        assert not backend.desired and '界面心跳' in backend.pause_reason
        assert heartbeat_count[0] > 0
        assert not reset_commands  # Publisher exists, but ordinary dragging never sends pose commands.
    finally:
        if recorder.active:
            recorder.stop('failed')
            recorder.thread.join(3.)
        executor.shutdown(timeout_sec=1.)
        backend.destroy_node()
        fake.destroy_node()
        rclpy.shutdown()
