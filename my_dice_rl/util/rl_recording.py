"""Queued raw RL rollouts; reward labels are applied only after execution stops."""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import queue
import threading
import time

import cv2
import h5py
import numpy as np

from my_dice_rl.util.recording import atomic_json, policy_image

SCHEMA = 'dice_panda_rl_rollout_v1'


class RolloutWriter:
    def __init__(self, root, task, config, policy, simulated=False):
        stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S_%f')
        self.path = Path(root) / ('_demo' if simulated else 'real') / task / stamp
        self.path.mkdir(parents=True, exist_ok=False)
        self.meta = dict(schema=SCHEMA, status='recording', simulated=simulated,
            policy=policy, config=config, frames=0, transitions=0, eligible_for_rl=False,
            synchronization='causal software receive-time pairing; not exposure synchronization',
            created_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat())
        self.queue = queue.Queue(maxsize=512)
        self.error, self.closed, self.frames = '', False, 0
        self.closing = False
        self.worker = threading.Thread(target=self._run, daemon=False)
        self.worker.start()

    def put(self, group, values, images=None):
        if self.error:
            raise RuntimeError(self.error)
        if self.closing:
            raise RuntimeError('Rollout is already closing')
        try:
            self.queue.put_nowait((group, values, images))
        except queue.Full as exc:
            raise RuntimeError('写盘队列落后，停止本条') from exc

    def observation(self, frame, command_index):
        values = dict(tick=np.float64(frame['tick']), wall_time=np.float64(time.time()),
            pose=np.asarray(frame['eef'].value, dtype=np.float64),
            gripper_width=np.float32(frame['gripper'].value),
            joint_position=np.asarray(frame['robot'].value['q']),
            joint_velocity=np.asarray(frame['robot'].value['dq']),
            wrench=np.asarray(frame['robot'].value['wrench']), command_index=np.int64(command_index))
        for key in ('wrist', 'external', 'eef', 'gripper', 'robot'):
            values['source/'+key] = np.float64(frame[key].source)
            values['receive/'+key] = np.float64(frame[key].received)
        self.put('observations', values, {key: policy_image(frame[key].value) for key in ('wrist', 'external')})

    def finish(self, outcome, reason, notes='', unsafe=False, diagnostics=None):
        if self.closing:
            return
        self.closing = True
        # Only a small sentinel is added here; disk I/O never blocks the control timer.
        try:
            self.queue.put_nowait(('finish', dict(outcome=outcome, reason=reason, notes=notes,
                                                 unsafe=unsafe, diagnostics=diagnostics), None))
        except queue.Full:
            self.error = '写盘队列已满，保留 partial 文件'

    def _run(self):
        counts = {}
        try:
            # Metadata fsync belongs on the writer thread too: a busy disk must
            # not consume the control loop's first 150 ms scheduling budget.
            atomic_json(self.path/'metadata.json', self.meta)
            with h5py.File(self.path/'rollout.partial.h5', 'w') as file:
                file.attrs['schema'] = SCHEMA
                while True:
                    if self.error:
                        raise RuntimeError(self.error)
                    try:
                        group, values, images = self.queue.get(timeout=.2)
                    except queue.Empty:
                        continue
                    if group == 'finish':
                        self._finalize(file, counts, values)
                        break
                    index = counts.get(group, 0)
                    for key, value in values.items():
                        value = np.asarray(value)
                        if not np.isfinite(value).all():
                            raise ValueError(f'Non-finite rollout field: {key}')
                        name = group+'/'+key
                        if name not in file:
                            file.create_dataset(name, (0, *value.shape), maxshape=(None, *value.shape),
                                                chunks=(1, *value.shape), dtype=value.dtype)
                        file[name].resize(index+1, axis=0)
                        file[name][index] = value
                    for key, image in (images or {}).items():
                        frames = image[None] if image.ndim == 3 else image
                        name = group+'/rgb/'+key
                        if name not in file:
                            file.create_dataset(name, (0, len(frames)), maxshape=(None, len(frames)),
                                                dtype=h5py.vlen_dtype(np.dtype('uint8')))
                        file[name].resize(index+1, axis=0)
                        for image_index, rgb in enumerate(frames):
                            ok, jpeg = cv2.imencode('.jpg', cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR),
                                                    [cv2.IMWRITE_JPEG_QUALITY, 95])
                            if not ok:
                                raise IOError('Rollout JPEG encoding failed')
                            file[name][index, image_index] = jpeg.reshape(-1)
                    counts[group] = index+1
                    if group == 'observations':
                        self.frames = index+1
                    if sum(counts.values()) % 50 == 0:
                        file.flush()
            partial = self.path/'rollout.partial.h5'
            with partial.open('rb') as stream:
                import os
                os.fsync(stream.fileno())
            partial.rename(self.path/'rollout.h5')
            self.meta['status'] = self.meta['outcome']
            atomic_json(self.path/'metadata.json', self.meta)
        except Exception as exc:
            self.error = str(exc)
            self.meta.update(status='invalid', error=self.error, eligible_for_rl=False)
            atomic_json(self.path/'metadata.json', self.meta)
        finally:
            self.closed = True

    def _finalize(self, file, counts, label):
        diagnostics = label.pop('diagnostics', None)
        if diagnostics is not None:
            self.meta['stop_diagnostics'] = diagnostics
        count = max(0, counts.get('observations', 0)-1)
        eligible = label['outcome'] in ('success', 'failed') and not label['unsafe'] and count > 0
        outcome = label['outcome'] if eligible or label['outcome'] == 'aborted' else 'invalid'
        reward = np.zeros(count, np.float32)
        terminated, truncated = np.zeros(count, bool), np.zeros(count, bool)
        if count:
            reward[-1] = float(outcome == 'success' and eligible)
            truncated[-1] = label['reason'] == 'timeout' and outcome != 'success' or not eligible
            terminated[-1] = eligible and not truncated[-1]
        starts = file['observations/command_index'][:] if counts.get('observations') else []
        for key, value in dict(obs_index=np.arange(count), next_obs_index=np.arange(1, count+1),
            command_start=np.asarray(starts[:-1], dtype=np.int64),
            command_end=np.asarray(starts[1:], dtype=np.int64), reward=reward,
            dt=np.diff(file['observations/tick'][:]) if counts.get('observations') else np.zeros(0),
            terminated=terminated, truncated=truncated).items():
            file.create_dataset('transitions/'+key, data=value)
        self.meta.update(outcome=outcome, label=label, eligible_for_rl=eligible,
                         frames=counts.get('observations', 0), transitions=count, counts=counts)
        file.attrs['outcome'] = outcome
        file.flush()
