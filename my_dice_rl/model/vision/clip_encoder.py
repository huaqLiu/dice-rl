"""与原项目同款CLIP ViT-B/32，取768维CLS，不使用512维图文投影头。"""
import torch
from torch import nn

CLIP_MODEL = 'vit_base_patch32_clip_224.openai'


class CLIPVisionEncoder(nn.Module):
    feature_dim = 768

    def __init__(self, pretrained=False):
        super().__init__()
        try:
            import timm
        except ImportError as exc:
            raise ImportError('CLIP encoder requires timm==1.0.19 in .venv-dp') from exc
        self.model = timm.create_model(CLIP_MODEL, pretrained=pretrained, num_classes=0, global_pool='')
        if self.model.num_features != self.feature_dim:
            raise ValueError('Expected 768-dimensional CLIP ViT-B/32 features')
        self.image_mean = self.model.pretrained_cfg['mean']
        self.image_std = self.model.pretrained_cfg['std']

    def forward(self, image):
        # forward_features includes ViT final LayerNorm; first token is CLS.
        # No text encoder or CLIP projection is used in the robot policy.
        return self.model.forward_features(image)[:, 0, :]
