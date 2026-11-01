"""Causal software alignment and history at the checkpoint's training frequency."""
from collections import deque
import threading
import time

import numpy as np

from my_dice_rl.util.recording import Sample


class ObservationSyncError(ValueError):
    """Preserve the failing query, since rejected frames never enter rollout.h5."""
    def __init__(self, message, diagnostics):
        self.diagnostics = diagnostics
        super().__init__(message)


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

    def _sync_error(self, message, tick, max_age, max_skew, selected=None):
        details = dict(query_tick=float(tick), max_age_s=float(max_age),
                       max_skew_s=float(max_skew), channels={})
        for key in self.keys:
            samples = self.buffers[key]
            causal = [s for s in samples if s.received <= tick]
            sample = (selected or {}).get(key, causal[-1] if causal else None)
            details['channels'][key] = dict(
                buffered=len(samples),
                causal=len(causal),
                error=self.errors.get(key),
                received=float(sample.received) if sample else None,
                source=float(sample.source) if sample else None,
                age_ms=float((tick-sample.received)*1000) if sample else None,
                latest_age_ms=float((tick-samples[-1].received)*1000) if samples else None)
        if selected:
            stamps = {k: s.received for k, s in selected.items()}
            oldest, newest = min(stamps, key=stamps.get), max(stamps, key=stamps.get)
            details.update(skew_ms=float((stamps[newest]-stamps[oldest])*1000),
                           oldest=oldest, newest=newest)
            message += (f'：{oldest} / {newest} 相差 {details["skew_ms"]:.1f} ms'
                        f'，上限 {max_skew*1000:.1f} ms')
        return ObservationSyncError(message, details)

    def snapshot(self, tick, max_age, max_skew, max_gripper_age=None):
        with self.lock:
            candidates = {}
            for key in self.keys:
                if key in self.errors:
                    raise self._sync_error(f'{key}: {self.errors[key]}', tick, max_age, max_skew)
                candidates[key] = [s for s in self.buffers[key] if 0 <= tick-s.received <= max_age]
                if not candidates[key]:
                    raise self._sync_error(f'{key}: 缺少新鲜观测', tick, max_age, max_skew)
            pairs = [(a, b) for a in candidates['wrist'] for b in candidates['external']
                     if abs(a.received-b.received) <= max_skew]
            pair = min(pairs, key=lambda p: max(tick-p[0].received, tick-p[1].received), default=None)
            if pair is None:
                raise self._sync_error('双相机无法在允许时间差内配对', tick, max_age, max_skew)
            result = {key: candidates[key][-1] for key in ('eef', 'gripper', 'robot')}
            result['wrist'], result['external'] = pair
            # Franka Hand reports at about 10 Hz, slower than images/arm state.
            # Keep the fast-stream alignment check and bound gripper age separately.
            synchronized = result if max_gripper_age is None else {k: v for k, v in result.items() if k != 'gripper'}
            stamps = [sample.received for sample in synchronized.values()]
            if max(stamps)-min(stamps) > max_skew:
                raise self._sync_error('图像与机器人状态时间差超限', tick, max_age, max_skew,
                                       synchronized)
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
            result = []
            for index in range(steps):
                try:
                    result.append(self.snapshot(tick-(steps-1-index)*dt, max_age, max_skew, max_gripper_age))
                except ObservationSyncError as exc:
                    exc.diagnostics.update(query_kind='history', history_index=index,
                                           history_end_tick=float(tick))
                    raise
            return result
