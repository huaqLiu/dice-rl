"""打印模型构成；--smoke 可用随机输入检查计算，不读取数据或连接设备。"""
import argparse

import torch

from my_dice_rl.model.diffusion.diffusion_policy import DiffusionPolicy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full', action='store_true', help='Print every layer')
    parser.add_argument('--smoke', action='store_true', help='Run loss/backward and action prediction')
    parser.add_argument('--device', default='cpu', choices=['cpu', 'cuda'])
    args = parser.parse_args()
    torch.manual_seed(0)
    policy = DiffusionPolicy().to(args.device)
    print('Observation: wrist/external RGB [B,2,3,224,224]; actual pose [B,2,9]; gripper [B,2,1]')
    print('Encoder: independent ResNet18 x 2 -> 512 + 512 + 9 + 1 = 1034 features/frame')
    print('Condition: 2 x 1034 = 2068; ConditionalUnet1D channels: [256,512,1024]')
    print('Actions: XYZ(3) + rotation6d(6) + target total gripper width(1) = 10')
    print('Horizon: 20 steps; action window: 4 steps; DDIM inference: 16 steps')
    print(f'Parameters: {sum(p.numel() for p in policy.parameters()):,}')
    if args.full:
        print(policy)
    if not args.smoke:
        return
    device = torch.device(args.device)
    # Synthetic values only check tensor flow. They are not demonstration trajectories.
    states = torch.randn(64, 10, device=device)
    states[:, -1] = torch.linspace(.01, .07, 64, device=device)
    actions = states.clone()
    policy.fit_normalizer(states, actions)
    obs = {
        'camera_wrist': torch.rand(1, 2, 3, 224, 224, device=device),
        'camera_external': torch.rand(1, 2, 3, 224, 224, device=device),
        'robot_eef_pose': states[:2, :9].unsqueeze(0),
        'gripper_width': states[:2, 9:].unsqueeze(0),
    }
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
