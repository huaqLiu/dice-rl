import math
from typing import Iterable

import numpy as np


def apply_deadband(value: float, deadband: float, exponent: float = 1.0) -> float:
    """Apply a continuous deadband and response curve to a normalized axis."""
    value = float(np.clip(value, -1.0, 1.0))
    magnitude = abs(value)
    if magnitude <= deadband:
        return 0.0
    scaled = (magnitude - deadband) / (1.0 - deadband)
    return math.copysign(scaled**exponent, value)


def quaternion_normalize(quaternion: Iterable[float]) -> np.ndarray:
    quaternion = np.asarray(quaternion, dtype=np.float64)
    norm = np.linalg.norm(quaternion)
    if norm < 1e-12:
        raise ValueError("Cannot normalize a zero quaternion")
    return quaternion / norm


def quaternion_multiply(left: Iterable[float], right: Iterable[float]) -> np.ndarray:
    """Hamilton product for quaternions stored as [x, y, z, w]."""
    x1, y1, z1, w1 = quaternion_normalize(left)
    x2, y2, z2, w2 = quaternion_normalize(right)
    result = np.array(
        [
            w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
            w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
            w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        ],
        dtype=np.float64,
    )
    return quaternion_normalize(result)


def rotvec_to_quaternion(rotvec: Iterable[float]) -> np.ndarray:
    rotvec = np.asarray(rotvec, dtype=np.float64)
    angle = np.linalg.norm(rotvec)
    if angle < 1e-12:
        return np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float64)
    axis = rotvec / angle
    half_angle = 0.5 * angle
    return quaternion_normalize(
        np.concatenate((axis * math.sin(half_angle), [math.cos(half_angle)]))
    )


def quaternion_to_rotvec(quaternion: Iterable[float]) -> np.ndarray:
    quaternion = quaternion_normalize(quaternion)
    if quaternion[3] < 0.0:
        quaternion = -quaternion
    vector = quaternion[:3]
    vector_norm = np.linalg.norm(vector)
    if vector_norm < 1e-12:
        return np.zeros(3, dtype=np.float64)
    angle = 2.0 * math.atan2(vector_norm, quaternion[3])
    return vector / vector_norm * angle


def quaternion_inverse(quaternion: Iterable[float]) -> np.ndarray:
    quaternion = quaternion_normalize(quaternion)
    return np.array(
        [-quaternion[0], -quaternion[1], -quaternion[2], quaternion[3]],
        dtype=np.float64,
    )


def rotate_vector(quaternion: Iterable[float], vector: Iterable[float]) -> np.ndarray:
    quaternion = quaternion_normalize(quaternion)
    vector_quaternion = np.array([*np.asarray(vector, dtype=np.float64), 0.0])
    return quaternion_multiply_raw(
        quaternion_multiply_raw(quaternion, vector_quaternion),
        quaternion_inverse(quaternion),
    )[:3]


def quaternion_multiply_raw(left: Iterable[float], right: Iterable[float]) -> np.ndarray:
    """Hamilton product without normalizing, used when rotating vectors."""
    x1, y1, z1, w1 = np.asarray(left, dtype=np.float64)
    x2, y2, z2, w2 = np.asarray(right, dtype=np.float64)
    return np.array(
        [
            w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
            w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
            w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        ],
        dtype=np.float64,
    )


def clamp_vector_norm(vector: Iterable[float], maximum_norm: float) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float64)
    norm = np.linalg.norm(vector)
    if norm <= maximum_norm or norm < 1e-12:
        return vector
    return vector * (maximum_norm / norm)


