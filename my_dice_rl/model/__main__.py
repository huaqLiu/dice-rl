"""打印模型构成；--smoke 可用随机输入检查计算，不读取数据或连接设备。"""
import argparse

import torch

from my_dice_rl.model.diffusion.diffusion_policy import DiffusionPolicy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full', action='store_true', help='Print every layer')
    parser.add_argument('--smoke', action='store_true', help='Run loss/backward and action prediction')
    parser.add_argument('--device', default='cpu', choices=['cpu', 'cuda'])
    parser.add_argument('--encoder', choices=['clip_vit_b32', 'resnet18'], default='clip_vit_b32')
    parser.add_argument('--camera-count', type=int, choices=[2, 3], default=2)
    parser.add_argument('--pretrained', action='store_true', help='Load pretrained weights; default prints architecture only')
    args = parser.parse_args()
    torch.manual_seed(0)
    keys = ['camera_wrist', 'camera_external'] + (['camera_side'] if args.camera_count == 3 else [])
    policy = DiffusionPolicy(vision_encoder=args.encoder, pretrained_vision=args.pretrained, camera_keys=keys).to(args.device)
    print('Observation:', ', '.join(keys), 'RGB [B,2,3,224,224]; actual pose [B,2,9]')
    size = policy.obs_encoder.visual_feature_dim
    print(f'Encoder: {args.encoder} x {len(keys)} -> {size} x {len(keys)} + 9 = {policy.obs_encoder.feature_dim} features/frame')
    print(f'Condition: 2 x {policy.obs_encoder.feature_dim} = {policy.obs_encoder.feature_dim*2}; ConditionalUnet1D channels: [256,512,1024]')
    print('Pretrained weights loaded:', args.pretrained, '(training default loads CLIP pretrained weights)')
    print('Actions: XYZ(3) + rotation6d(6) = 9; preserve existing grasp')
    print('Horizon: 20 steps; action window: 4 steps; DDIM inference: 16 steps')
    print(f'Parameters: {sum(p.numel() for p in policy.parameters()):,}')
    if args.full:
        print(policy)
    if not args.smoke:
        return
    device = torch.device(args.device)
    # Synthetic values only check tensor flow. They are not demonstration trajectories.
    states = torch.randn(64, 9, device=device)
    actions = states.clone()
    policy.fit_normalizer(states, actions)
    obs = {key: torch.rand(1, 2, 3, 224, 224, device=device) for key in keys}
    obs['robot_eef_pose'] = states[:2, :9].unsqueeze(0)
    policy.train()
    loss = policy.compute_loss({'obs': obs, 'action': actions[:20].unsqueeze(0)})
    loss.backward()
    policy.eval()
    result = policy.predict_action(obs)
    if not torch.isfinite(loss) or not torch.isfinite(result['action_pred']).all():
        raise RuntimeError('Non-finite loss or sampled action')
    print(f'Synthetic loss/backward OK; loss={loss.item():.6f}')
    print('action_pred:', tuple(result['action_pred'].shape))
    print('action:', tuple(result['action'].shape))
    print('Model is untrained; this check does not measure task performance.')


if __name__ == '__main__':
    main()
