"""NumPy rotation conversions; quaternions XYZW, rotation6d = first two rows."""
import numpy as np


def quaternion_to_matrix(quaternion):
    q = np.asarray(quaternion, dtype=np.float64)
    if q.shape[-1] != 4 or not np.isfinite(q).all():
        raise ValueError("Expected finite XYZW quaternions")
    norm = np.linalg.norm(q, axis=-1, keepdims=True)
    if np.any(norm < 1e-12):
        raise ValueError("Zero quaternion")
    x, y, z, w = np.moveaxis(q / norm, -1, 0)
    return np.stack([
        1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w),
        2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w),
        2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y),
    ], axis=-1).reshape(q.shape[:-1] + (3, 3))


def pose7_to_pose9(pose):
    pose = np.asarray(pose, dtype=np.float64)
    if pose.shape[-1] != 7 or not np.isfinite(pose).all():
        raise ValueError("Expected finite XYZ+XYZW poses")
    rotation = quaternion_to_matrix(pose[..., 3:])[..., :2, :].reshape(pose.shape[:-1] + (6,))
    return np.concatenate([pose[..., :3], rotation], axis=-1).astype(np.float32)


def rotation6d_to_matrix(rotation):
    value = np.asarray(rotation, dtype=np.float64)
    if value.shape[-1] != 6 or not np.isfinite(value).all():
        raise ValueError("Expected finite rotation6d")
    first, second = value[..., :3], value[..., 3:]
    norm = np.linalg.norm(first, axis=-1, keepdims=True)
    if np.any(norm < 1e-8):
        raise ValueError("Degenerate rotation6d first row")
    first = first / norm
    second = second - np.sum(first * second, axis=-1, keepdims=True) * first
    norm = np.linalg.norm(second, axis=-1, keepdims=True)
    if np.any(norm < 1e-8):
        raise ValueError("Degenerate rotation6d second row")
    second = second / norm
    return np.stack([first, second, np.cross(first, second)], axis=-2)


def matrix_to_quaternion(matrix):
    """SO(3) -> normalized XYZW; stable near 180-degree rotations."""
    matrix = np.asarray(matrix, dtype=np.float64)
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        raise ValueError('Expected one finite 3x3 rotation matrix')
    if not np.allclose(matrix @ matrix.T, np.eye(3), atol=1e-5) or np.linalg.det(matrix) < .999:
        raise ValueError('Rotation matrix must be in SO(3)')
    trace = np.trace(matrix)
    if trace > 0:
        s = np.sqrt(trace+1.) * 2
        q = [(matrix[2, 1]-matrix[1, 2])/s, (matrix[0, 2]-matrix[2, 0])/s,
             (matrix[1, 0]-matrix[0, 1])/s, s/4]
    else:
        i = int(np.argmax(np.diag(matrix)))
        j, k = (i+1) % 3, (i+2) % 3
        s = np.sqrt(1+matrix[i, i]-matrix[j, j]-matrix[k, k]) * 2
        q = np.zeros(4)
        q[i], q[j], q[k] = s/4, (matrix[j, i]+matrix[i, j])/s, (matrix[k, i]+matrix[i, k])/s
        q[3] = (matrix[k, j]-matrix[j, k])/s
    q = np.asarray(q) / np.linalg.norm(q)
    return -q if q[3] < 0 else q


def quaternion_angle(left, right):
    left, right = np.asarray(left), np.asarray(right)
    dot = abs(np.dot(left/np.linalg.norm(left), right/np.linalg.norm(right)))
    return float(2*np.arccos(np.clip(dot, 0, 1)))


def interpolate_quaternion(left, right, fraction):
    left, right = np.asarray(left, dtype=np.float64), np.asarray(right, dtype=np.float64)
    left, right = left/np.linalg.norm(left), right/np.linalg.norm(right)
    dot = float(np.dot(left, right))
    if dot < 0:
        right, dot = -right, -dot
    if dot > .9995:
        result = left + fraction*(right-left)
    else:
        angle = np.arccos(np.clip(dot, -1, 1))
        result = (np.sin((1-fraction)*angle)*left + np.sin(fraction*angle)*right)/np.sin(angle)
    return result/np.linalg.norm(result)
