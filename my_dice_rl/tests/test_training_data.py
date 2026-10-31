import json

import h5py
import numpy as np
import pytest
import torch

pytest.importorskip('zarr')

from my_dice_rl.agent.dataset.gear_dataset import GearDataset, split_episodes
from my_dice_rl.agent.dataset.sequence_sampler import SequenceSampler
from my_dice_rl.model.common.normalizer import FeatureNormalizer
from my_dice_rl.script.process_gear_dataset import convert_dataset
from my_dice_rl.util.rotation import pose7_to_pose9, quaternion_to_matrix, rotation6d_to_matrix


def test_rotation_row_convention_and_quaternion_sign():
    q = np.array([.2, .3, .4, .5])
    matrix = quaternion_to_matrix(q)
    pose = pose7_to_pose9(np.r_[.4, .1, .3, q])
    np.testing.assert_allclose(rotation6d_to_matrix(pose[3:]), matrix, atol=1e-6)
    np.testing.assert_allclose(quaternion_to_matrix(-q), matrix)
    np.testing.assert_allclose(matrix @ matrix.T, np.eye(3), atol=1e-6)


def test_sampler_never_crosses_episode_boundaries():
    sampler = SequenceSampler([3, 6], [0, 1], obs_steps=2, horizon_steps=4)
    episode, anchor, obs, action, mask = sampler[2]
    assert episode == 0 and anchor == 2
    np.testing.assert_equal(obs, [1, 2])
    np.testing.assert_equal(action, [2, 2, 2, 2])
    np.testing.assert_equal(mask, [True, False, False, False])
    episode, anchor, obs, action, _ = sampler[3]
    assert episode == 1 and anchor == 3
    np.testing.assert_equal(obs, [3, 3])
    np.testing.assert_equal(action, [3, 4, 5, 5])


def test_export_gripper_modes_and_no_validation_leakage(collected_episodes, tmp_path):
    with pytest.raises(ValueError, match='No eligible'):
        convert_dataset(collected_episodes, tmp_path/'blocked.zarr')
    output = tmp_path/'gear.zarr'
    report = convert_dataset(collected_episodes, output, allow_demo=True)
    assert report['episodes'] == 3 and report['frames'] == 60
    with pytest.raises(ValueError, match='Simulated'):
        GearDataset(output)
    train_ids, val_ids = split_episodes(3, .2, seed=42)
    assert not set(train_ids) & set(val_ids)
    train = GearDataset(output, episode_ids=train_ids, horizon_steps=8, allow_demo=True)
    validation = GearDataset(output, episode_ids=val_ids, horizon_steps=8, allow_demo=True)
    states, actions = train.normalizer_data()
    normalizer = FeatureNormalizer(10)
    normalizer.fit(states)
    assert float(states[:, 0].max()) < .6
    assert float(validation[0]['obs']['robot_eef_pose'][-1, 0]) > .89
    assert float(normalizer.center[0]) < .6
    sample = train[0]
    assert sample['obs']['camera_wrist'].shape == (2, 3, 224, 224)
    assert sample['action'].shape == (8, 10)
    assert sample['obs']['camera_wrist'][:, 0].mean() > .65
    assert float(sample['action'][0, -1]) == 0.  # Registered hold -> closed label.
    assert float(sample['obs']['gripper_width'][0, 0]) == pytest.approx(.035)
    assert float(train.root['data/gripper_recorded_target_width'][0, 0]) == pytest.approx(.035)
    assert float(actions[:, -1].max()) == pytest.approx(.08)
    with pytest.raises(FileExistsError):
        convert_dataset(collected_episodes, output, allow_demo=True)


def test_corrupt_successful_trajectory_is_rejected_atomically(collected_episodes, tmp_path):
    path = next(collected_episodes.glob('*/episode_*/trajectory.h5'))
    with h5py.File(path, 'r+') as file:
        value = file['timestamp'][2]
        file['timestamp'][2] = value + .03
    output = tmp_path/'broken.zarr'
    with pytest.raises(ValueError, match='sample times'):
        convert_dataset(collected_episodes, output, allow_demo=True)
    assert not output.exists()
    assert not list(tmp_path.glob('broken.zarr.partial-*'))
