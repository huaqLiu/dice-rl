"""Evaluate EMA actions on multiple held-out windows, entirely offline."""
import argparse
import copy
import json
from pathlib import Path
import signal
import threading
import time

import numpy as np
import torch
from torch.utils.data import DataLoader

from my_dice_rl.agent.dataset.gear_dataset import make_datasets
from my_dice_rl.util.checkpoint import load_policy, move_to_device
from my_dice_rl.util.rotation import rotation6d_to_matrix


def summarize(values):
    if not values:
        return None
    a = np.asarray(values)
    return dict(count=int(a.size), mean=float(a.mean()), median=float(np.median(a)),
                p95=float(np.percentile(a,95)), max=float(a.max()))


def evaluate(checkpoint, output, device='cpu', max_windows=15, stop_event=None):
    torch.set_num_threads(2)
    model, state = load_policy(checkpoint, device, use_ema=True)
    config, dataset_id = copy.deepcopy(state['config']), state['dataset_id']
    global_step, complete_training = state['global_step'], state.get('training_complete', False)
    checkpoint_epoch=state['epoch']
    del state
    train, validation = make_datasets(config['task'], model.image_size if hasattr(model, 'image_size') else config['model']['image_size'])
    dataset = validation if len(validation) else train
    if dataset.metadata['dataset_id'] != dataset_id:
        raise ValueError('Evaluation dataset differs from checkpoint training dataset')
    # Spread the budget across every held-out episode, including start/middle/end.
    by_episode = {}
    for index, (episode, anchor) in enumerate(dataset.sampler.indices):
        by_episode.setdefault(episode, []).append(index)
    selected = []
    for fraction in (0., .5, 1.):
        for indices in by_episode.values():
            index = indices[round(fraction*(len(indices)-1))]
            if index not in selected:
                selected.append(index)
    selected = selected[:max_windows]
    positions, angles, widths, latencies, cases, predictions, references, masks = [], [], [], [], [], [], [], []
    prefix_positions,prefix_angles,prefix_widths=[],[],[]
    target_device = torch.device(device)
    for index in selected:
        if stop_event is not None and stop_event.is_set():
            break
        batch = move_to_device(next(iter(DataLoader(dataset, batch_size=1,
            sampler=[index], num_workers=0))), target_device)
        generator = torch.Generator(device=target_device).manual_seed(config['training']['seed']+100000+index)
        started = time.monotonic()
        result = model.predict_action(batch['obs'], generator=generator)['action_pred'][0].cpu().numpy()
        latency = time.monotonic()-started
        if not np.isfinite(result).all():
            raise ValueError('Policy produced nonfinite actions')
        reference = batch['action'][0].cpu().numpy()
        mask = batch['valid_mask'][0].cpu().numpy()
        prediction, desired = result[mask], reference[mask]
        pos = np.linalg.norm(prediction[:,:3]-desired[:,:3],axis=1)*1000
        width = np.abs(prediction[:,9]-desired[:,9])*1000
        try:
            rotation = rotation6d_to_matrix(prediction[:,3:9])
            target_rotation = rotation6d_to_matrix(desired[:,3:9])
            angle = np.degrees(np.arccos(np.clip((np.sum(rotation*target_rotation,axis=(-2,-1))-1)/2,-1,1)))
            angles.extend(angle.tolist())
            rotation_error = None
        except ValueError as exc:
            rotation_error = str(exc)
        positions.extend(pos.tolist()); widths.extend(width.tolist()); latencies.append(latency*1000)
        prefix_mask=mask.copy()
        prefix_mask[config['task']['act_steps']:]=False
        prefix_prediction,prefix_reference=result[prefix_mask],reference[prefix_mask]
        prefix_positions.extend((np.linalg.norm(prefix_prediction[:,:3]-prefix_reference[:,:3],axis=1)*1000).tolist())
        prefix_widths.extend((np.abs(prefix_prediction[:,9]-prefix_reference[:,9])*1000).tolist())
        if rotation_error is None:
            prefix_angles.extend(angle[:int(prefix_mask.sum())].tolist())
        cases.append(dict(episode_id=int(batch['episode_id'][0]), frame_index=int(batch['frame_index'][0]),
            valid_steps=int(mask.sum()), position_error_mm=summarize(pos.tolist()),
            rotation_error=rotation_error, inference_ms=latency*1000))
        predictions.append(result); references.append(reference); masks.append(mask)
    report = dict(schema='dice_gear_dp_eval_v1', checkpoint=str(Path(checkpoint).resolve()),
        dataset_id=dataset_id, simulated=dataset.metadata['simulated'],
        checkpoint_training_complete=complete_training,checkpoint_epoch=checkpoint_epoch,
        global_step=global_step, split='validation' if len(validation) else 'train', device=device,
        completed_windows=len(cases), requested_windows=len(selected),
        complete=len(cases)==len(selected), position_error_mm=summarize(positions),
        rotation_error_deg=summarize(angles), gripper_error_mm=summarize(widths),
        execution_prefix_steps=config['task']['act_steps'],
        execution_prefix_position_error_mm=summarize(prefix_positions),
        execution_prefix_rotation_error_deg=summarize(prefix_angles),
        execution_prefix_gripper_error_mm=summarize(prefix_widths),
        inference_ms=summarize(latencies), cases=cases,
        note='Held-out window imitation error; not a closed-loop or physical assembly success rate. CPU timing is not GPU deployment timing.')
    output = Path(output).resolve()
    output.parent.mkdir(parents=True,exist_ok=True)
    if predictions:
        np.savez_compressed(output.with_suffix('.npz'), predictions=np.stack(predictions),
                            references=np.stack(references), valid_masks=np.stack(masks))
    temporary = output.with_suffix('.tmp')
    temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    temporary.replace(output)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--device',default='cpu')
    parser.add_argument('--max-windows',type=int,default=15)
    args = parser.parse_args()
    if args.max_windows < 1:
        parser.error('max-windows must be positive')
    stop = threading.Event()
    for number in (signal.SIGINT,signal.SIGTERM,signal.SIGUSR1):
        signal.signal(number,lambda *_:stop.set())
    print(json.dumps(evaluate(args.checkpoint,args.output,args.device,args.max_windows,stop),ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
