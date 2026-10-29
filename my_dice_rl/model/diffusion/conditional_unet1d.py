"""沿动作序列的时间轴做卷积；按原 DICE-RL 的条件残差 U-Net 结构实现。

参考：dice_rl/model/diffusion/conditional_unet1d.py。
这里只保留全局观测条件分支，并显式检查序列长度，方便逐段阅读。
原项目许可证见 ../LICENSE_DICE_RL。
"""
import math

import torch
from torch import nn


class SinusoidalTimeEmbedding(nn.Module):
    def __init__(self, dim: int):
        super().__init__()
        if dim < 4 or dim % 2:
            raise ValueError("Time embedding dimension must be even and >= 4")
        frequencies = torch.exp(-math.log(10000) * torch.arange(dim // 2) / (dim // 2 - 1))
        self.register_buffer("frequencies", frequencies)

    def forward(self, timesteps):
        angles = timesteps[:, None] * self.frequencies[None, :]
        return torch.cat([angles.sin(), angles.cos()], dim=-1)


class Conv1dBlock(nn.Sequential):
    def __init__(self, in_channels, out_channels, kernel_size=5, groups=8):
        super().__init__(
            nn.Conv1d(in_channels, out_channels, kernel_size, padding=kernel_size // 2),
            nn.GroupNorm(groups, out_channels),
            nn.Mish(),
        )


class ConditionalResidualBlock1D(nn.Module):
    def __init__(self, in_channels, out_channels, cond_dim, kernel_size=5, groups=8):
        super().__init__()
        self.block1 = Conv1dBlock(in_channels, out_channels, kernel_size, groups)
        self.block2 = Conv1dBlock(out_channels, out_channels, kernel_size, groups)
        self.condition = nn.Sequential(nn.Mish(), nn.Linear(cond_dim, out_channels * 2))
        self.residual = (nn.Conv1d(in_channels, out_channels, 1)
                         if in_channels != out_channels else nn.Identity())

    def forward(self, value, condition):
        # FiLM：观测条件与扩散时间步决定每个通道的缩放和偏移。
        scale, bias = self.condition(condition).unsqueeze(-1).chunk(2, dim=1)
        hidden = scale * self.block1(value) + bias
        return self.block2(hidden) + self.residual(value)


class ConditionalUnet1D(nn.Module):
    def __init__(self, action_dim: int, global_cond_dim: int,
                 down_dims=(256, 512, 1024), time_embed_dim=256, kernel_size=5, groups=8):
        super().__init__()
        if not down_dims or groups <= 0 or any(d <= 0 or d % groups for d in down_dims):
            raise ValueError("Positive down_dims must be divisible by groups")
        if kernel_size <= 0 or kernel_size % 2 == 0:
            raise ValueError("kernel_size must be positive and odd")
        self.action_dim = action_dim
        self.global_cond_dim = global_cond_dim
        self.horizon_multiple = 2 ** (len(down_dims) - 1)
        self.time_encoder = nn.Sequential(
            SinusoidalTimeEmbedding(time_embed_dim),
            nn.Linear(time_embed_dim, time_embed_dim * 4), nn.Mish(),
            nn.Linear(time_embed_dim * 4, time_embed_dim),
        )
        cond_dim = global_cond_dim + time_embed_dim
        channel_pairs = list(zip((action_dim, *down_dims[:-1]), down_dims))

        def residual(in_dim, out_dim):
            return ConditionalResidualBlock1D(in_dim, out_dim, cond_dim, kernel_size, groups)

        self.down_blocks = nn.ModuleList()
        for index, (in_dim, out_dim) in enumerate(channel_pairs):
            downsample = (nn.Conv1d(out_dim, out_dim, 3, stride=2, padding=1)
                          if index < len(channel_pairs) - 1 else nn.Identity())
            self.down_blocks.append(nn.ModuleList([
                residual(in_dim, out_dim), residual(out_dim, out_dim), downsample]))
        self.middle_blocks = nn.ModuleList([
            residual(down_dims[-1], down_dims[-1]), residual(down_dims[-1], down_dims[-1])])
        self.up_blocks = nn.ModuleList()
        for in_dim, out_dim in reversed(channel_pairs[1:]):
            self.up_blocks.append(nn.ModuleList([
                residual(out_dim * 2, in_dim), residual(in_dim, in_dim),
                nn.ConvTranspose1d(in_dim, in_dim, 4, stride=2, padding=1)]))
        self.output = nn.Sequential(
            Conv1dBlock(down_dims[0], down_dims[0], kernel_size, groups),
            nn.Conv1d(down_dims[0], action_dim, 1),
        )

    def forward(self, noisy_action, timesteps, global_cond):
        """(B, H, 10) + 扩散步 t + 观测条件 -> 同形状的预测噪声。"""
        if noisy_action.ndim != 3 or noisy_action.shape[-1] != self.action_dim:
            raise ValueError(f"Expected noisy actions (B, H, {self.action_dim})")
        batch_size, horizon = noisy_action.shape[:2]
        if horizon < self.horizon_multiple or horizon % self.horizon_multiple:
            raise ValueError(f"Action horizon must be divisible by {self.horizon_multiple}")
        if global_cond.shape != (batch_size, self.global_cond_dim):
            raise ValueError("Unexpected global observation condition shape")
        timesteps = torch.as_tensor(timesteps, device=noisy_action.device).reshape(-1)
        if timesteps.numel() not in (1, batch_size):
            raise ValueError("Expected one timestep or one timestep per batch item")
        time_feature = self.time_encoder(timesteps.expand(batch_size))
        condition = torch.cat([time_feature, global_cond], dim=-1)

        hidden = noisy_action.transpose(1, 2)  # Conv1d 使用 (B, channels, time)。
        skips = []
        for block1, block2, downsample in self.down_blocks:
            hidden = block2(block1(hidden, condition), condition)
            skips.append(hidden)
            hidden = downsample(hidden)
        for block in self.middle_blocks:
            hidden = block(hidden, condition)
        for block1, block2, upsample in self.up_blocks:
            hidden = torch.cat([hidden, skips.pop()], dim=1)
            hidden = upsample(block2(block1(hidden, condition), condition))
        return self.output(hidden).transpose(1, 2)
