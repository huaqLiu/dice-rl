"""DP 模型总入口：归一化 -> 编码观测 -> 训练去噪 / 采样动作序列。

这里没有数据加载、优化器或机器人接口。动作表示：XYZ + rotation6d + 总开度。
"""
import torch
from torch import nn
from torch.nn import functional as F

from my_dice_rl.model.common.normalizer import FeatureNormalizer
from my_dice_rl.model.vision.multi_image_encoder import MultiImageEncoder
from my_dice_rl.model.diffusion.conditional_unet1d import ConditionalUnet1D


class DiffusionPolicy(nn.Module):
    action_dim = 10

    def __init__(self, obs_steps=2, horizon_steps=20, act_steps=4,
                 down_dims=(256, 512, 1024), time_embed_dim=256,
                 train_diffusion_steps=50, inference_steps=16,
                 pretrained_vision=False, image_size=224):
        super().__init__()
        if obs_steps < 1 or not 1 <= act_steps <= horizon_steps:
            raise ValueError("Require obs_steps >= 1 and 1 <= act_steps <= horizon_steps")
        if not 1 <= inference_steps <= train_diffusion_steps:
            raise ValueError("Require 1 <= inference_steps <= train_diffusion_steps")
        self.obs_steps, self.horizon_steps, self.act_steps = obs_steps, horizon_steps, act_steps
        self.inference_steps = inference_steps
        self.obs_encoder = MultiImageEncoder(image_size, pretrained_vision)
        self.network = ConditionalUnet1D(
            self.action_dim, self.obs_encoder.feature_dim * obs_steps,
            down_dims=down_dims, time_embed_dim=time_embed_dim)
        if horizon_steps < 1 or horizon_steps % self.network.horizon_multiple:
            raise ValueError(f"horizon_steps must be divisible by {self.network.horizon_multiple}")
        self.state_normalizer = FeatureNormalizer(10)
        self.action_normalizer = FeatureNormalizer(self.action_dim)
        try:
            from diffusers import DDIMScheduler
        except ImportError as exc:
            raise ImportError("Install model/requirements.txt in a training virtual environment") from exc
        # 与原真机 DP 配置一致：cosine schedule、预测 epsilon、DDIM 推理。
        self.noise_scheduler = DDIMScheduler(
            num_train_timesteps=train_diffusion_steps,
            beta_schedule="squaredcos_cap_v2", prediction_type="epsilon", clip_sample=True)

    @torch.no_grad()
    def fit_normalizer(self, training_states, training_actions):
        """states: (...,10) 实际位姿9+实际开度1；actions: (...,10) 目标动作。

        必须由后续数据集只提供训练集统计数据。不要用验证集拟合。
        """
        self.state_normalizer.fit(training_states)
        self.action_normalizer.fit(training_actions)

    def encode_obs(self, obs):
        pose, gripper = obs["robot_eef_pose"], obs["gripper_width"]
        if pose.ndim != 3 or pose.shape[1:] != (self.obs_steps, 9):
            raise ValueError(f"Expected robot_eef_pose (B, {self.obs_steps}, 9)")
        if gripper.shape != (*pose.shape[:2], 1):
            raise ValueError("Expected gripper_width (B, To, 1)")
        state = self.state_normalizer.normalize(torch.cat([pose, gripper], dim=-1))
        normalized_obs = {**obs, "robot_eef_pose": state[..., :9], "gripper_width": state[..., 9:]}
        return self.obs_encoder(normalized_obs).flatten(start_dim=1)

    def forward(self, noisy_actions, timesteps, obs):
        """单次去噪网络前向；noisy_actions 已处于归一化动作空间。"""
        return self.network(noisy_actions, timesteps, self.encode_obs(obs))

    def compute_loss(self, batch):
        """batch={'obs': {...}, 'action': (B,H,10), 'valid_mask': 可选(B,H)}。"""
        actions = batch["action"]
        if actions.ndim != 3 or actions.shape[1:] != (self.horizon_steps, self.action_dim):
            raise ValueError(f"Expected actions (B, {self.horizon_steps}, 10)")
        actions = self.action_normalizer.normalize(actions)
        noise = torch.randn_like(actions)
        timesteps = torch.randint(self.noise_scheduler.config.num_train_timesteps,
                                  (actions.shape[0],), device=actions.device)
        noisy_actions = self.noise_scheduler.add_noise(actions, noise, timesteps)
        predicted_noise = self(noisy_actions, timesteps, batch["obs"])
        error = F.mse_loss(predicted_noise, noise, reduction="none")
        mask = batch.get("valid_mask")
        if mask is None:
            return error.mean()
        if mask.shape != actions.shape[:2] or mask.dtype != torch.bool or not mask.any():
            raise ValueError("valid_mask must be nonempty boolean (B,H)")
        return error[mask].mean()  # 轨迹边界补齐的无效动作不参与损失。

    @torch.no_grad()
    def predict_action(self, obs, init_noise=None, generator=None):
        """返回物理单位动作；只执行前 act_steps 步，再根据新观测重新预测。

        数据约定：obs 是截至当前 t 的历史，动作序列从当前 t 的目标开始。
        init_noise 允许后续 DICE-RL 对同一噪声 z 生成相同基础动作。
        """
        condition = self.encode_obs(obs)
        shape = (condition.shape[0], self.horizon_steps, self.action_dim)
        if init_noise is None:
            sample = torch.randn(shape, device=condition.device, dtype=condition.dtype,
                                 generator=generator)
        else:
            if init_noise.shape != shape:
                raise ValueError(f"Expected init_noise {shape}")
            sample = init_noise.to(condition).clone()
        # 每次采样创建独立 scheduler，避免推理步数等可变状态互相影响。
        scheduler = type(self.noise_scheduler).from_config(self.noise_scheduler.config)
        scheduler.set_timesteps(self.inference_steps, device=condition.device)
        for timestep in scheduler.timesteps:
            predicted_noise = self.network(sample, timestep, condition)
            sample = scheduler.step(predicted_noise, timestep, sample, eta=0.,
                                    generator=generator).prev_sample
        prediction = self.action_normalizer.unnormalize(sample.clamp(-1, 1))
        prediction = torch.cat([prediction[..., :9], prediction[..., 9:].clamp(0., .08)], dim=-1)
        return {"action": prediction[:, :self.act_steps], "action_pred": prediction}
