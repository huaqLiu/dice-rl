"""No devices: DICE gradients, SMDP targets, replay persistence and process coordination."""
import copy
import json
import multiprocessing

import numpy as np
import pytest
import torch
from torch import nn

from my_dice_rl.agent.dataset.rl_chunks import returns, OnlineChunks, ExpertChunks
from my_dice_rl.agent.dataset.gear_dataset import make_datasets
from my_dice_rl.agent.dataset.rl_dataset import RLDataset
from my_dice_rl.model.common.normalizer import FeatureNormalizer
from my_dice_rl.model.rl.distill_rl import DistillRL
from my_dice_rl.script.process_gear_dataset import convert_dataset
from my_dice_rl.tests.test_rl_dataset import write_episode
from my_dice_rl.util.replay_buffer import HybridReplayBuffer
from my_dice_rl.util.rl_config import load_rl_config, require_review
from my_dice_rl.util.rl_coordination import DeviceLease


class TinyTeacher(nn.Module):
    def __init__(self):
        super().__init__()
        self.obs_steps, self.horizon_steps = 1, 4
        self.obs_encoder = nn.Identity()
        self.obs_encoder.feature_dim = 4
        self.mapping = nn.Linear(4, 40)
        self.action_normalizer = FeatureNormalizer(10)
        self.action_normalizer.fit(torch.tensor([[-1.] * 10, [1.] * 10]))

    def predict_action_from_features(self, feature, init_noise):
        return dict(action_pred=(self.mapping(feature).reshape(-1, 4, 10) * .1 + init_noise * .05).tanh())


def mini_batch(count=4):
    return dict(features=torch.randn(count, 4), next_features=torch.randn(count, 4),
        action=torch.randn(count, 2, 10), mask=torch.ones(count, 2, dtype=torch.bool),
        duration=torch.ones(count, 2), reward=torch.ones(count, 1), discount=torch.full((count, 1), .99),
        terminated=torch.zeros(count, 1, dtype=torch.bool), truncated=torch.zeros(count, 1, dtype=torch.bool),
        dt=torch.full((count, 1), .2), noise=torch.randn(count, 4, 10),
        noise_valid=torch.ones(count, 1, dtype=torch.bool))


def test_residual_initialization_gradients_targets_and_constant_dimension():
    config = load_rl_config(require_paths=False)['model']
    config.update(chunk_steps=2, hidden_dims=[16, 16], ensemble_size=2, multi_z=2, filter_warmup=0)
    model = DistillRL(TinyTeacher(), config)
    batch = mini_batch()
    batch['expert'] = torch.tensor([[True], [False], [True], [False]])
    noise = torch.randn(4, 4, 10)
    action, base = model.action(batch['features'], noise)
    torch.testing.assert_close(action, base, rtol=0, atol=0)
    before = {key: value.clone() for key, value in model.teacher.state_dict().items()}
    critic_loss, _ = model.critic_loss(batch)
    critic_loss.backward()
    assert any(p.grad is not None for p in model.critic.parameters())
    model.critic.requires_grad_(False)
    loss, metrics = model.actor_loss(batch, 1)
    loss.backward()
    assert torch.isfinite(loss) and model.actor.network[-1].weight.grad.abs().sum() > 0
    assert all(p.grad is None and not p.requires_grad for p in model.teacher.parameters())
    assert 0 <= metrics['contraction_fraction'] <= 1
    for key, value in model.teacher.state_dict().items():
        torch.testing.assert_close(value, before[key], rtol=0, atol=0)
    with torch.no_grad():
        model.actor.network[-1].bias.fill_(.1)
        model.teacher.action_normalizer.constant[-1] = True
        action, base = model.action(batch['features'], noise)
        torch.testing.assert_close(action[..., -1], base[..., -1])
        model.update_targets(.5)
    torch.testing.assert_close(model.target_actor.network[-1].bias, model.actor.network[-1].bias * .5)


def test_td_bootstraps_truncation_and_time_discount():
    target = DistillRL.td_target(torch.tensor([[1.], [0.]]), torch.tensor([[.9], [.9]]),
                                torch.tensor([[True], [False]]), torch.tensor([[4.], [4.]]))
    torch.testing.assert_close(target, torch.tensor([[1.], [3.6]]))
    reward, discount = returns(np.array([0., 1.]), np.array([.1, .2]), .9, .1)
    assert reward == pytest.approx(.9) and discount == pytest.approx(.9 ** 3)


