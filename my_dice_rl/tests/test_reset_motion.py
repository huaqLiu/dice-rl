"""Reset trajectory bounds and reference validation, without hardware."""
import numpy as np
import pytest
import yaml

from my_dice_rl.util.reset_motion import ResetTrajectory, load_reset_pose
from my_dice_rl.util.spacemouse_math import quaternion_inverse, quaternion_multiply, quaternion_to_rotvec


def test_reset_curve_has_bounded_velocity_acceleration_and_shortest_rotation():
    start = (np.zeros(3), np.array([0., 0., 0., 1.]))
    goal = (np.array([.03, -.02, .01]), -np.array([0., 0., np.sin(.1), np.cos(.1)]))
    plan = ResetTrajectory(start, goal, .02, .1, .05, .3, .15, .6)
    times = np.linspace(0, plan.duration, 2001)
    values = [plan.sample(t) for t in times]
    positions = np.array([p for p, q in values])
    rotations = np.array([quaternion_to_rotvec(quaternion_multiply(q, quaternion_inverse(start[1]))) for p, q in values])
    dt = times[1] - times[0]
    for samples, speed, acceleration in [(positions, .02, .05), (rotations, .1, .3)]:
        velocity = np.diff(samples, axis=0) / dt
        assert np.max(np.linalg.norm(velocity, axis=1)) <= speed * 1.0001
        assert np.max(np.linalg.norm(np.diff(velocity, axis=0) / dt, axis=1)) <= acceleration * 1.0001
    np.testing.assert_allclose(positions[0], start[0], atol=1e-12)
    np.testing.assert_allclose(positions[-1], goal[0], atol=1e-12)
    assert abs(np.dot(values[-1][1], goal[1])) == pytest.approx(1.)
    with pytest.raises(ValueError, match='过远'):
        ResetTrajectory(start, (np.ones(3), goal[1]), .02, .1, .05, .3, .15, .6)


def test_reset_reference_requires_matching_frames_and_valid_quaternion(tmp_path):
    path = tmp_path / 'reference.yaml'
    config = dict(reset_base_frame='panda_link0', reset_ee_frame='panda_hand_tcp',
                  reset_position_m=[.4, 0., .3], reset_orientation_xyzw=[0., 0., 0., 1.])
    path.write_text(yaml.safe_dump(config))
    load_reset_pose(path, 'panda_link0', 'panda_hand_tcp')
    with pytest.raises(ValueError, match='坐标系'):
        load_reset_pose(path, 'panda_link0', 'panda_hand')
    config['reset_orientation_xyzw'] = [0., 0., 0., 0.]
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match='位姿无效'):
        load_reset_pose(path, 'panda_link0', 'panda_hand_tcp')
