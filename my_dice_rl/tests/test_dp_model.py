"""Offline model checks; no ROS imports, devices, or robot commands."""
import copy

import pytest
import torch

pytest.importorskip("diffusers", reason="Use the separate DP training environment")

from my_dice_rl.model.common.normalizer import FeatureNormalizer
from my_dice_rl.model.diffusion.conditional_unet1d import ConditionalUnet1D
from my_dice_rl.model.diffusion.diffusion_policy import DiffusionPolicy


@pytest.fixture
def example():
    torch.manual_seed(3)
    # Small channels and images exercise the full architecture quickly on CPU.
    torch.set_num_threads(2)
    model = DiffusionPolicy(image_size=64, horizon_steps=8, act_steps=3,
                            down_dims=(16, 32), time_embed_dim=16,
                            train_diffusion_steps=8, inference_steps=3)
    states = torch.randn(20, 10)
    actions = torch.randn(20, 10)
    states[:, -1] = torch.linspace(.01, .07, 20)
    actions[:, -1] = torch.linspace(.0, .08, 20)
    model.fit_normalizer(states, actions)
    obs = {
        'camera_wrist': torch.rand(1, 2, 3, 64, 64),
        'camera_external': torch.rand(1, 2, 3, 64, 64),
        'robot_eef_pose': states[:2, :9].unsqueeze(0),
        'gripper_width': states[:2, 9:].unsqueeze(0).clone().requires_grad_(True),
    }
    batch = {'obs': obs, 'action': actions[:8].unsqueeze(0)}
    return model, batch


def test_loss_reaches_both_cameras_and_gripper_input(example):
    model, batch = example
    model.train()
    loss = model.compute_loss(batch)
    assert loss.ndim == 0 and torch.isfinite(loss)
    loss.backward()
    for key in ('camera_wrist', 'camera_external'):
        gradient = model.obs_encoder.backbones[key].conv1.weight.grad
        assert gradient is not None and torch.isfinite(gradient).all()
        assert gradient.abs().sum() > 0
    assert batch['obs']['gripper_width'].grad.abs().sum() > 0


def test_sampling_uses_ten_dimensions_and_checkpoint_keeps_normalizers(example):
    model, batch = example
    model.eval()
    noise = torch.randn(1, 8, 10)
    first = model.predict_action(batch['obs'], init_noise=noise)
    restored = copy.deepcopy(model)
    restored.state_normalizer.fitted.fill_(False)
    restored.load_state_dict(model.state_dict())
    second = restored.predict_action(batch['obs'], init_noise=noise)
    assert first['action'].shape == (1, 3, 10)
    assert first['action_pred'].shape == (1, 8, 10)
    assert torch.isfinite(first['action_pred']).all()
    assert (first['action'][..., -1] >= 0).all() and (first['action'][..., -1] <= .08).all()
    torch.testing.assert_close(first['action_pred'], second['action_pred'])


def test_normalizer_requires_training_statistics_and_preserves_constant_actions():
    normalizer = FeatureNormalizer(2)
    with pytest.raises(RuntimeError, match="Fit normalizers"):
        normalizer.normalize(torch.zeros(2, 2))
    values = torch.tensor([[0., .035], [1., .035]])
    normalizer.fit(values)
    torch.testing.assert_close(normalizer.unnormalize(normalizer.normalize(values)), values)
    restored = normalizer.unnormalize(torch.tensor([[.5, .9]]))
    assert restored[0, 1] == values[0, 1]


def test_padded_action_steps_can_be_masked(example, monkeypatch):
    model, batch = example
    batch['valid_mask'] = torch.tensor([[True, True, True, False, False, False, False, False]])
    # With zero predicted noise, the expected masked loss is known exactly.
    monkeypatch.setattr(model, 'forward', lambda noisy, timesteps, obs: torch.zeros_like(noisy))
    torch.manual_seed(17)
    noise = torch.randn_like(batch['action'])
    expected = noise[batch['valid_mask']].square().mean()
    torch.manual_seed(17)
    loss = model.compute_loss(batch)
    torch.testing.assert_close(loss, expected)
    batch['valid_mask'][:] = False
    with pytest.raises(ValueError, match="valid_mask"):
        model.compute_loss(batch)


@pytest.mark.parametrize('horizon', [16, 20])
def test_unet_accepts_standard_horizons_with_global_condition(horizon):
    network = ConditionalUnet1D(10, 12, down_dims=(16, 32, 64), time_embed_dim=16)
    actions = torch.randn(2, horizon, 10)
    result = network(actions, torch.tensor([1, 5]), torch.randn(2, 12))
    assert result.shape == actions.shape
    with pytest.raises(ValueError, match='divisible'):
        network(torch.randn(2, 17, 10), 1, torch.randn(2, 12))
