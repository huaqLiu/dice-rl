"""按特征归一化：训练数据范围映射到 [-1, 1]，参数随模型保存。"""
import torch
from torch import nn


class FeatureNormalizer(nn.Module):
    def __init__(self, feature_dim: int):
        super().__init__()
        self.feature_dim = feature_dim
        self.register_buffer("center", torch.zeros(feature_dim))
        self.register_buffer("half_range", torch.ones(feature_dim))
        self.register_buffer("constant", torch.zeros(feature_dim, dtype=torch.bool))
        self.register_buffer("fitted", torch.tensor(False))

    @torch.no_grad()
    def fit(self, values: torch.Tensor):
        """只传入训练集；前面可以有任意样本/时间维，最后一维是特征。"""
        if values.ndim < 2 or values.shape[-1] != self.feature_dim or values.numel() == 0:
            raise ValueError(f"Expected nonempty (..., {self.feature_dim}) training values")
        values = values.detach().to(self.center).reshape(-1, self.feature_dim)
        if not torch.isfinite(values).all():
            raise ValueError("Normalizer training values must be finite")
        low, high = values.amin(dim=0), values.amax(dim=0)
        half_range = (high - low) / 2
        self.center.copy_((low + high) / 2)
        self.constant.copy_(half_range <= 1e-6)
        self.half_range.copy_(torch.where(self.constant, torch.ones_like(half_range), half_range))
        self.fitted.fill_(True)

    def _check(self, values):
        if not bool(self.fitted):
            raise RuntimeError("Fit normalizers on training data before loss/prediction")
        if values.shape[-1] != self.feature_dim:
            raise ValueError(f"Expected last dimension {self.feature_dim}")

    def normalize(self, values: torch.Tensor) -> torch.Tensor:
        self._check(values)
        return (values - self.center) / self.half_range

    def unnormalize(self, values: torch.Tensor) -> torch.Tensor:
        self._check(values)
        restored = values * self.half_range + self.center
        # 训练示范中从未变化的动作分量保持原值，例如始终夹住齿轮的夹爪开度。
        return torch.where(self.constant, self.center, restored)
