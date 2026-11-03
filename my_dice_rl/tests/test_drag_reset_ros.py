"""Random reset, controller switches and cancellation on fake ROS devices / DDS187."""
from concurrent.futures import Future
import json
import time
import threading
from types import SimpleNamespace

import numpy as np
import pytest
import rclpy
from controller_manager_msgs.msg import ControllerState
from controller_manager_msgs.srv import ListControllers, LoadController, ConfigureController, SwitchController
from geometry_msgs.msg import PoseStamped, TransformStamped
from franka_msgs.msg import FrankaRobotState
from rclpy.node import Node
from rclpy.executors import SingleThreadedExecutor
from sensor_msgs.msg import JointState
from std_msgs.msg import String
from std_srvs.srv import SetBool
from tf2_ros import TransformBroadcaster

from my_dice_rl.env.real.drag_backend import DragCollectorNode
from my_dice_rl.tests.test_drag_collection import drag_config
from my_dice_rl.util.recording import StreamBuffer, atomic_json
from my_dice_rl.util.reset_motion import load_reset_pose
from my_dice_rl.util.rotation import quaternion_angle


def test_later_pause_does_not_hide_original_fault():
    from my_dice_rl.env.real.drag_reset import DragReset
    reset = object.__new__(DragReset)
    reset.lock, reset.busy, reset.stopping = threading.RLock(), True, False
    reset.phase, reset.diagnostics = 'MOTION', {}
    events = []
    reset.event = lambda kind, **values: events.append((kind, values))
    reset.cancel('复位跟踪误差超限')
    reset.cancel('复位已由操作员停止')
    reset.cancel('关闭窗口，复位已停止')
    assert reset.stop_reason == '复位跟踪误差超限'
    assert len(events) == 1 and events[0][1]['message'] == reset.stop_reason


