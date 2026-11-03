"""Real CLIP architecture/weights, no robot or CUDA; use cached weights offline."""
import copy

import pytest
import torch

from my_dice_rl.model.diffusion.diffusion_policy import DiffusionPolicy
from my_dice_rl.util.checkpoint import atomic_save, load_policy, CHECKPOINT_SCHEMA
from my_dice_rl.util.config import load_train_config, model_config


@pytest.fixture(scope='module')
def clip_policy():
    torch.set_num_threads(2)
    policy = DiffusionPolicy(vision_encoder='clip_vit_b32', pretrained_vision=True,
        vision_augment=True, image_size=224, horizon_steps=4, act_steps=2, down_dims=[16, 32],
        time_embed_dim=16, train_diffusion_steps=4, inference_steps=2)
    policy.fit_normalizer(torch.randn(20, 9), torch.randn(20, 9))
    yield policy


def observations():
    return {**{key: torch.rand(1, 2, 3, 224, 224) for key in ('camera_wrist', 'camera_external')},
            'robot_eef_pose': torch.randn(1, 2, 9)}


def test_clip_pretrained_cls_features_independent_cameras_and_normalization(clip_policy):
    model = clip_policy.eval()
    encoder = model.obs_encoder
    assert encoder.visual_feature_dim == 768 and encoder.feature_dim == 1545
    left = encoder.backbones['camera_wrist'].model.patch_embed.proj.weight
    right = encoder.backbones['camera_external'].model.patch_embed.proj.weight
    assert left.data_ptr() != right.data_ptr()
    torch.testing.assert_close(left, right, rtol=0, atol=0)
    torch.testing.assert_close(encoder.image_mean.flatten(), torch.tensor([.48145466, .4578275, .40821073]))
    torch.testing.assert_close(encoder.image_std.flatten(), torch.tensor([.26862954, .26130258, .27577711]))
    obs = observations()
    with torch.no_grad():
        features = model.encode_obs(obs)
        repeated = model.encode_obs(obs)
        image = (obs['camera_wrist'][:, 0]-encoder.image_mean)/encoder.image_std
        # Same original-project selection: timm global_pool='' followed by CLS.
        expected = encoder.backbones['camera_wrist'].model(image)[:, 0]
    assert features.shape == (1, 3090)
    torch.testing.assert_close(features, repeated, rtol=0, atol=0)
    torch.testing.assert_close(features.reshape(1, 2, 1545)[:, 0, :768], expected)


def test_clip_loss_backpropagates_to_both_pretrained_encoders(clip_policy):
    model = clip_policy.train()
    model.zero_grad(set_to_none=True)
    loss = model.compute_loss(dict(obs=observations(), action=torch.randn(1, 4, 9)))
    loss.backward()
    assert torch.isfinite(loss)
    for backbone in model.obs_encoder.backbones.values():
        grad = backbone.model.patch_embed.proj.weight.grad
        assert grad is not None and torch.isfinite(grad).all() and grad.abs().sum() > 0
    model.zero_grad(set_to_none=True)


def test_clip_checkpoint_loads_without_pretrained_download_and_keeps_actions(clip_policy, tmp_path, monkeypatch):
    import timm
    model = clip_policy.eval()
    config = dict(vision_encoder='clip_vit_b32', pretrained_vision=True, vision_augment=True,
        frozen_vision=False, state_dim=9, action_dim=9, image_size=224, obs_steps=2,
        horizon_steps=4, act_steps=2, down_dims=[16, 32], time_embed_dim=16,
        train_diffusion_steps=4, inference_steps=2)
    obs, noise = observations(), torch.randn(1, 4, 9)
    expected = model.predict_action(obs, init_noise=noise)['action_pred']
    path = tmp_path/'clip.pt'
    atomic_save(path, dict(schema=CHECKPOINT_SCHEMA, model_config=config,
                           model=model.state_dict(), ema=dict(model=model.state_dict())))
    original = timm.create_model
    def offline(*args, **kwargs):
        assert kwargs.get('pretrained') is False, 'Checkpoint loading must not download CLIP weights'
        return original(*args, **kwargs)
    monkeypatch.setattr(timm, 'create_model', offline)
    restored, _ = load_policy(path)
    result = restored.predict_action(obs, init_noise=noise)['action_pred']
    assert result.shape == (1, 4, 9)
    torch.testing.assert_close(result, expected, rtol=0, atol=0)


def test_default_training_config_uses_clip_and_pose_only_actions():
    config = load_train_config()
    model = model_config(config)
    assert model['vision_encoder'] == 'clip_vit_b32' and model['pretrained_vision']
    assert not model['frozen_vision'] and model['vision_augment']
    assert model['state_dim'] == model['action_dim'] == 9
    assert config['training']['vision_learning_rate'] == .1*config['training']['learning_rate']
    assert config['training']['early_stopping_patience'] == 30


def test_clip_teacher_and_residual_policy_keep_same_fixed_noise_actions(clip_policy):
    from my_dice_rl.util.rl_checkpoint import teacher_from_payload, ResidualPolicy
    from my_dice_rl.model.rl.distill_rl import ResidualActor
    config = dict(vision_encoder='clip_vit_b32', pretrained_vision=True, vision_augment=True,
        state_dim=9, action_dim=9, image_size=224, obs_steps=2, horizon_steps=4, act_steps=2,
        down_dims=[16, 32], time_embed_dim=16, train_diffusion_steps=4, inference_steps=2)
    teacher = teacher_from_payload(dict(model_config=config, model=clip_policy.state_dict()))
    assert teacher.obs_encoder.feature_dim*teacher.obs_steps == 3090
    actor = ResidualActor(3090, 4, 9, [16], [.05]*9)
    policy = ResidualPolicy(teacher, actor, 2).eval()
    obs, noise = observations(), torch.randn(1, 4, 9)
    expected = clip_policy.eval().predict_action(obs, init_noise=noise)['action_pred']
    actual = policy.predict_action(obs, init_noise=noise)['action_pred']
    torch.testing.assert_close(actual, expected)
    assert all(not parameter.requires_grad for parameter in teacher.parameters())
