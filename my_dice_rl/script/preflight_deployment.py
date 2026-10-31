"""Compare expert trajectories with deployment limits without connecting to a robot."""
import argparse
import json
from pathlib import Path

import numpy as np
import yaml
import zarr

from my_dice_rl.util.config import project_path, PROJECT_ROOT
from my_dice_rl.util.rotation import rotation6d_to_matrix


def check_limits(dataset, execution, output=None):
    root = zarr.open_group(str(project_path(dataset)),mode='r')
    if not root.attrs.get('complete') or root.attrs.get('simulated'):
        raise ValueError('Deployment preflight expects a complete real demonstration dataset')
    cfg = yaml.safe_load(Path(execution).read_text())
    ends = root['meta/episode_ends'][:]
    start = 0
    episodes = []
    for number,end in enumerate(ends):
        end = int(end)
        actual = root['data/robot_eef_pose'][start:end]
        action = root['data/action'][start:end]
        actual_rotation = rotation6d_to_matrix(actual[:,3:9])
        goal_rotation = rotation6d_to_matrix(action[:,3:9])
        reset_dist = np.linalg.norm(action[:,:3]-actual[0,:3],axis=1)
        actual_dist = np.linalg.norm(actual[:,:3]-actual[0,:3],axis=1)
        goal_error = np.linalg.norm(action[:,:3]-actual[:,:3],axis=1)
        reset_angle = np.arccos(np.clip((np.sum(goal_rotation*actual_rotation[0],axis=(-2,-1))-1)/2,-1,1))
        tracking_angle = np.arccos(np.clip((np.sum(goal_rotation*actual_rotation,axis=(-2,-1))-1)/2,-1,1))
        flags = []
        if max(reset_dist.max(),actual_dist.max()) > cfg['max_translation_from_reset']:
            flags.append('workspace_translation')
        if reset_angle.max() > cfg['max_rotation_from_reset']:
            flags.append('workspace_rotation')
        if goal_error.max() > cfg['max_goal_position_error']:
            flags.append('goal_position_distance')
        if tracking_angle.max() > cfg['max_goal_orientation_error']:
            flags.append('goal_orientation_distance')
        source = root.attrs['episodes'][number]['source']
        episodes.append(dict(episode_index=number,source=source,frames=end-start,
            target_distance_from_start_max_m=float(reset_dist.max()),
            actual_distance_from_start_max_m=float(actual_dist.max()),
            target_actual_distance_max_m=float(goal_error.max()),
            target_rotation_from_start_max_rad=float(reset_angle.max()),
            target_actual_angle_max_rad=float(tracking_angle.max()),flags=flags))
        start = end
    report = dict(schema='dice_deployment_preflight_v1',dataset=str(project_path(dataset)),
        dataset_id=root.attrs['dataset_id'],execution_config=str(Path(execution).resolve()),
        frames=int(ends[-1]),episodes=episodes,
        episodes_exceeding_limits=sum(bool(e['flags']) for e in episodes),
        observed_translation_envelope_m=max(max(e['target_distance_from_start_max_m'],
            e['actual_distance_from_start_max_m']) for e in episodes),
        configured_translation_envelope_m=cfg['max_translation_from_reset'],
        note='Offline comparison from each demonstration first frame; physical reset confirmation may differ. Observed envelopes are not approved robot safety limits. No limits are changed.')
    if output:
        path = Path(output).resolve()
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',required=True)
    parser.add_argument('--execution',default=str(PROJECT_ROOT/'cfg/execute_dp.yaml'))
    parser.add_argument('--output')
    args = parser.parse_args()
    print(json.dumps(check_limits(args.dataset,args.execution,args.output),ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
