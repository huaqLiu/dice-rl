"""独立的 RL checkpoint/部署包格式；部署包嵌入 DP，迁移文件后不依赖原路径。"""
import copy
from pathlib import Path

import torch
import yaml

from my_dice_rl.model.diffusion.diffusion_policy import DiffusionPolicy
from my_dice_rl.model.rl.distill_rl import ResidualActor
from my_dice_rl.util.checkpoint import atomic_save, load_checkpoint, load_policy
from my_dice_rl.util.rl_config import execution_limits, file_digest, require_review, digest

RL_CHECKPOINT_SCHEMA = 'dice_gear_rl_checkpoint_v1'
RL_POLICY_SCHEMA = 'dice_gear_rl_policy_v1'


def snapshot_teacher(path, device):
    teacher, state = load_policy(path, device, use_ema=True)
    payload = dict(model_config=copy.deepcopy(state['model_config']), task=copy.deepcopy(state['config']['task']),
                   dataset_id=state['dataset_id'], sha256=file_digest(path),
                   model={k: v.detach().cpu() for k, v in teacher.state_dict().items()})
    return teacher, payload


def teacher_from_payload(payload, device='cpu'):
    teacher = DiffusionPolicy(**{**payload['model_config'], 'pretrained_vision': False})
    teacher.load_state_dict(payload['model'], strict=True)
    if not teacher.state_normalizer.fitted or not teacher.action_normalizer.fitted:
        raise ValueError('Frozen DP normalizers are not fitted')
    return teacher.to(device).requires_grad_(False).eval()


def load_rl_checkpoint(path):
    state = torch.load(Path(path), map_location='cpu', weights_only=False)
    if state.get('schema') != RL_CHECKPOINT_SCHEMA:
        raise ValueError('Not an RL training checkpoint')
    return state


def export_rl_policy(checkpoint, output, allow_draft=False):
    if Path(output).exists():
        raise FileExistsError('Choose a new immutable policy filename: ' + str(output))
    state = load_rl_checkpoint(checkpoint)
    config = state['config']
    review = yaml.safe_load(Path(config['review_config']).read_text())
    simulated = bool(state['simulated'] or state['teacher']['task'].get('allow_demo', True))
    deployable = not simulated and state['global_step'] > 0 and state.get('actor_updates', 0) > 0
    try:
        require_review(review)
    except ValueError:
        deployable = False
        if not allow_draft:
            raise
    if not deployable and not allow_draft:
        raise ValueError('模拟/未训练策略不能导出为真机部署包；仅离线检查可使用 --allow-draft')
    execution = yaml.safe_load(Path(config['execution_config']).read_text())
    payload = dict(schema=RL_POLICY_SCHEMA, teacher=state['teacher'], model_config=config['model'],
        actor=state['model']['actor'], global_step=state['global_step'], actor_updates=state.get('actor_updates', 0), simulated=simulated,
        deployable=deployable, review=review, review_digest=digest(review),
        execution_limits=execution_limits(execution), teacher_sha256=state['teacher']['sha256'],
        training_checkpoint_sha256=file_digest(checkpoint),
        algorithm='frozen_dp_residual_dice', data_manifest=state['data_manifest'])
    # 导出前实例化并严格校验，避免导出缺失权重或错误维度。
    # 校验时的随机网络初始化不能改变 learner 下一步的 RNG，保证恢复训练一致。
    with torch.random.fork_rng(devices=[]):
        policy = policy_from_payload(payload)
    for value in policy.state_dict().values():
        if value.is_floating_point() and not torch.isfinite(value).all():
            raise ValueError('Nonfinite policy weights; export refused')
    atomic_save(output, payload)
    return Path(output).resolve()


class ResidualPolicy(torch.nn.Module):
    def __init__(self, teacher, actor, chunk_steps):
        super().__init__()
        self.teacher, self.actor = teacher, actor
        self.obs_steps, self.horizon_steps, self.act_steps = teacher.obs_steps, teacher.horizon_steps, chunk_steps

    @property
    def state_normalizer(self):
        return self.teacher.state_normalizer

    @property
    def action_normalizer(self):
        return self.teacher.action_normalizer

    @torch.no_grad()
    def predict_action(self, obs, init_noise=None, generator=None):
        features = self.teacher.encode_obs(obs)
        if init_noise is None:
            init_noise = torch.randn(len(features), self.horizon_steps, 10,
                                     device=features.device, generator=generator)
        if init_noise.shape != (len(features), self.horizon_steps, 10) or not torch.isfinite(init_noise).all():
            raise ValueError('Invalid RL initial noise')
        physical = self.teacher.predict_action_from_features(features, init_noise)['action_pred']
        base = self.action_normalizer.normalize(physical).clamp(-1, 1)
        residual = self.actor(features, init_noise).masked_fill(self.action_normalizer.constant, 0.)
        action = self.action_normalizer.unnormalize((base + residual).clamp(-1, 1))
        action = torch.cat([action[..., :9], action[..., 9:].clamp(0., .08)], dim=-1)
        return dict(action=action[:, :self.act_steps], action_pred=action)


def policy_from_payload(payload, device='cpu'):
    if payload.get('schema') != RL_POLICY_SCHEMA:
        raise ValueError('Not a self-contained RL deployment policy')
    teacher = teacher_from_payload(payload['teacher'], device)
    config = payload['model_config']
    if not 1 <= config['chunk_steps'] <= teacher.horizon_steps:
        raise ValueError('RL action window exceeds teacher horizon')
    actor = ResidualActor(teacher.obs_steps * teacher.obs_encoder.feature_dim, teacher.horizon_steps,
                          10, config['hidden_dims'], config['residual_limit']).to(device)
    actor.load_state_dict(payload['actor'], strict=True)
    if not torch.equal(actor.limit.cpu(), torch.tensor(config['residual_limit'], dtype=torch.float32)):
        raise ValueError('Residual bounds disagree with deployment metadata')
    return ResidualPolicy(teacher, actor, config['chunk_steps']).to(device).eval()
