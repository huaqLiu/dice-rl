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


def load_policy(path, device='cpu', use_ema=True):
    from my_dice_rl.model.diffusion.diffusion_policy import DiffusionPolicy
    state = load_checkpoint(path)
    kwargs = {**state['model_config'], 'pretrained_vision': False}
    model = DiffusionPolicy(**kwargs)
    weights = state['ema']['model'] if use_ema else state['model']
    model.load_state_dict(weights)
    return model.to(device).eval(), state


def move_to_device(value, device):
    if isinstance(value, torch.Tensor):
        return value.to(device, non_blocking=True)
    if isinstance(value, dict):
        return {key: move_to_device(item, device) for key, item in value.items()}
    return value
