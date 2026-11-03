"""两路或三路 RGB 与9维末端位姿拼接；10维状态仅用于读取旧模型。"""
from collections.abc import Mapping
import copy

import torch
from torch import nn
from torchvision.models import ResNet18_Weights, resnet18
from torchvision import transforms
from my_dice_rl.util.cameras import camera_keys as configured_camera_keys


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
    def __init__(self, image_size: int = 224, pretrained: bool = False, state_dim: int = 9,
                 vision_encoder='resnet18', frozen_vision=False, vision_augment=False, camera_keys=None):
        super().__init__()
        if image_size < 32:
            raise ValueError("image_size must be at least 32")
        self.camera_keys = configured_camera_keys({'camera_keys': camera_keys}) if camera_keys is not None else self.camera_keys
        self.image_size = image_size
        if isinstance(state_dim, bool) or state_dim not in (9, 10):
            raise ValueError('state_dim must be 9 (pose) or legacy 10 (pose + gripper)')
        self.state_dim = state_dim
        if vision_encoder not in ('resnet18', 'clip_vit_b32'):
            raise ValueError('vision_encoder must be resnet18 or clip_vit_b32')
        if not isinstance(frozen_vision, bool) or not isinstance(vision_augment, bool):
            raise ValueError('frozen_vision and vision_augment must be boolean')
        self.vision_encoder, self.frozen_vision, self.vision_augment = vision_encoder, frozen_vision, vision_augment
        if vision_encoder == 'clip_vit_b32':
            if image_size != 224:
                raise ValueError('CLIP ViT-B/32 requires 224x224 images')
            from my_dice_rl.model.vision.clip_encoder import CLIPVisionEncoder
            base = CLIPVisionEncoder(pretrained)
            self.visual_feature_dim = base.feature_dim
            mean, std = base.image_mean, base.image_std
            # All cameras start from identical pretrained weights, then learn independently.
            self.backbones = nn.ModuleDict({key: base if index == 0 else copy.deepcopy(base)
                                            for index, key in enumerate(self.camera_keys)})
        else:
            self.visual_feature_dim = 512
            mean, std = [.485, .456, .406], [.229, .224, .225]
            self.backbones = nn.ModuleDict({key: _resnet18(pretrained) for key in self.camera_keys})
        self.feature_dim = self.visual_feature_dim * len(self.camera_keys) + self.state_dim
        self.register_buffer("image_mean", torch.tensor(mean).view(1, 3, 1, 1))
        self.register_buffer("image_std", torch.tensor(std).view(1, 3, 1, 1))
        # Same crop ratio / jitter magnitudes as the original robot config; train only.
        self.image_augmentation = nn.Sequential(
            transforms.RandomCrop(int(image_size*.95)),
            transforms.Resize((image_size, image_size), antialias=True),
            transforms.ColorJitter(brightness=.3, contrast=.4, saturation=.5, hue=.08))
        if frozen_vision:
            self.backbones.requires_grad_(False).eval()

    def train(self, mode=True):
        super().train(mode)
        if self.frozen_vision:
            self.backbones.eval()
        return self

    def forward(self, obs: Mapping[str, torch.Tensor]) -> torch.Tensor:
        """输入图像(B, To, 3, 224, 224)，CLIP每路输出768维，拼接所有视角与机器人状态。

        RGB 为 [0,1] 浮点数。位姿与开度在进入本编码器前由 DP 模型归一化。
        """
        pose = obs["robot_eef_pose"]
        if pose.ndim != 3 or pose.shape[-1] != self.pose_dim:
            raise ValueError("robot_eef_pose must have shape (B, To, 9): XYZ + rotation6d")
        batch_size, obs_steps = pose.shape[:2]
        state = [pose]
        if self.state_dim == 10:
            gripper = obs['gripper_width']
            if gripper.shape != (batch_size, obs_steps, 1):
                raise ValueError("gripper_width must have shape (B, To, 1)")
            state.append(gripper)
        features = []
        for key in self.camera_keys:
            image = obs[key]
            expected = (batch_size, obs_steps, 3, self.image_size, self.image_size)
            if image.shape != expected or not image.is_floating_point():
                raise ValueError(f"{key} must be floating RGB [0,1], shape {expected}")
            image = image.reshape(-1, *image.shape[2:])
            if self.training and self.vision_augment:
                image = self.image_augmentation(image)
            image = (image - self.image_mean) / self.image_std
            visual = self.backbones[key](image)
            features.append(visual.reshape(batch_size, obs_steps, self.visual_feature_dim))
        return torch.cat([*features, *state], dim=-1)
