"""TCP Z 轴沿基坐标 ±Z，保留最近的绕轴转角；动作旋转使用前两行 6D。"""
import numpy as np
import torch
from torch.nn import functional as F


def orientation_config(value=None):
    value = dict(value or {})
    if set(value) - {'mode', 'tool_z_sign', 'translation_hold_tilt_rad'}:
        raise ValueError('Unknown orientation_constraint setting')
    mode = value.get('mode', 'none')
    if mode not in ('none', 'vertical_keep_yaw'):
        raise ValueError('Unknown orientation_constraint.mode')
    sign = value.get('tool_z_sign', -1)
    tilt = value.get('translation_hold_tilt_rad', np.deg2rad(2.))
    if isinstance(sign, bool) or sign not in (-1, 1):
        raise ValueError('tool_z_sign must be -1 or +1')
    if isinstance(tilt, bool) or not np.isfinite(tilt) or not 0 < tilt < np.pi/2:
        raise ValueError('translation_hold_tilt_rad must be in (0, pi/2)')
    return dict(mode=mode, tool_z_sign=int(sign), translation_hold_tilt_rad=float(tilt))


def vertical_rotation(matrix, sign=-1):
    """最近竖直旋转，退化时采用工具 X 轴的水平投影。"""
    u, v = matrix[0, 0] + sign*matrix[1, 1], matrix[1, 0] - sign*matrix[0, 1]
    if np.hypot(u, v) < 1e-8:
        u, v = matrix[0, 0], matrix[1, 0]
    if np.hypot(u, v) < 1e-8:
        u, v = 1., 0.
    c, s = np.array([u, v]) / np.hypot(u, v)
    return np.array([[c, -sign*s, 0.], [s, sign*c, 0.], [0., 0., sign]])


def constrain_action_tensor(action, actual_state, config):
    """可微物理动作投影；actual_state 为最后观测的位姿9维（旧版可带夹爪）。"""
    if config['mode'] == 'none':
        return action
    sign = config['tool_z_sign']
    rows = action[..., 3:9].reshape(*action.shape[:-1], 2, 3)
    a = F.normalize(rows[..., 0, :], dim=-1, eps=1e-8)
    b = rows[..., 1, :] - (a*rows[..., 1, :]).sum(-1, keepdim=True)*a
    b = F.normalize(b, dim=-1, eps=1e-8)
    uv = torch.stack([a[..., 0]+sign*b[..., 1], b[..., 0]-sign*a[..., 1]], -1)
    fallback = torch.stack([a[..., 0], b[..., 0]], -1)
    fixed = torch.zeros_like(uv)
    fixed[..., 0] = 1
    fallback = torch.where(fallback.norm(dim=-1, keepdim=True) > 1e-8, fallback, fixed)
    uv = torch.where(uv.norm(dim=-1, keepdim=True) > 1e-8, uv, fallback)
    cs = F.normalize(uv, dim=-1, eps=1e-8)
    c, s = cs.unbind(-1)
    zero = torch.zeros_like(c)
    rotation = torch.stack([c, -sign*s, zero, s, sign*c, zero], -1)
    # 旋转6D的前两行叉积得到第三行，其 zz 分量等于 R[2,2]。
    actual_rows = actual_state[..., 3:9].reshape(*actual_state.shape[:-1], 2, 3)
    aa = F.normalize(actual_rows[..., 0, :], dim=-1, eps=1e-8)
    bb = actual_rows[..., 1, :] - (aa*actual_rows[..., 1, :]).sum(-1, keepdim=True)*aa
    bb = F.normalize(bb, dim=-1, eps=1e-8)
    aligned = sign*torch.cross(aa, bb, dim=-1)[..., 2] >= np.cos(config['translation_hold_tilt_rad'])
    position = torch.where(aligned[..., None, None], action[..., :3], actual_state[..., None, :3])
    return torch.cat([position, rotation, action[..., 9:]], -1)
