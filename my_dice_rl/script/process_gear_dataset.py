"""Validate collected HDF5 episodes and atomically export a disk-backed DP dataset."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
from datetime import datetime
from zoneinfo import ZoneInfo
import uuid

import cv2
import h5py
import numpy as np
from numcodecs import Blosc
import zarr

from my_dice_rl.agent.dataset.gear_dataset import DATASET_SCHEMA
from my_dice_rl.util.config import project_path
from my_dice_rl.util.rotation import pose7_to_pose9

COLLECTION_SCHEMA = 'dice_panda_gear_dual_rgb_v1'
STREAMS = ('wrist', 'external', 'eef', 'target', 'gripper', 'teleop', 'robot')


def _array(file, key, count, tail):
    if key not in file or file[key].shape != (count, *tail):
        raise ValueError(f'Missing or inconsistent field: {key}')
    value = file[key][:]
    if not np.isfinite(value).all():
        raise ValueError(f'Non-finite data: {key}')
    return value


def _episode_values(file, metadata, hold_target_width):
    count = metadata['frames']
    config = metadata['config']
    if count < max(2, config.get('min_episode_frames', 2)):
        raise ValueError('Successful episode is too short')
    if file.attrs.get('schema') != COLLECTION_SCHEMA or file.attrs.get('frames') != count:
        raise ValueError('HDF5 schema/frame count disagrees with metadata')
    hz = float(config['record_hz'])
    if not np.isfinite(hz) or hz <= 0:
        raise ValueError('Invalid sampling frequency')
    timestamp = _array(file, 'timestamp', count, ())
    if not np.allclose(np.diff(timestamp), 1 / hz, rtol=.01, atol=1e-4):
        raise ValueError('Trajectory has missing/nonuniform sample times')
    actual = _array(file, 'robot_eef_pose', count, (7,))
    target = _array(file, 'target_eef_pose', count, (7,))
    for pose in (actual, target):
        if np.any(np.abs(np.linalg.norm(pose[:, 3:], axis=-1)-1) > .02):
            raise ValueError('Invalid quaternion norm')
    width = _array(file, 'gripper_width', count, (1,))
    recorded_target = _array(file, 'gripper_target_width', count, (1,))
    modes = _array(file, 'gripper_mode', count, ())
    if not np.isin(modes, [0, 1, 2]).all():
        raise ValueError('Unknown gripper mode')
    if np.any((width < 0) | (width > .085)) or np.any((recorded_target < 0) | (recorded_target > .08)):
        raise ValueError('Gripper width out of range')
    for stream in STREAMS:
        receive = _array(file, f'receive_timestamp/{stream}', count, ())
        _array(file, f'source_timestamp/{stream}', count, ())
        age = timestamp - receive
        if np.any(age < -1e-5) or np.any(age > config['max_age_s'] + 1e-4):
            raise ValueError(f'Stale/noncausal stream: {stream}')
    skew = np.abs(file['receive_timestamp/wrist'][:] - file['receive_timestamp/external'][:])
    if np.any(skew > config['max_camera_skew_s'] + 1e-4):
        raise ValueError('Camera receive-time skew exceeds collection threshold')
    label_width = recorded_target.copy()
    label_width[modes == 0] = hold_target_width
    action = np.concatenate([pose7_to_pose9(target), label_width], axis=-1).astype(np.float32)
    for camera in ('wrist', 'external'):
        if f'rgb/{camera}' not in file or len(file[f'rgb/{camera}']) != count:
            raise ValueError(f'Missing/incomplete RGB: {camera}')
    return {
        'robot_eef_pose': pose7_to_pose9(actual), 'gripper_width': width.astype(np.float32),
        'action': action, 'gripper_mode': modes.astype(np.uint8),
        'gripper_recorded_target_width': recorded_target.astype(np.float32),
        'timestamp': timestamp.astype(np.float64),
        'external_wrench': _array(file, 'external_wrench', count, (6,)).astype(np.float32),
    }


def convert_dataset(input_path, output_path, allow_demo=False, hold_target_width=0., task='gear_insertion'):
    source, output = Path(input_path).expanduser().resolve(), Path(output_path).expanduser().resolve()
    if not 0 <= hold_target_width <= .08:
        raise ValueError('hold_target_width must be between 0 and 0.08 m')
    if output.exists():
        raise FileExistsError(f'Output already exists; choose a new path: {output}')
    if not source.exists():
        raise FileNotFoundError(source)
    metadata_paths = ([source if source.name == 'metadata.json' else source.parent/'metadata.json']
                      if source.is_file() else sorted(source.rglob('metadata.json')))
    selected, skipped = [], []
    for path in metadata_paths:
        metadata = json.loads(path.read_text(encoding='utf-8'))
        simulated = bool(metadata.get('config', {}).get('simulated') or '_demo' in path.parts)
        reason = None
        if metadata.get('status') != 'success':
            reason = 'status=' + str(metadata.get('status'))
        elif metadata.get('config', {}).get('task') != task:
            reason = 'different task'
        elif simulated and not allow_demo:
            reason = 'simulated data excluded'
        if reason:
            skipped.append(dict(path=str(path), reason=reason))
        else:
            if metadata.get('schema') != COLLECTION_SCHEMA:
                raise ValueError(f'Unexpected collection schema: {path}')
            selected.append((path, metadata, simulated))
    if not selected:
        raise ValueError(f'No eligible successful episodes; skipped {len(skipped)}. '
                         'Check task/status, or use --allow-demo only for a synthetic check.')
    if len({item[2] for item in selected}) != 1:
        raise ValueError('Export real and simulated data to separate datasets')
    first_config = selected[0][1]['config']
    common = {key: first_config[key] for key in ('base_frame', 'ee_frame', 'record_hz')}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + '.partial-' + uuid.uuid4().hex[:8])
    compressor = Blosc(cname='zstd', clevel=3, shuffle=Blosc.BITSHUFFLE)
    manifest, ends, total = [], [], 0
    try:
        root = zarr.open_group(str(temporary), mode='w')
        root.attrs.update(schema=DATASET_SCHEMA, complete=False)
        data = root.create_group('data')
        for camera in ('wrist', 'external'):
            data.create_dataset('camera_' + camera, shape=(0, 224, 224, 3),
                                chunks=(1, 224, 224, 3), dtype='u1', compressor=compressor)
        for metadata_path, metadata, simulated in selected:
            config = metadata['config']
            if any(config[key] != value for key, value in common.items()):
                raise ValueError(f'Coordinate frames or sampling frequency changed: {metadata_path}')
            trajectory = metadata_path.parent / 'trajectory.h5'
            print('Converting:', trajectory, flush=True)
            with h5py.File(trajectory, 'r') as file:
                values = _episode_values(file, metadata, hold_target_width)
                count = metadata['frames']
                for key, value in values.items():
                    if key not in data:
                        data.create_dataset(key, shape=(0, *value.shape[1:]),
                                            chunks=(256, *value.shape[1:]), dtype=value.dtype,
                                            compressor=compressor)
                    data[key].append(value, axis=0)
                for camera in ('wrist', 'external'):
                    target = data['camera_' + camera]
                    target.resize(total + count, 224, 224, 3)
                    for index in range(count):
                        bgr = cv2.imdecode(file['rgb/' + camera][index], cv2.IMREAD_COLOR)
                        if bgr is None or bgr.shape != (224, 224, 3):
                            raise ValueError(f'Invalid 224x224 JPEG: {trajectory}, {camera}, frame {index}')
                        target[total+index] = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            digest = hashlib.sha256()
            with trajectory.open('rb') as stream:
                for block in iter(lambda: stream.read(1024*1024), b''):
                    digest.update(block)
            manifest.append(dict(source=str(trajectory), sha256=digest.hexdigest(), frames=count,
                                 hold_frames=int(np.sum(values['gripper_mode'] == 0))))
            total += count
            ends.append(total)
        root.create_dataset('meta/episode_ends', data=np.asarray(ends, dtype=np.int64))
        identity = hashlib.sha256(json.dumps(dict(episodes=manifest, **common,
                    hold_target_width=hold_target_width), sort_keys=True).encode()).hexdigest()
        root.attrs.update(**common, schema=DATASET_SCHEMA, complete=True, dataset_id=identity,
            simulated=selected[0][2], task=task, hold_target_width=float(hold_target_width),
            rotation_convention='first two rows of rotation matrix; quaternion XYZW',
            action_convention='absolute XYZ + rotation6d + total gripper target width (m)',
            hold_label_policy='mode0 -> configured closed target; recorded width retained separately',
            image_size=224, episodes=manifest,
            created_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat())
        report = dict(dataset_id=identity, episodes=len(ends), frames=total, skipped=skipped,
                      simulated=selected[0][2], hold_target_width=hold_target_width)
        (temporary / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        if output.exists():
            raise FileExistsError(output)
        os.rename(temporary, output)
        return report
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, help='Task folder, session folder, or one episode')
    parser.add_argument('--output', default='datasets/processed/gear_insertion.zarr')
    parser.add_argument('--task', default='gear_insertion')
    parser.add_argument('--allow-demo', action='store_true')
    parser.add_argument('--hold-target-width', type=float, default=0.)
    args = parser.parse_args()
    report = convert_dataset(project_path(args.input), project_path(args.output),
                             args.allow_demo, args.hold_target_width, args.task)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
