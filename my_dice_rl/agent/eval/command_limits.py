"""Validate absolute goals and interpolate bounded Cartesian commands."""
import numpy as np

from my_dice_rl.util.rotation import (rotation6d_to_matrix, matrix_to_quaternion,
    quaternion_angle, interpolate_quaternion)


def check_observation(frame, config):
    robot = frame['robot'].value
    wrench = np.asarray(robot['wrench'])
    if wrench.shape != (6,) or not np.isfinite(wrench).all():
        raise ValueError('外力估计无效')
    if np.linalg.norm(wrench[:3]) > config['max_force'] or np.linalg.norm(wrench[3:]) > config['max_torque']:
        raise ValueError('接触力/力矩超过限制')
    if robot.get('unsafe', False) or robot.get('mode', 2) not in (1, 2):
        raise ValueError('机器人报告错误、碰撞或非执行模式')


def limit_command(action, previous, actual, reset, dt, config):
    action = np.asarray(action, dtype=np.float64)
    if action.shape != (10,) or not np.isfinite(action).all() or not 0 <= action[9] <= .08:
        raise ValueError('策略动作必须是有限的10维值，夹爪范围0–0.08米')
    rotation = matrix_to_quaternion(rotation6d_to_matrix(action[3:9]))
    goal = np.r_[action[:3], rotation]
    if (np.linalg.norm(goal[:3]-reset[:3]) > config['max_translation_from_reset']
            or np.linalg.norm(actual[:3]-reset[:3]) > config['max_translation_from_reset']
            or quaternion_angle(goal[3:], reset[3:]) > config['max_rotation_from_reset']):
        raise ValueError('策略超出本轮复位位置的允许范围')
    if (np.linalg.norm(goal[:3]-actual[:3]) > config['max_goal_position_error']
            or quaternion_angle(goal[3:], actual[3:]) > config['max_goal_orientation_error']):
        raise ValueError('预测目标离实际末端过远')
    if (np.linalg.norm(previous[:3]-actual[:3]) > config['max_position_error']
            or quaternion_angle(previous[3:], actual[3:]) > config['max_orientation_error']):
        raise ValueError('末端跟踪误差超限')
    delta = goal[:3]-previous[:3]
    norm = np.linalg.norm(delta)
    position = previous[:3] + delta * min(1., config['max_linear_speed']*dt/max(norm, 1e-12))
    angle = quaternion_angle(previous[3:], goal[3:])
    fraction = min(1., config['max_angular_speed']*dt/max(angle, 1e-12))
    return np.r_[position, interpolate_quaternion(previous[3:], goal[3:], fraction)]
