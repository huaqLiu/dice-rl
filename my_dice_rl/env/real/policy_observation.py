"""Causal software alignment and history at the checkpoint's training frequency."""
from collections import deque
import threading
import time

import numpy as np

from my_dice_rl.util.recording import Sample


class ObservationBuffer:
    keys = ('wrist', 'external', 'eef', 'gripper', 'robot')

    def __init__(self):
        self.lock = threading.RLock()
        self.buffers = {key: deque(maxlen=128) for key in self.keys}
        self.errors, self.camera_info = {}, {}

    def put(self, key, value, source=0., received=None):
        with self.lock:
            self.buffers.setdefault(key, deque(maxlen=128)).append(
                Sample(time.monotonic() if received is None else received, source, value))
            self.errors.pop(key, None)

    def error(self, key, message):
        with self.lock:
            self.errors[key] = str(message)

    def latest(self, key):
        with self.lock:
            values = self.buffers.get(key)
            return values[-1] if values else None

    def snapshot(self, tick, max_age, max_skew, max_gripper_age=None):
        with self.lock:
            candidates = {}
            for key in self.keys:
                if key in self.errors:
                    raise ValueError(f'{key}: {self.errors[key]}')
                candidates[key] = [s for s in self.buffers[key] if 0 <= tick-s.received <= max_age]
                if not candidates[key]:
                    raise ValueError(f'{key}: 缺少新鲜观测')
            pairs = [(a, b) for a in candidates['wrist'] for b in candidates['external']
                     if abs(a.received-b.received) <= max_skew]
            pair = min(pairs, key=lambda p: max(tick-p[0].received, tick-p[1].received), default=None)
            if pair is None:
                raise ValueError('双相机无法在允许时间差内配对')
            result = {key: candidates[key][-1] for key in ('eef', 'gripper', 'robot')}
            result['wrist'], result['external'] = pair
            # Franka Hand reports at about 10 Hz, slower than images/arm state.
            # Keep the fast-stream alignment check and bound gripper age separately.
            synchronized = result if max_gripper_age is None else {k: v for k, v in result.items() if k != 'gripper'}
            stamps = [sample.received for sample in synchronized.values()]
            if max(stamps)-min(stamps) > max_skew:
                raise ValueError('图像与机器人状态时间差超限')
            if max_gripper_age is not None and tick-result['gripper'].received > max_gripper_age:
                raise ValueError('夹爪观测更新超时')
            pose = np.asarray(result['eef'].value)
            if pose.shape != (7,) or not np.isfinite(pose).all() or abs(np.linalg.norm(pose[3:])-1) > .02:
                raise ValueError('末端位姿无效')
            width = result['gripper'].value
            if not np.isfinite(width) or not 0 <= width <= .085:
                raise ValueError('夹爪开度无效')
            return dict(tick=float(tick), **result)

    def history(self, tick, steps, dt, max_age, max_skew, max_gripper_age=None):
        # Every historical frame is causal relative to its own query time.
        with self.lock:
            return [self.snapshot(tick-(steps-1-index)*dt, max_age, max_skew, max_gripper_age)
                    for index in range(steps)]
