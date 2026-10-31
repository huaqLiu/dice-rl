"""Bounded straight-line TCP reset trajectory; no ROS calls or obstacle planning."""
from pathlib import Path

import numpy as np
import yaml

from my_dice_rl.util.spacemouse_math import (
    quaternion_inverse, quaternion_multiply, quaternion_to_rotvec, rotvec_to_quaternion,
)


def load_reset_pose(path, base_frame, ee_frame):
    try:
        reference = yaml.safe_load(Path(path).read_text())
    except yaml.YAMLError as exc:
        raise ValueError('复位参考 YAML 无法解析') from exc
    if not isinstance(reference, dict):
        raise ValueError('复位参考配置必须是字段映射')
    if (reference.get('reset_base_frame') != base_frame
            or reference.get('reset_ee_frame') != ee_frame):
        raise ValueError('复位参考坐标系与当前 TCP 不一致')
    position = np.asarray(reference.get('reset_position_m'), dtype=float)
    orientation = np.asarray(reference.get('reset_orientation_xyzw'), dtype=float)
    if (position.shape != (3,) or orientation.shape != (4,)
            or not np.isfinite(position).all() or not np.isfinite(orientation).all()
            or abs(np.linalg.norm(orientation) - 1.) > .001):
        raise ValueError('复位参考位姿无效或尚未填写')
    return position, orientation / np.linalg.norm(orientation)


class ResetTrajectory:
    """Quintic translation and shortest quaternion rotation with zero endpoint rates."""

    def __init__(self, start, goal, linear_speed, angular_speed,
                 linear_acceleration, angular_acceleration, max_distance, max_angle):
        self.position, self.orientation = (np.asarray(item, dtype=float).copy() for item in start)
        self.delta = np.asarray(goal[0], dtype=float) - self.position
        self.rotation = quaternion_to_rotvec(quaternion_multiply(goal[1], quaternion_inverse(self.orientation)))
        distance, angle = np.linalg.norm(self.delta), np.linalg.norm(self.rotation)
        if distance > max_distance or angle > max_angle:
            raise ValueError(f'距复位参考过远：{distance * 1000:.1f} mm / {np.degrees(angle):.1f} 度；请先人工移近')
        # max(ds/du)=1.875, max(abs(d²s/du²))=10/sqrt(3).
        self.duration = max(1., 1.875 * distance / linear_speed, 1.875 * angle / angular_speed,
                            np.sqrt(10 / np.sqrt(3) * distance / linear_acceleration),
                            np.sqrt(10 / np.sqrt(3) * angle / angular_acceleration))

    def sample(self, elapsed):
        u = float(np.clip(elapsed / self.duration, 0., 1.))
        s = u ** 3 * (10 + u * (-15 + 6 * u))
        return (self.position + s * self.delta,
                quaternion_multiply(rotvec_to_quaternion(s * self.rotation), self.orientation))
