"""Zarr data stays on disk; split complete episodes before fitting normalization."""
from pathlib import Path

import cv2
import numpy as np
import torch
from torch.utils.data import Dataset
import zarr

from my_dice_rl.agent.dataset.sequence_sampler import SequenceSampler

DATASET_SCHEMA = 'dice_gear_dp_zarr_v1'


def split_episodes(count, val_ratio=.2, seed=42):
    if count < 1 or not 0 <= val_ratio < 1:
        raise ValueError('Require at least one episode and 0 <= val_ratio < 1')
    order = np.random.default_rng(seed).permutation(count)
    n_val = min(count-1, max(1, round(count*val_ratio))) if val_ratio and count > 1 else 0
    return sorted(order[n_val:].tolist()), sorted(order[:n_val].tolist())


class GearDataset(Dataset):
    def __init__(self, path, episode_ids=None, obs_steps=2, horizon_steps=20,
                 stride=1, image_size=224, allow_demo=False):
        self.path = str(Path(path).expanduser().resolve())
        if not Path(self.path).is_dir():
            raise FileNotFoundError(f'Processed dataset missing: {self.path}; run process_gear_dataset first')
        root = zarr.open_group(self.path, mode='r')
        self.metadata = dict(root.attrs)
        if self.metadata.get('schema') != DATASET_SCHEMA or not self.metadata.get('complete'):
            raise ValueError('Dataset is incomplete or has a different schema')
        if self.metadata.get('simulated') and not allow_demo:
            raise ValueError('Simulated data requires task.allow_demo=true')
        self.episode_ends = np.asarray(root['meta/episode_ends'][:], dtype=np.int64)
        if not len(self.episode_ends):
            raise ValueError('Dataset contains no episodes')
        count = int(self.episode_ends[-1])
        for key, shape in [('robot_eef_pose', (9,)), ('gripper_width', (1,)), ('action', (10,))]:
            if root['data/' + key].shape != (count, *shape):
                raise ValueError(f'Invalid dataset shape: {key}')
        for key in ('camera_wrist', 'camera_external'):
            if root['data/' + key].shape != (count, 224, 224, 3):
                raise ValueError(f'Expected 224x224 RGB: {key}')
        self.episode_ids = (list(range(len(self.episode_ends))) if episode_ids is None
                            else list(episode_ids))
        self.sampler = SequenceSampler(self.episode_ends, self.episode_ids, obs_steps,
                                       horizon_steps, stride)
        self.image_size = image_size
        self._root = None

    @property
    def root(self):
        if self._root is None:
            self._root = zarr.open_group(self.path, mode='r')
        return self._root

    def __getstate__(self):
        state = self.__dict__.copy()
        state['_root'] = None  # Each DataLoader worker opens its own read-only store.
        return state

    def __len__(self):
        return len(self.sampler)

    def read(self, key, indices):
        # Indices are contiguous except repeated padding at either boundary.
        low, high = int(indices.min()), int(indices.max())
        return self.root['data/' + key][low:high+1][indices-low]

    def __getitem__(self, index):
        episode, anchor, obs_indices, act_indices, valid = self.sampler[index]
        obs = {}
        for key in ('camera_wrist', 'camera_external'):
            rgb = self.read(key, obs_indices)
            if self.image_size != 224:
                rgb = np.stack([cv2.resize(im, (self.image_size, self.image_size),
                                          interpolation=cv2.INTER_AREA) for im in rgb])
            obs[key] = torch.from_numpy(rgb.transpose(0, 3, 1, 2).copy()).float() / 255.
        for key in ('robot_eef_pose', 'gripper_width'):
            obs[key] = torch.from_numpy(self.read(key, obs_indices).astype(np.float32))
        return dict(obs=obs, action=torch.from_numpy(self.read('action', act_indices).astype(np.float32)),
                    valid_mask=torch.from_numpy(valid), episode_id=episode, frame_index=anchor)

    def normalizer_data(self):
        """Only this dataset's selected (training) episodes contribute statistics."""
        starts = np.r_[0, self.episode_ends[:-1]]
        states, actions = [], []
        for episode in self.episode_ids:
            section = slice(int(starts[episode]), int(self.episode_ends[episode]))
            pose = self.root['data/robot_eef_pose'][section]
            gripper = self.root['data/gripper_width'][section]
            states.append(np.concatenate([pose, gripper], axis=-1))
            actions.append(self.root['data/action'][section])
        if not states:
            raise ValueError('Cannot fit normalization on an empty split')
        return (torch.from_numpy(np.concatenate(states).astype(np.float32)),
                torch.from_numpy(np.concatenate(actions).astype(np.float32)))


def make_datasets(task, image_size=224):
    kwargs = dict(path=task['dataset_path'], obs_steps=task['obs_steps'],
                  horizon_steps=task['horizon_steps'], stride=task['sample_stride'],
                  image_size=image_size, allow_demo=task['allow_demo'])
    whole = GearDataset(**kwargs)
    metadata = whole.metadata
    if metadata['task'] != task['name']:
        raise ValueError('Dataset task disagrees with task configuration')
    for key in ('base_frame', 'ee_frame'):
        if metadata[key] != task[key]:
            raise ValueError(f'Dataset {key} disagrees with task configuration')
    if not np.isclose(metadata['record_hz'], task['record_hz']):
        raise ValueError('Dataset sampling frequency disagrees with task configuration')
    if not np.isclose(metadata['hold_target_width'], task['hold_target_width']):
        raise ValueError('Dataset hold target width disagrees with task configuration')
    train_ids, val_ids = split_episodes(len(whole.episode_ends), task['val_ratio'], task['split_seed'])
    return GearDataset(episode_ids=train_ids, **kwargs), GearDataset(episode_ids=val_ids, **kwargs)