def test_chunk_reconstruction_and_expert_boundaries(tmp_path, collected_episodes):
    path = write_episode(tmp_path / 'online')
    raw = RLDataset(path, allow_demo=True, image_size=32)
    reward = dict(success=1., failure=0., timeout=0., step_penalty=0.)
    data = OnlineChunks(raw, 3, 4, .1, .9, reward, stride=1)
    sample = data[0]
    # 使用区间中点的实际目标，并非 plans/actions 中的 0.5m 原预测。
    np.testing.assert_allclose(sample['action'][:, 0], [.401, .402, .402])
    assert sample['noise_valid'].item()
    last = data[-1]
    assert last['mask'].tolist() == [True, False, False] and last['terminated']
    assert last['reward'].item() == 1.
    import h5py
    with h5py.File(path / 'rollout.h5', 'r+') as file:
        file['commands/tick'][2] = 10.2  # 新指令刚好在区间结束处，不能当成此前已执行。
    boundary = OnlineChunks(RLDataset(path, allow_demo=True, image_size=32), 3, 4, .1, .9, reward)
    assert boundary[0]['action'][1, 0].item() == pytest.approx(.401)
    output = tmp_path / 'expert.zarr'
    convert_dataset(collected_episodes, output, allow_demo=True)
    task = dict(name='gear_insertion', dataset_path=str(output), obs_steps=2, horizon_steps=8,
                sample_stride=1, allow_demo=True, base_frame='panda_link0', ee_frame='panda_hand_tcp',
                record_hz=10., hold_target_width=0., val_ratio=.34, split_seed=42)
    train, _ = make_datasets(task, image_size=32)
    expert = ExpertChunks(train, 4, 8, .1, .9, reward, stride=1)
    for i, (_, start, end, high) in enumerate(expert.indices):
        assert end < high and end > start
        value = expert[i]
        assert int(value['mask'].sum()) == end - start
        if end == high - 1:
            assert value['terminated'] and value['reward'].item() == pytest.approx(.9 ** (end - start - 1))


def test_replay_mixture_fifo_restore_and_validation():
    replay = HybridReplayBuffer(4, 4, expert_ratio=.25, seed=2)
    batch = mini_batch(6)
    batch['features'][:, 0] = torch.arange(6)
    replay.add_batch(batch, expert=False)
    replay.add_batch(batch, expert=True)
    assert len(replay) == 8
    sample = replay.sample(8)
    assert int(sample['expert'].sum()) == 2
    assert set(sample['features'][~sample['expert'].flatten(), 0].tolist()) <= {2., 3., 4., 5.}
    state = replay.state_dict()
    restored = HybridReplayBuffer(4, 4, .25, 999)
    restored.load_state_dict(state)
    left, right = replay.sample(12), restored.sample(12)
    for key in left:
        torch.testing.assert_close(left[key], right[key], rtol=0, atol=0)
    for invalid in ['nan', 'shape', 'flags']:
        bad = copy.deepcopy(batch)
        if invalid == 'nan':
            bad['features'][0, 0] = float('nan')
        elif invalid == 'shape':
            bad['action'] = bad['action'][:, :, :9]
        else:
            bad['terminated'][0] = bad['truncated'][0] = True
        with pytest.raises(ValueError):
            replay.add_batch(bad, expert=False)
        assert len(replay) == 8


def _probe_lock(path, queue):
    lease = DeviceLease(path)
    queue.put(lease.acquire())
    lease.release()


def test_device_lease_excludes_other_process(tmp_path):
    context = multiprocessing.get_context('spawn')
    queue = context.Queue()
    path = str(tmp_path / 'device.lock')
    lease = DeviceLease(path)
    for expected in [False, True]:
        if not expected:
            assert lease.acquire()
        else:
            lease.release()
        child = context.Process(target=_probe_lock, args=(path, queue))
        child.start()
        assert queue.get(timeout=10.) is expected
        child.join(timeout=10.)
        assert child.exitcode == 0
    queue.close()


def test_config_draft_and_invalid_settings():
    config = load_rl_config(require_paths=False)
    with pytest.raises(ValueError, match='统一确认'):
        require_review(config['review'])
    for override in ['training.gamma=1.2', 'training.gamma=true', 'training.batch_size=0', 'model.residual_limit=[0.1]',
                     'model.target_subset=99', 'model.td_loss=invalid', 'data.allow_demo=1']:
        with pytest.raises(ValueError):
            load_rl_config(overrides=[override], require_paths=False)


def test_manual_success_review_allows_optional_tolerance_and_hold():
    review = dict(confirmed=True, success_definition='人工判定插入约 15 mm',
        success_insertion_depth_mm=15., success_position_tolerance_mm=None,
        success_hold_seconds=None, payload_mass_kg=1.,
        payload_center_of_mass_m=[0., 0., .03], reset_position_m=[.4, 0., .3],
        reset_orientation_xyzw=[0., 0., 0., 1.])
    require_review(review)
    require_review({**review, 'success_hold_seconds': 0.})
    for key in ('success_position_tolerance_mm', 'success_hold_seconds'):
        with pytest.raises(ValueError, match=key):
            require_review({**review, key: -1.})
    with pytest.raises(ValueError, match='payload_center_of_mass_m'):
        require_review({**review, 'payload_center_of_mass_m': None})
