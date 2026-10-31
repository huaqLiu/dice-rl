"""DICE-RL 真机残差分支：冻结 DP，学习残差，使用集成 Q 和分布收缩正则。

参考 DICE-RL-Robot/dice_rl/model/distill_rl_img.py；这里不复制原工程的通信/硬件依赖。
Critic 使用实际执行目标重采样得到的动作块；未来预测中未执行的部分不参与 TD 损失。
"""
import copy

import torch
from torch import nn
from torch.nn import functional as F


def mlp(input_dim, output_dim, hidden_dims):
    layers = []
    for width in hidden_dims:
        layers.extend([nn.Linear(input_dim, width), nn.LayerNorm(width), nn.Mish()])
        input_dim = width
    layers.append(nn.Linear(input_dim, output_dim))
    return nn.Sequential(*layers)


class ResidualActor(nn.Module):
    def __init__(self, feature_dim, horizon, action_dim, hidden_dims, residual_limit):
        super().__init__()
        self.horizon, self.action_dim = horizon, action_dim
        self.network = mlp(feature_dim + horizon * action_dim, horizon * action_dim, hidden_dims)
        self.register_buffer('limit', torch.tensor(residual_limit, dtype=torch.float32))
        if self.limit.shape != (action_dim,) or not torch.isfinite(self.limit).all() or ((self.limit < 0) | (self.limit > 1)).any():
            raise ValueError('residual_limit must be ten finite nonnegative normalized limits')
        # 第一轮部署等价于基础 DP，不以随机残差启动真机。
        nn.init.zeros_(self.network[-1].weight)
        nn.init.zeros_(self.network[-1].bias)

    def forward(self, features, noise):
        value = self.network(torch.cat([features, noise.flatten(1)], dim=-1))
        return value.reshape(-1, self.horizon, self.action_dim).tanh() * self.limit


class CriticEnsemble(nn.Module):
    def __init__(self, feature_dim, chunk_steps, action_dim, hidden_dims, ensemble_size):
        super().__init__()
        # mask 和持续时间让短终止块与完整动作块可区分。
        size = feature_dim + chunk_steps * (action_dim + 2)
        self.networks = nn.ModuleList([mlp(size, 1, hidden_dims) for _ in range(ensemble_size)])

    def forward(self, features, action, mask, duration):
        action = action * mask[..., None]
        value = torch.cat([features, action.flatten(1), mask.float(), duration * mask], dim=-1)
        return torch.stack([network(value) for network in self.networks], dim=0)


