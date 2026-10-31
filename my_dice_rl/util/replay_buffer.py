"""CPU 经验回放：专家 reservoir 采样 + 在线 FIFO；状态可保存，组批时才传 GPU。"""
import copy

import numpy as np
import torch


class _Bank:
    def __init__(self, capacity, reservoir):
        self.capacity, self.reservoir = capacity, reservoir
        self.size, self.seen, self.pointer = 0, 0, 0
        self.arrays, self.references = {}, [''] * capacity

    def add(self, values, reference, rng):
        if not self.arrays:
            self.arrays = {k: np.empty((self.capacity, *v.shape), dtype=v.dtype) for k, v in values.items()}
        self.seen += 1
        if self.size < self.capacity:
            index = self.size
            self.size += 1
        elif self.reservoir:
            index = int(rng.integers(self.seen))
            if index >= self.capacity:
                return
        else:
            index = self.pointer
        for key, value in values.items():
            if key not in self.arrays or value.shape != self.arrays[key].shape[1:] or value.dtype != self.arrays[key].dtype:
                raise ValueError('Replay sample schema changed')
            self.arrays[key][index] = value
        self.references[index] = reference
        self.pointer = (index + 1) % self.capacity

    def state_dict(self):
        return dict(capacity=self.capacity, reservoir=self.reservoir, size=self.size,
                    seen=self.seen, pointer=self.pointer, references=self.references[:self.size],
                    arrays={k: v[:self.size].copy() for k, v in self.arrays.items()})

    def load_state_dict(self, state):
        if state['capacity'] != self.capacity or state['reservoir'] != self.reservoir:
            raise ValueError('Replay capacity/mode differs from checkpoint')
        self.size, self.seen, self.pointer = state['size'], state['seen'], state['pointer']
        self.arrays = {k: np.empty((self.capacity, *v.shape[1:]), dtype=v.dtype) for k, v in state['arrays'].items()}
        for key in self.arrays:
            self.arrays[key][:self.size] = state['arrays'][key]
        self.references[:self.size] = state['references']


class HybridReplayBuffer:
    def __init__(self, capacity=10000, expert_capacity=10000, expert_ratio=.5, seed=42):
        if min(capacity, expert_capacity) < 1 or not 0 <= expert_ratio <= 1:
            raise ValueError('Positive replay capacities and expert_ratio in [0,1] are required')
        self.online, self.expert = _Bank(capacity, False), _Bank(expert_capacity, True)
        self.expert_ratio = float(expert_ratio)
        self.rng = np.random.default_rng(seed)

    def add_batch(self, batch, expert, references=None):
        values = {k: v.detach().cpu().numpy() if isinstance(v, torch.Tensor) else np.asarray(v)
                  for k, v in batch.items()}
        count = len(values['features'])
        if count < 1 or any(len(v) != count or (v.dtype.kind in 'fc' and not np.isfinite(v).all()) for v in values.values()):
            raise ValueError('Invalid/nonfinite replay batch')
        required = {'features', 'next_features', 'action', 'mask', 'duration', 'reward', 'discount',
                    'terminated', 'truncated', 'dt', 'noise', 'noise_valid'}
        if set(values) != required:
            raise ValueError('Unexpected replay fields')
        if values['features'].ndim != 2 or values['next_features'].shape != values['features'].shape:
            raise ValueError('Replay feature shapes disagree')
        shape = values['action'].shape
        if len(shape) != 3 or shape[-1] != 10 or values['mask'].shape != shape[:2] or values['duration'].shape != shape[:2]:
            raise ValueError('Replay action/mask/duration shapes disagree')
        for key in ('reward', 'discount', 'terminated', 'truncated', 'dt', 'noise_valid'):
            if values[key].shape != (count, 1):
                raise ValueError('Replay scalar shape must be (B,1): ' + key)
        for key in ('mask', 'terminated', 'truncated', 'noise_valid'):
            if values[key].dtype != np.bool_:
                raise ValueError('Replay flags must be boolean: ' + key)
        if (values['noise'].ndim != 3 or values['noise'].shape[-1] != 10
                or np.any(values['dt'] <= 0) or np.any(values['duration'] < 0)
                or np.any(values['duration'][~values['mask']] != 0)
                or np.any(values['terminated'] & values['truncated'])):
            raise ValueError('Invalid replay timing/noise/termination flags')
        if np.any(values['discount'] < 0) or np.any(values['discount'] > 1):
            raise ValueError('Replay discount outside [0,1]')
        if not np.all(values['mask'].any(axis=1)):
            raise ValueError('Replay requires at least one valid action per sample')
        bank = self.expert if expert else self.online
        values['expert'] = np.full((count, 1), expert, dtype=bool)
        for existing in (self.online, self.expert):
            if existing.arrays and any(values[k].shape[1:] != existing.arrays[k].shape[1:]
                                       or values[k].dtype != existing.arrays[k].dtype for k in values):
                raise ValueError('Replay sample schema changed')
        for index in range(count):
            bank.add({k: v[index].copy() for k, v in values.items()},
                     references[index] if references is not None else '', self.rng)

    def __len__(self):
        return self.online.size + self.expert.size

    def sample(self, batch_size, device='cpu'):
        if batch_size < 1 or not len(self):
            raise ValueError('Cannot sample an empty replay or nonpositive batch')
        expert_count = round(batch_size * self.expert_ratio)
        if not self.online.size:
            expert_count = batch_size
        if not self.expert.size:
            expert_count = 0
        sections = []
        for bank, count in [(self.expert, expert_count), (self.online, batch_size - expert_count)]:
            if count:
                indices = self.rng.integers(bank.size, size=count)
                sections.append({k: v[indices] for k, v in bank.arrays.items()})
        order = self.rng.permutation(batch_size)
        return {k: torch.from_numpy(np.concatenate([s[k] for s in sections], axis=0)[order]).to(device)
                for k in sections[0]}

    def state_dict(self):
        return dict(online=self.online.state_dict(), expert=self.expert.state_dict(),
                    expert_ratio=self.expert_ratio, rng=copy.deepcopy(self.rng.bit_generator.state))

    def load_state_dict(self, state):
        if self.expert_ratio != state['expert_ratio']:
            raise ValueError('Replay expert ratio differs from checkpoint')
        self.online.load_state_dict(state['online'])
        self.expert.load_state_dict(state['expert'])
        self.rng.bit_generator.state = state['rng']
