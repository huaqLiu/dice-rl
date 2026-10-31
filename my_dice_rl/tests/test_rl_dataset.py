"""Offline rollout reader checks; RolloutWriter creates all fixtures in tmp_path."""
import json

import h5py
import numpy as np
import pytest
import torch
from torch.utils.data import DataLoader

from my_dice_rl.agent.dataset.rl_dataset import RLDataset, collate_rl_batch
from my_dice_rl.util.recording import Sample
from my_dice_rl.util.rl_recording import RolloutWriter
from my_dice_rl.util.rotation import pose7_to_pose9


def write_episode(root, outcome='success', reason='manual stop', simulated=True, unsafe=False,
                  x=.4, no_commands=False):
    config = dict(task='gear_insertion', max_age_s=.2, max_sensor_skew_s=.08, policy_dt=.1,
                  collector=dict(base_frame='panda_link0', ee_frame='panda_hand_tcp'))
    writer = RolloutWriter(root, 'gear_insertion', config, dict(simulated=simulated), simulated)
    pose = np.array([x, 0., .3, 0., 0., 0., 1.])
    actions = np.tile(np.r_[pose7_to_pose9(pose), 0.], (4, 1))
    actions[:, 0] += .1  # Raw prediction intentionally differs from bounded published positions.
    actions[1, 9] = .08
    rgb = dict(wrist=np.full((224, 224, 3), [200, 20, 10], np.uint8),
               external=np.full((224, 224, 3), [10, 20, 200], np.uint8))
    command_ticks = np.array([] if no_commands else [10.02, 10.04, 10.14, 10.32, 10.34])
    if not no_commands:
        writer.put('plans', dict(tick=np.float64(10.01), latency=np.float64(.02), actions=actions,
            init_noise=np.arange(40, dtype=np.float32).reshape(4, 10),
            history_ticks=np.array([9.91, 10.01]), actual_pose=np.tile(pose, (2, 1)),
            actual_gripper=np.array([.035, .035])),
            {key: np.stack([image, image]) for key, image in rgb.items()})
        for i, tick in enumerate(command_ticks):
            plan_id, action_index = (0, i - 1) if i in (1, 2) else (-1, -1)
            requested = actions[action_index] if plan_id == 0 else np.r_[pose7_to_pose9(pose), 0.]
            command_pose = pose.copy()
            command_pose[0] += i * .001
            writer.put('commands', dict(tick=np.float64(tick), pose=command_pose,
                requested_action=requested.astype(np.float32), gripper_target=np.float32(.08 if i >= 3 else 0.),
                gripper_mode=np.uint8(1 if i >= 3 else 0), gripper_busy=np.uint8(i == 2),
                plan_id=np.int64(plan_id), action_index=np.int64(action_index)))
    for i, tick in enumerate([10., 10.1, 10.2, 10.3, 10.4]):
        actual = pose.copy()
        actual[0] += i * .001
        values = dict(**rgb, eef=actual, gripper=.035,
                      robot=dict(q=np.zeros(7), dq=np.zeros(7), wrench=np.zeros(6)))
        frame = {key: Sample(tick - .01, 1700000000. + tick, value) for key, value in values.items()}
        writer.observation(dict(tick=tick, **frame), int(np.searchsorted(command_ticks, tick, side='right')))
    writer.finish(outcome, reason, unsafe=unsafe)
    writer.worker.join(timeout=3.)
    assert writer.closed and not writer.error
    return writer.path


