"""Atomic local checkpoints and offline policy loading; no robot interfaces."""
import os
from pathlib import Path
import random
import uuid

import numpy as np
import torch

CHECKPOINT_SCHEMA = 'dice_gear_dp_checkpoint_v1'


def rng_state():
    return dict(python=random.getstate(), numpy=np.random.get_state(),
                torch=torch.get_rng_state(),
                cuda=torch.cuda.get_rng_state_all() if torch.cuda.is_initialized() else [])


def restore_rng(state):
    random.setstate(state['python'])
    np.random.set_state(state['numpy'])
    torch.set_rng_state(state['torch'].cpu())
    if state['cuda'] and torch.cuda.is_available():
        torch.cuda.set_rng_state_all([item.cpu() for item in state['cuda']])


def atomic_save(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex[:8] + '.tmp')
    try:
        with temporary.open('wb') as stream:
            torch.save(state, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_checkpoint(path):
    # These training checkpoints contain optimizer and NumPy RNG state as well as tensors.
    state = torch.load(Path(path), map_location='cpu', weights_only=False)
    if state.get('schema') != CHECKPOINT_SCHEMA:
        raise ValueError('Not a my_dice_rl DP checkpoint')
    return state


def policy_model_config(config, weights):
    """旧包未记录state_dim时，从归一化权重还原，避免误用新默认架构。"""
    result = dict(config)
    dimension = int(weights['state_normalizer.center'].numel())
    if dimension not in (9, 10) or result.get('state_dim', dimension) != dimension:
        raise ValueError('Checkpoint state_dim disagrees with normalizer weights')
    result['state_dim'] = dimension
    action_dim = int(weights['action_normalizer.center'].numel())
    if action_dim not in (9, 10) or result.get('action_dim', action_dim) != action_dim:
        raise ValueError('Checkpoint action_dim disagrees with normalizer weights')
    result['action_dim'] = action_dim
    from my_dice_rl.util.cameras import camera_keys
    stored = tuple(key for key in camera_keys({'camera_names': ['wrist', 'external', 'side']})
                   if any(name.startswith('obs_encoder.backbones.' + key + '.') for name in weights))
    if not stored or (result.get('camera_keys') is not None and tuple(result['camera_keys']) != stored):
        raise ValueError('Checkpoint cameras disagree with visual backbone weights')
    result['camera_keys'] = list(stored)
    return result


def load_policy(path, device='cpu', use_ema=True):
    from my_dice_rl.model.diffusion.diffusion_policy import DiffusionPolicy
    state = load_checkpoint(path)
    weights = state['ema']['model'] if use_ema else state['model']
    kwargs = {**policy_model_config(state['model_config'], weights), 'pretrained_vision': False}
    model = DiffusionPolicy(**kwargs)
    model.load_state_dict(weights)
    return model.to(device).eval(), state


def move_to_device(value, device):
    if isinstance(value, torch.Tensor):
        return value.to(device, non_blocking=True)
    if isinstance(value, dict):
        return {key: move_to_device(item, device) for key, item in value.items()}
    return value