@pytest.mark.parametrize('outcome', ['completed', 'startup_race', 'settle_stall', 'stop', 'gui_timeout', 'force', 'delayed_switch_stop'])
def test_staged_random_reset_and_faults_with_fake_controllers(tmp_path, outcome):
    rclpy.init(domain_id=187)
    config = drag_config(tmp_path)
    prefix = '/dice_random_reset_test'
    for key in ('wrist_topic', 'external_topic', 'gripper_topic', 'robot_state_topic', 'target_topic',
                'teleop_topic', 'teleop_enable_service'):
        config[key] = prefix+'/'+key
    config['drag'].update(heartbeat_topic=prefix+'/heartbeat', state_topic=prefix+'/state')
    config['reset'].update(reference_file=str(tmp_path/'reference.yaml'), controller_manager=prefix+'/manager',
        command_topic=prefix+'/command', audit_file=str(tmp_path/'audit.jsonl'), linear_speed=.2,
        angular_speed=1., linear_acceleration=2., angular_acceleration=3., settle_s=.03, timeout_s=20.)
    reference = dict(reset_base_frame=config['base_frame'], reset_ee_frame=config['ee_frame'],
                     reset_position_m=[.5, 0., .16], reset_orientation_xyzw=[1., 0., 0., 0.])
    atomic_json(tmp_path/'reference.yaml', reference)
    fake = Node('fake_random_reset_devices')
    buffers = StreamBuffer()
    backend = DragCollectorNode(config, buffers)
    reset = backend.reset_controller
    reset.rng = np.random.default_rng(34)
    executor = SingleThreadedExecutor()
    for node in (fake, backend): executor.add_node(node)
    drag, cart = config['controller_name'], config['reset']['controller_name']
    controllers = {drag: 'active'}
    pose = np.array([.49, .01, .06, 1., 0., 0., 0.])
    modes, force = [0], [0.]
    commands, switches = [], []
    def listing(request, response):
        response.controller = [ControllerState(name=name, state=state, claimed_interfaces=(
            [f'panda_joint{i}/effort' for i in range(1, 8)] if state == 'active' else []))
            for name, state in controllers.items()]
        return response
    def loading(request, response):
        controllers[request.name] = 'unconfigured'
        response.ok = True
        return response
    def configure(request, response):
        controllers[request.name] = 'inactive'
        response.ok = True
        return response
    def switch(request, response):
        assert request.strictness == SwitchController.Request.STRICT
        for name in request.deactivate_controllers: controllers[name] = 'inactive'
        for name in request.activate_controllers: controllers[name] = 'active'
        modes[0] = 0
        switches.append(list(request.activate_controllers))
        response.ok = True
        return response
    manager = config['reset']['controller_manager']
    for name, cls, callback in [('list_controllers', ListControllers, listing),
            ('load_controller', LoadController, loading), ('configure_controller', ConfigureController, configure),
            ('switch_controller', SwitchController, switch)]:
        fake.create_service(cls, manager+'/'+name, callback)
    def enable(request, response):
        modes[0] = 1 if request.data else 0
        response.success = controllers[drag] == 'active'
        return response
    fake.create_service(SetBool, config['teleop_enable_service'], enable)
    def command(message):
        p, q = message.pose.position, message.pose.orientation
        value = np.array([p.x, p.y, p.z, q.x, q.y, q.z, q.w])
        commands.append(value)
        if controllers.get(cart) == 'active':
            pose[:] = value
            if outcome == 'settle_stall' and reset.phase == 'MOTION':
                pose[2] -= .005  # Simulate a 5 mm equilibrium error in impedance control.
    fake.create_subscription(PoseStamped, config['reset']['command_topic'], command, 1)
    tf = TransformBroadcaster(fake)
    grip = fake.create_publisher(JointState, config['gripper_topic'], 1)
    robot = fake.create_publisher(FrankaRobotState, config['robot_state_topic'], 1)
    state = fake.create_publisher(String, config['drag']['state_topic'], 1)
    def publish():
        stamp = fake.get_clock().now().to_msg()
        t = TransformStamped()
        t.header.stamp, t.header.frame_id, t.child_frame_id = stamp, config['base_frame'], config['ee_frame']
        p, q = t.transform.translation, t.transform.rotation
        p.x, p.y, p.z = map(float, pose[:3])
        q.x, q.y, q.z, q.w = map(float, pose[3:])
        tf.sendTransform(t)
        msg = JointState()
        msg.header.stamp, msg.name, msg.position = stamp, config['gripper_joint_names'], [.015, .015]
        grip.publish(msg)
        msg = FrankaRobotState()
        msg.header.stamp, msg.robot_mode = stamp, 2
        msg.o_f_ext_hat_k = [force[0], 0., 0., 0., 0., 0.]
        robot.publish(msg)
        state.publish(String(data=json.dumps(dict(active=controllers[drag] == 'active', mode=modes[0],
            stamp=fake.get_clock().now().nanoseconds*1e-9))))
    fake.create_timer(.01, publish)
    def pump(seconds, gui=True):
        end = time.monotonic()+seconds
        while time.monotonic() < end:
            if gui: backend.gui_tick()
            executor.spin_once(timeout_sec=.005)
    def until(predicate, timeout=8., gui=True):
        end = time.monotonic()+timeout
        while time.monotonic() < end and not predicate(): pump(.01, gui)
        assert predicate(), reset.message
    try:
        pump(.6)
        assert backend.request_control('hold').result().success
        if outcome == 'delayed_switch_stop':
            original = reset.clients['switch_controller']
            deferred = Future()
            class DeferFirst:
                calls = 0
                def service_is_ready(self): return original.service_is_ready()
                def call_async(self, request):
                    self.calls += 1
                    return deferred if self.calls == 1 else original.call_async(request)
            reset.clients['switch_controller'] = DeferFirst()
        if outcome == 'startup_race':
            # Qt can be preempted during initialization while ROS tick runs.
            # The previous code exposed busy=True before CHECK and raced into motion_tick.
            entered, release = threading.Event(), threading.Event()
            original_event = reset.event
            def slow_event(kind, **values):
                if kind == 'random_goal':
                    entered.set()
                    assert release.wait(1.)
                original_event(kind, **values)
            reset.event = slow_event
            responses = []
            worker = threading.Thread(target=lambda: responses.append(backend.request_control('reset').result()))
            worker.start()
            assert entered.wait(1.)
            timer = threading.Timer(.06, release.set)
            timer.start()
            pump(.12)
            worker.join(1.)
            timer.join(1.)
            assert responses[0].success and not reset.stop_reason
        else:
            assert backend.request_control('reset').result().success
        first_goal = reset.goal.copy()
        assert not backend.request_control('enable').result().success
        assert not backend.request_recording_lock(True).result().success
        if outcome == 'delayed_switch_stop':
            until(lambda: reset.pending is not None and reset.pending[0] == 'CART')
            backend.request_control('pause')
            pump(.1)
            assert reset.busy
            controllers[drag], controllers[cart] = 'inactive', 'active'
            deferred.set_result(SimpleNamespace(ok=True))
        elif outcome == 'settle_stall':
            until(lambda: reset.diagnostics and reset.diagnostics['elapsed_s'] >
                  reset.diagnostics['planned_duration_s']+.12)
            assert reset.stage_index == 1 and '等待实际到位' in reset.message
            assert reset.diagnostics['position_error_m'] > config['reset']['position_tolerance_m']
            reset.started = time.monotonic()-config['reset']['timeout_s']-1.
        elif outcome not in ('completed', 'startup_race'):
            until(lambda: pose[2] > .065)
            if outcome == 'stop': backend.request_control('pause')
            elif outcome == 'force': force[0] = 25.
        until(lambda: not reset.busy, gui=outcome != 'gui_timeout')
        pump(.08)
        assert controllers[drag] == 'active' and controllers[cart] == 'inactive'
        assert not backend.desired and not buffers.latest('teleop').value['resetting']
        if outcome in ('completed', 'startup_race'):
            np.testing.assert_allclose(pose, first_goal, atol=1e-8)
            assert not reset.stop_reason
            assert np.all(np.abs(first_goal[:3]-[.5, 0., .16]) <= .02)
            assert quaternion_angle(first_goal[3:], [1., 0., 0., 0.]) <= np.deg2rad(10.)
            # Before full withdrawal, all commands preserve original XY and orientation.
            for value in commands:
                if value[2] >= .19-1e-8: break
                np.testing.assert_allclose(value[:2], [.49, .01], atol=1e-10)
                assert quaternion_angle(value[3:], [1., 0., 0., 0.]) < 1e-8
            assert switches == [[cart], [drag]]
        else:
            assert reset.stop_reason
            assert pose[2] < .19  # Stop before full withdrawal, no automatic continuation.
            if outcome == 'gui_timeout': assert '超时' in reset.stop_reason
            if outcome == 'force': assert '力/力矩' in reset.stop_reason
            if outcome == 'settle_stall':
                assert reset.stop_reason == '复位超时'
                assert '距本段目标' in reset.message and '退轴' in reset.message
        previous = pose.copy()
        pump(.1)
        np.testing.assert_array_equal(pose, previous)
        saved = load_reset_pose(tmp_path/'reference.yaml', config['base_frame'], config['ee_frame'])
        np.testing.assert_array_equal(saved[0], [.5, 0., .16])
        if outcome in ('completed', 'startup_race'):
            backend.recording_locked = True
            assert not backend.request_control('save_reset').result().success
            backend.recording_locked = False
            assert backend.request_control('save_reset').result().success
            updated = load_reset_pose(tmp_path/'reference.yaml', config['base_frame'], config['ee_frame'])
            np.testing.assert_allclose(np.r_[updated[0], updated[1]], pose, atol=1e-10)
    finally:
        executor.shutdown(timeout_sec=1.)
        backend.destroy_node()
        fake.destroy_node()
        rclpy.shutdown()
