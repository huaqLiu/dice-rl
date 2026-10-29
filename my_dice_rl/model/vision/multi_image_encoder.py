"""两路 RGB -> 两个视觉特征；与实际末端位姿、夹爪开度拼接。"""
from collections.abc import Mapping

import torch
from torch import nn
from torchvision.models import ResNet18_Weights, resnet18


def _replace_batchnorm(module: nn.Module):
    """GroupNorm 不依赖 batch 统计量，也方便后续使用 EMA。"""
    for name, child in list(module.named_children()):
        if isinstance(child, nn.BatchNorm2d):
            replacement = nn.GroupNorm(32, child.num_features)
            with torch.no_grad():
                replacement.weight.copy_(child.weight)
                replacement.bias.copy_(child.bias)
            setattr(module, name, replacement)
        else:
            _replace_batchnorm(child)


def _resnet18(pretrained: bool):
    # pretrained=False 不下载权重；正式训练时可选择 ImageNet 预训练权重。
    network = resnet18(weights=ResNet18_Weights.DEFAULT if pretrained else None)
    _replace_batchnorm(network)
    network.fc = nn.Identity()  # 去掉分类头，输出 512 维视觉特征。
    return network


class MultiImageEncoder(nn.Module):
    camera_keys = ("camera_wrist", "camera_external")
    pose_dim = 9  # 实际 XYZ + rotation6d；采集文件的四元数要由后续数据集转换。
    state_dim = 10  # pose_dim + 实际夹爪总开度。

    def __init__(self, image_size: int = 224, pretrained: bool = False):
        super().__init__()
        if image_size < 32:
            raise ValueError("image_size must be at least 32")
        self.image_size = image_size
        self.backbones = nn.ModuleDict({key: _resnet18(pretrained) for key in self.camera_keys})
        self.feature_dim = 512 * 2 + self.state_dim  # 每一帧：1034 维。
        self.register_buffer("image_mean", torch.tensor([.485, .456, .406]).view(1, 3, 1, 1))
        self.register_buffer("image_std", torch.tensor([.229, .224, .225]).view(1, 3, 1, 1))

    def forward(self, obs: Mapping[str, torch.Tensor]) -> torch.Tensor:
        """输入图像 (B, To, 3, 224, 224)，返回逐帧特征 (B, To, 1034)。

        RGB 为 [0,1] 浮点数。位姿与开度在进入本编码器前由 DP 模型归一化。
        """
        pose, gripper = obs["robot_eef_pose"], obs["gripper_width"]
        if pose.ndim != 3 or pose.shape[-1] != self.pose_dim:
            raise ValueError("robot_eef_pose must have shape (B, To, 9): XYZ + rotation6d")
        batch_size, obs_steps = pose.shape[:2]
        if gripper.shape != (batch_size, obs_steps, 1):
            raise ValueError("gripper_width must have shape (B, To, 1)")
        features = []
        for key in self.camera_keys:
            image = obs[key]
            expected = (batch_size, obs_steps, 3, self.image_size, self.image_size)
            if image.shape != expected or not image.is_floating_point():
                raise ValueError(f"{key} must be floating RGB [0,1], shape {expected}")
            image = image.reshape(-1, *image.shape[2:])
            image = (image - self.image_mean) / self.image_std
            visual = self.backbones[key](image)
            features.append(visual.reshape(batch_size, obs_steps, 512))
        return torch.cat([*features, pose, gripper], dim=-1)
