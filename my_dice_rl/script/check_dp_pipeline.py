"""Check data/model or reload a trained EMA policy, entirely offline."""
import argparse
import json
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader

from my_dice_rl.agent.dataset.gear_dataset import make_datasets
from my_dice_rl.model.diffusion.diffusion_policy import DiffusionPolicy
from my_dice_rl.util.checkpoint import load_policy, move_to_device
from my_dice_rl.util.config import DEFAULT_TRAIN_CONFIG, load_train_config, model_config, project_path
from my_dice_rl.util.rotation import rotation6d_to_matrix


def check_pipeline(config, checkpoint=None, output=None):
    device = torch.device(config['training']['device'])
    checkpoint_state = None
    if checkpoint:
        model, checkpoint_state = load_policy(checkpoint, device)
        # Model windows/preprocessing are taken from the checkpoint, not CLI guesses.
        cfg = checkpoint_state['model_config']
        for key in ('obs_steps', 'horizon_steps', 'act_steps'):
            config['task'][key] = cfg[key]
        image_size = cfg['image_size']
    else:
        cfg = model_config(config)
        model = DiffusionPolicy(**cfg)
        image_size = cfg['image_size']
    train, validation = make_datasets(config['task'], image_size)
    dataset = validation if len(validation) else train
    if not checkpoint:
        model.fit_normalizer(*train.normalizer_data())
        model.to(device)
    batch = move_to_device(next(iter(DataLoader(dataset, batch_size=1, shuffle=False))), device)
    torch.manual_seed(config['training']['seed'])
    with torch.no_grad():
        model.eval()
        loss = model.compute_loss(batch)
    if not checkpoint:
        model.train()
        model.compute_loss(batch).backward()
        model.zero_grad(set_to_none=True)
    model.eval()
    result = model.predict_action(batch['obs'])
    if not torch.isfinite(loss) or not torch.isfinite(result['action_pred']).all():
        raise RuntimeError('Non-finite loss/predictions')
    prediction = result['action_pred'][0].cpu().numpy()
    reference = batch['action'][0].cpu().numpy()
    valid = batch['valid_mask'][0].cpu().numpy()
    predicted, target = prediction[valid], reference[valid]
    metrics = dict(position_rmse_m=float(np.sqrt(np.mean((predicted[:, :3]-target[:, :3])**2))),
                   gripper_rmse_m=float(np.sqrt(np.mean((predicted[:, 9]-target[:, 9])**2))))
    try:
        rotation = rotation6d_to_matrix(predicted[:, 3:9])
        desired = rotation6d_to_matrix(target[:, 3:9])
        trace = np.sum(rotation * desired, axis=(-2, -1))
        metrics['mean_rotation_error_rad'] = float(np.arccos(np.clip((trace-1)/2, -1, 1)).mean())
    except ValueError as exc:
        metrics['mean_rotation_error_rad'] = None
        metrics['rotation_error'] = str(exc)
    report = dict(checkpoint=str(checkpoint) if checkpoint else None,
        split='validation' if len(validation) else 'train',
        simulated=dataset.metadata['simulated'], windows=len(dataset),
        loss=float(loss), action_shape=list(result['action'].shape),
        action_pred_shape=list(result['action_pred'].shape),
        obs_shapes={key: list(value.shape) for key, value in batch['obs'].items()},
        metrics=metrics, note='Offline imitation error, not robot task success rate')
    if checkpoint_state:
        report['training_dataset_matches'] = checkpoint_state['dataset_id'] == dataset.metadata['dataset_id']
    if output:
        path = Path(output).expanduser().resolve()
        if path.exists() or path.with_suffix('.json').exists():
            raise FileExistsError(path)
        if path.suffix != '.npz':
            raise ValueError('Output file must end in .npz')
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, predicted_action=prediction, target_action=reference,
                            valid_mask=valid, actual_pose=batch['obs']['robot_eef_pose'][0].cpu().numpy(),
                            actual_gripper_width=batch['obs']['gripper_width'][0].cpu().numpy())
        path.with_suffix('.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', help='Defaults to training YAML, or checkpoint run YAML')
    parser.add_argument('--dataset', help='Optional processed dataset path')
    parser.add_argument('--checkpoint', help='best.pt or last.pt; EMA weights are used')
    parser.add_argument('--output', help='Optional new .npz file for predictions and reference actions')
    parser.add_argument('--set', nargs='*', default=[])
    args = parser.parse_args()
    path = (args.config or (Path(args.checkpoint).resolve().parent.parent / 'config.yaml'
                           if args.checkpoint else DEFAULT_TRAIN_CONFIG))
    config = load_train_config(path, args.set)
    if args.dataset:
        config['task']['dataset_path'] = str(project_path(args.dataset))
    print(json.dumps(check_pipeline(config, args.checkpoint, args.output), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