def test_transition_uses_actual_commands_and_preserves_plan_noise(tmp_path):
    path = write_episode(tmp_path)
    dataset = RLDataset(path, allow_demo=True, image_size=32)
    assert len(dataset) == 4
    first, second, hold, last = [dataset[i] for i in range(4)]
    assert first['obs']['camera_wrist'].shape == (2, 3, 32, 32)
    assert first['obs']['camera_wrist'][0, 0].mean() > first['obs']['camera_wrist'][0, 2].mean()
    assert first['obs']['camera_external'][0, 2].mean() > first['obs']['camera_external'][0, 0].mean()
    assert first['obs']['robot_eef_pose'].shape == (2, 9)
    np.testing.assert_allclose(first['obs']['robot_eef_pose'][:, 0], [.4, .4])
    np.testing.assert_allclose(first['next_obs']['robot_eef_pose'][:, 0], [.4, .401])
    assert first['obs_mask'].tolist() == [False, True]
    assert first['next_obs_mask'].tolist() == [True, True]
    assert first['obs']['gripper_width'].shape == (2, 1)
    assert first['action'].shape == (2, 10)
    np.testing.assert_allclose(first['action'][:, 0], [.4, .401])
    np.testing.assert_allclose(first['action'][:, 3:9], [[1., 0., 0., 0., 1., 0.]] * 2)
    assert second['commands']['index'].tolist() == [1, 2]
    assert second['commands']['is_new'].tolist() == [False, True]
    assert second['action'][-1, 9] == 0.  # Gripper request is pending, not yet accepted.
    assert second['commands']['requested_action'][-1, 9] == pytest.approx(.08)
    assert second['commands']['gripper_busy'][-1]
    assert hold['command_start'] == hold['command_end'] == 3
    assert hold['commands']['is_new'].tolist() == [False]
    for sample in [first, second, hold, last]:
        assert sample['commands']['duration'].min() >= 0
        assert (sample['commands']['duration'].sum() + sample['uncovered_dt']).item() == pytest.approx(.1)
    assert first['uncovered_dt'].item() == pytest.approx(.02)
    assert last['reward'].item() == 1. and last['terminated'] and not last['truncated']
    plan = dataset.get_plan(0, 0)
    assert plan['predicted_action'].shape == (4, 10)
    assert plan['predicted_action'][0, 0] == pytest.approx(.5)
    np.testing.assert_array_equal(plan['init_noise'], np.arange(40).reshape(4, 10))
    assert plan['obs']['camera_wrist'].shape == (2, 3, 32, 32)
    with pytest.raises(IndexError, match='hold'):
        dataset.get_plan(0, -1)


def test_collation_boundaries_and_spawn_workers(tmp_path):
    write_episode(tmp_path, x=.4)
    write_episode(tmp_path, x=.7, outcome='failed')
    dataset = RLDataset(tmp_path, allow_demo=True, image_size=16)
    assert len(dataset) == 8
    assert dataset[3]['terminated'] and dataset[4]['transition_index'] == 0
    np.testing.assert_allclose(dataset[4]['obs']['robot_eef_pose'][:, 0], [.7, .7])
    batch = collate_rl_batch([dataset[2], dataset[3]])
    assert batch['action'].shape == (2, 3, 10)
    assert batch['action_mask'].tolist() == [[True, False, False], [True, True, True]]
    assert batch['commands']['plan_id'][0, 1:].tolist() == [-1, -1]
    assert not batch['commands']['is_new'][0].any()
    loader = DataLoader(dataset, batch_size=3, num_workers=2, multiprocessing_context='spawn',
                        collate_fn=collate_rl_batch)
    batches = list(loader)
    assert sum(len(b['reward']) for b in batches) == 8
    assert all(b['obs']['camera_wrist'].shape[1:] == (2, 3, 16, 16) for b in batches)


@pytest.mark.parametrize('reason,truncated', [('manual stop', False), ('timeout', True)])
def test_failure_terminal_and_timeout_are_distinct(tmp_path, reason, truncated):
    dataset = RLDataset(write_episode(tmp_path, outcome='failed', reason=reason), allow_demo=True)
    last = dataset[-1]
    assert last['reward'].item() == 0.
    assert last['truncated'].item() == truncated
    assert last['terminated'].item() == (not truncated)


