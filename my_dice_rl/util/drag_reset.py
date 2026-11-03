"""Random targets around a fixed reference and staged upward withdrawal; no ROS."""
import numpy as np

from my_dice_rl.util.spacemouse_math import quaternion_multiply, rotvec_to_quaternion


def random_reset_goal(reference, position_noise_m, rotation_noise_deg, rng):
    position, orientation = (np.asarray(v, dtype=float).copy() for v in reference)
    delta = rng.uniform(-position_noise_m, position_noise_m, 3)
    axis = rng.normal(size=3)
    axis /= max(np.linalg.norm(axis), 1e-12)
    rotvec = axis*rng.uniform(0., np.deg2rad(rotation_noise_deg))
    return np.r_[position+delta, quaternion_multiply(rotvec_to_quaternion(rotvec), orientation)]


def reset_waypoints(actual, reference, goal, position_noise_m, clearance_m):
    # Clear the top of the entire randomized starting-height range before XY/rotation.
    clearance = max(actual[2], reference[0][2]+position_noise_m+clearance_m, goal[2])
    retract = np.asarray(actual, dtype=float).copy()
    retract[2] = clearance
    above = np.asarray(goal, dtype=float).copy()
    above[2] = clearance
    return [('沿基坐标+Z退轴', retract), ('在退轴高度调整XY和姿态', above),
            ('下降到随机起点', np.asarray(goal, dtype=float).copy())]
