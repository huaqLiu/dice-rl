"""Fixed-reference randomization and withdrawal order, without robot interfaces."""
import numpy as np

from my_dice_rl.util.drag_reset import random_reset_goal, reset_waypoints
from my_dice_rl.util.rotation import quaternion_angle


def test_random_targets_stay_within_fixed_position_box_and_total_angle():
    reference = (np.array([.5, -.02, .16]), np.array([1., 0., 0., 0.]))
    saved = [v.copy() for v in reference]
    rng = np.random.default_rng(12)
    goals = np.array([random_reset_goal(reference, .02, 10., rng) for _ in range(1000)])
    delta = goals[:, :3]-reference[0]
    assert np.all(np.abs(delta) <= .02)
    assert np.all(delta.min(0) < -.018) and np.all(delta.max(0) > .018)
    assert all(quaternion_angle(goal[3:], reference[1]) <= np.deg2rad(10.)+1e-10 for goal in goals)
    np.testing.assert_allclose(np.linalg.norm(goals[:, 3:], axis=1), 1.)
    for original, current in zip(saved, reference):
        np.testing.assert_array_equal(original, current)


def test_retract_is_upward_at_current_xy_and_orientation_before_random_transfer():
    reference = (np.array([.5, 0., .16]), np.array([1., 0., 0., 0.]))
    actual = np.r_[.49, .01, .059, 1., 0., 0., 0.]
    goal = random_reset_goal(reference, .02, 10., np.random.default_rng(4))
    stages = reset_waypoints(actual, reference, goal, .02, .01)
    retract, above, final = [v for _, v in stages]
    np.testing.assert_array_equal(retract[:2], actual[:2])
    np.testing.assert_array_equal(retract[3:], actual[3:])
    assert retract[2] >= .19-1e-12
    assert above[2] == retract[2] and above[2] >= final[2]
    np.testing.assert_array_equal(above[:2], goal[:2])
    np.testing.assert_array_equal(above[3:], goal[3:])
    np.testing.assert_array_equal(final, goal)