class DistillRL(nn.Module):
    def __init__(self, teacher, config):
        super().__init__()
        self.teacher, self.config = teacher, copy.deepcopy(config)
        teacher.requires_grad_(False).eval()
        self.feature_dim = teacher.obs_encoder.feature_dim * teacher.obs_steps
        self.horizon, self.chunk_steps = teacher.horizon_steps, int(config['chunk_steps'])
        if not 1 <= self.chunk_steps <= self.horizon or config['ensemble_size'] < 2:
            raise ValueError('Invalid chunk length or critic ensemble size')
        if not 1 <= config['target_subset'] <= config['ensemble_size']:
            raise ValueError('target_subset must fit critic ensemble')
        self.actor = ResidualActor(self.feature_dim, self.horizon, 10,
                                   config['hidden_dims'], config['residual_limit'])
        self.critic = CriticEnsemble(self.feature_dim, self.chunk_steps, 10,
                                    config['hidden_dims'], config['ensemble_size'])
        self.target_actor = copy.deepcopy(self.actor).requires_grad_(False)
        self.target_critic = copy.deepcopy(self.critic).requires_grad_(False)

    def train(self, mode=True):
        super().train(mode)
        self.teacher.eval()
        self.target_actor.eval()
        self.target_critic.eval()
        return self

    @torch.no_grad()
    def base_action(self, features, noise):
        physical = self.teacher.predict_action_from_features(features, init_noise=noise)['action_pred']
        return self.teacher.action_normalizer.normalize(physical).clamp(-1, 1)

    def action(self, features, noise, target=False, base=None):
        base = self.base_action(features, noise) if base is None else base
        actor = self.target_actor if target else self.actor
        residual = actor(features, noise)
        residual = residual.masked_fill(self.teacher.action_normalizer.constant, 0.)
        return (base + residual).clamp(-1, 1), base

    @staticmethod
    def td_target(reward, discount, terminated, next_q):
        # truncated 可以 bootstrap；只有真正 terminated 才清零后继价值。
        return reward + discount * (~terminated).float() * next_q

    def critic_loss(self, batch):
        with torch.no_grad():
            noise = torch.randn(len(batch['features']), self.horizon, 10, device=batch['features'].device)
            action, _ = self.action(batch['next_features'], noise, target=True)
            mask = torch.ones_like(batch['mask'])
            duration = torch.ones_like(batch['duration'])
            q = self.target_critic(batch['next_features'], action[:, :self.chunk_steps], mask, duration)
            subset = torch.randperm(len(q), device=q.device)[:self.config['target_subset']]
            target = self.td_target(batch['reward'], batch['discount'], batch['terminated'], q[subset].amin(0))
        q = self.critic(batch['features'], batch['action'], batch['mask'], batch['duration'])
        target = target.unsqueeze(0).expand_as(q)
        loss = F.smooth_l1_loss(q, target) if self.config['td_loss'] == 'huber' else F.mse_loss(q, target)
        return loss, dict(critic_loss=float(loss.detach()), q_mean=float(q.detach().mean()),
                          target_q=float(target.mean()))

    def actor_loss(self, batch, step):
        count, k = len(batch['features']), self.config['multi_z']
        features = batch['features'].repeat_interleave(k, dim=0)
        mask = batch['mask'].repeat_interleave(k, dim=0)
        duration = batch['duration'].repeat_interleave(k, dim=0)
        noise = torch.randn(count * k, self.horizon, 10, device=features.device)
        current, base = self.action(features, noise)
        q = self.critic(features, current[:, :self.chunk_steps], mask, duration).amin(0).reshape(count, k)
        with torch.no_grad():
            prior_q = self.critic(features, base[:, :self.chunk_steps], mask, duration).amin(0).reshape(count, k)
            contraction = q.detach() <= prior_q + self.config['q_margin']
            if step < self.config['filter_warmup']:
                contraction = torch.ones_like(contraction)
            if self.config['retain_expert_bc']:
                contraction |= batch['expert'].bool().expand(-1, k)
        scale = q.detach().abs().mean().clamp_min(1e-3) if self.config['normalize_q'] else 1.
        q_loss = -q.mean() / scale
        distance = (current - base).square().mean((1, 2)).reshape(count, k)
        bc_loss = (distance * contraction).mean()
        # 高价值噪声样本作为同状态低价值样本的自模仿目标；默认权重可设为零。
        candidates = current.reshape(count, k, self.horizon, 10)
        best = q.detach().argmax(1)
        best_action = candidates[torch.arange(count, device=q.device), best].detach()
        best_q = q.detach().gather(1, best[:, None])
        imitate_mask = (best_q - q.detach()) > self.config['q_margin']
        imitation = ((candidates - best_action[:, None]).square().mean((2, 3)) * imitate_mask).mean()
        loss = q_loss + self.config['bc_weight'] * bc_loss + self.config['self_imitation_weight'] * imitation
        return loss, dict(actor_loss=float(loss.detach()), actor_q=float(q.detach().mean()),
                          bc_loss=float(bc_loss.detach()), contraction_fraction=float(contraction.float().mean()),
                          imitation_loss=float(imitation.detach()))

    @torch.no_grad()
    def update_targets(self, tau):
        for target, source in [(self.target_actor, self.actor), (self.target_critic, self.critic)]:
            for dst, src in zip(target.parameters(), source.parameters()):
                dst.lerp_(src, tau)