def test_ineligible_demo_and_partial_filtering(tmp_path):
    good = write_episode(tmp_path)
    write_episode(tmp_path, outcome='aborted', unsafe=True)
    with pytest.raises(ValueError, match='No eligible'):
        RLDataset(tmp_path)
    dataset = RLDataset(tmp_path, allow_demo=True)
    assert len(dataset) == 4 and len(dataset.skipped) == 1
    assert dataset.skipped[0]['reason'] == 'status=aborted'
    partial = good / 'rollout.partial.h5'
    partial.touch()
    with pytest.raises(ValueError, match='partial'):
        RLDataset(partial, allow_demo=True)
    metadata_path = good / 'metadata.json'
    metadata = json.loads(metadata_path.read_text())
    metadata['eligible_for_rl'] = False
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match='No eligible'):
        RLDataset(tmp_path, allow_demo=True)


def test_real_data_default_and_no_real_demo_mixing(tmp_path):
    real = write_episode(tmp_path, simulated=False)
    assert len(RLDataset(real / 'rollout.h5')) == 4
    write_episode(tmp_path)
    with pytest.raises(ValueError, match='mix real and simulated'):
        RLDataset(tmp_path, allow_demo=True)


@pytest.mark.parametrize('key,value,match', [
    ('transitions/next_obs_index', [2, 2, 3, 4], 'indices'),
    ('transitions/dt', [.2] * 4, 'dt disagrees'),
    ('transitions/reward', [1., 0., 0., 0.], 'Rewards'),
    ('observations/command_index', [0, 1, 3, 3, 5], 'command indices'),
    ('observations/receive/eef', [10.02] * 5, 'noncausal'),
    ('commands/plan_id', [-1, 5, 0, -1, -1], 'plan/action reference'),
    ('commands/action_index', [-1, 20, 1, -1, -1], 'prediction horizon'),
])
def test_corrupt_indices_timing_and_labels_fail_closed(tmp_path, key, value, match):
    path = write_episode(tmp_path)
    with h5py.File(path / 'rollout.h5', 'r+') as file:
        file[key][:] = value
    with pytest.raises(ValueError, match=match):
        RLDataset(path, allow_demo=True)


def test_invalid_quaternion_and_jpeg_rejected(tmp_path):
    path = write_episode(tmp_path)
    with h5py.File(path / 'rollout.h5', 'r+') as file:
        file['observations/pose'][0, 3:] = 0.
    with pytest.raises(ValueError, match='quaternion'):
        RLDataset(path, allow_demo=True)
    with h5py.File(path / 'rollout.h5', 'r+') as file:
        file['observations/pose'][0, 6] = 1.
        file['observations/rgb/wrist'][0, 0] = np.array([1, 2, 3], np.uint8)
    dataset = RLDataset(path, allow_demo=True)
    with pytest.raises(ValueError, match='Corrupt RGB'):
        dataset[0]


def test_empty_commands_and_legacy_dt_are_not_invented(tmp_path):
    path = write_episode(tmp_path, no_commands=True)
    with h5py.File(path / 'rollout.h5', 'r+') as file:
        del file['transitions/dt']
    dataset = RLDataset(path, allow_demo=True, image_size=16)
    sample = dataset[0]
    assert sample['action'].shape == (0, 10)
    assert sample['uncovered_dt'].item() == pytest.approx(.1)
    assert sample['commands']['duration'].numel() == 0
    batch = collate_rl_batch([sample, dataset[1]])
    assert batch['action'].shape == (2, 0, 10)
    assert batch['action_mask'].shape == (2, 0)


def test_equal_timestamp_respects_recorded_observation_command_order(tmp_path):
    path = write_episode(tmp_path)
    with h5py.File(path / 'rollout.h5', 'r+') as file:
        file['commands/tick'][0] = 10.  # Observation was saved first in this same control tick.
    dataset = RLDataset(path, allow_demo=True, image_size=16)
    sample = dataset[0]
    assert sample['commands']['is_new'][0]
    assert sample['uncovered_dt'].item() == 0.
    assert sample['commands']['duration'].sum().item() == pytest.approx(.1)
