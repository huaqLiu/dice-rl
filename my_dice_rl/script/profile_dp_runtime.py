"""Profile the real execution policy loader/predictor on saved observations; no ROS."""
import argparse
from contextlib import nullcontext
import json
from pathlib import Path
import time

import cv2
import h5py
import numpy as np
import torch
import yaml
import zarr

from my_dice_rl.agent.eval.dp_policy import CheckpointPolicy
from my_dice_rl.agent.dataset.gear_dataset import split_episodes
from my_dice_rl.util.config import PROJECT_ROOT
from my_dice_rl.util.gpu_idle import read_gpu
from my_dice_rl.util.recording import Sample
from my_dice_rl.util.rl_coordination import DeviceLease


def profile(checkpoint,output,device='cuda:0',windows=10):
    torch.set_num_threads(2)
    cv2.setNumThreads(1)
    gpu = str(device).startswith('cuda')
    availability = read_gpu(str(device).split(':')[-1] if ':' in str(device) else '0') if gpu else None
    if availability and availability['processes']:
        raise RuntimeError('GPU 有计算任务占用，请等待空闲后再做部署耗时测试')
    collector=yaml.safe_load((PROJECT_ROOT/'cfg/collect.yaml').read_text())
    execution=yaml.safe_load((PROJECT_ROOT/'cfg/execute_dp.yaml').read_text())
    lease=DeviceLease(PROJECT_ROOT/'log_dir/rl/device.lock') if gpu else nullcontext()
    with lease:
        started=time.monotonic()
        policy=CheckpointPolicy(checkpoint,device,collector,real=False)
        warmup_ms=(time.monotonic()-started)*1000
        task=policy.identity['task']
        root=zarr.open_group(task['dataset_path'],mode='r')
        _,validation=split_episodes(len(root.attrs['episodes']),task['val_ratio'],task['split_seed'])
        ids=validation or list(range(len(root.attrs['episodes'])))
        latencies=[];finite=True;gripper_min=.08;gripper_max=0.
        for index in range(windows):
            source=root.attrs['episodes'][ids[index%len(ids)]]['source']
            with h5py.File(source,'r') as file:
                count=len(file['timestamp'])
                anchor=round((index%3)/2*(count-1))
                history=[]
                for frame in np.clip(np.arange(anchor-policy.obs_steps+1,anchor+1),0,count-1):
                    observation={key:Sample(0.,0.,cv2.cvtColor(cv2.imdecode(file['rgb/'+key][frame],
                        cv2.IMREAD_COLOR),cv2.COLOR_BGR2RGB)) for key in ('wrist','external')}
                    observation['eef']=Sample(0.,0.,file['robot_eef_pose'][frame])
                    observation['gripper']=Sample(0.,0.,float(file['gripper_width'][frame,0]))
                    history.append(observation)
            started=time.monotonic()
            action,noise=policy.predict(history)
            latencies.append((time.monotonic()-started)*1000)
            finite &= bool(np.isfinite(action).all() and np.isfinite(noise).all())
            gripper_min=min(gripper_min,float(action[:,9].min()))
            gripper_max=max(gripper_max,float(action[:,9].max()))
        report=dict(schema='dice_dp_runtime_profile_v1',checkpoint=str(Path(checkpoint).resolve()),
            dataset_id=policy.identity['dataset_id'],simulated=bool(root.attrs['simulated']),device=device,
            load_and_warmup_ms=warmup_ms,windows=windows,
            latency_ms=dict(mean=float(np.mean(latencies)),p95=float(np.percentile(latencies,95)),max=max(latencies)),
            configured_inference_budget_ms=execution['max_inference_latency_s']*1000,
            all_within_budget=max(latencies)<=execution['max_inference_latency_s']*1000,
            finite_actions=finite,gripper_prediction_range_m=[gripper_min,gripper_max],
            gpu_before=availability,robot_commands_sent=False,
            note='Saved-observation timing through the actual DP execution predictor; not closed-loop robot validation.')
        path=Path(output).resolve();path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
        return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--device',default='cuda:0')
    parser.add_argument('--windows',type=int,default=10)
    args=parser.parse_args()
    if args.windows<1:parser.error('windows must be positive')
    print(json.dumps(profile(args.checkpoint,args.output,args.device,args.windows),ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
