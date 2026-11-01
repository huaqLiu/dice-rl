"""Run neural inference in a spawned process, separate from ROS/Qt's Python GIL."""
from concurrent.futures import ProcessPoolExecutor
import copy
import hashlib
import multiprocessing
import os
from pathlib import Path
import time

_policy = None


def _initialize_worker():
    import cv2
    import torch
    torch.set_num_threads(2)
    cv2.setNumThreads(1)


def _load_worker(path, device, collector, real, execution, loader):
    global _policy
    if loader is None:
        from my_dice_rl.agent.eval.rl_policy import load_execution_policy
        loader = load_execution_policy
    _policy = loader(path, device, collector, real, execution)
    identity = copy.deepcopy(_policy.identity)
    identity.update(inference_backend='process', inference_worker_pid=os.getpid(),
                    inference_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    return dict(path=_policy.path, identity=identity, obs_steps=_policy.obs_steps,
                dt=_policy.dt, horizon=_policy.horizon, act_steps=_policy.act_steps,
                image_size=getattr(_policy, 'image_size', 224))


def _predict_worker(history, submitted):
    started = time.monotonic()
    actions, noise = _policy.predict(history)
    finished = time.monotonic()
    return actions, noise, dict(worker_pid=os.getpid(), queue_ms=(started-submitted)*1000,
                               compute_ms=(finished-started)*1000, finished=finished)


class ProcessPolicy:
    """Same policy interface as CheckpointPolicy; no ROS initialization in the worker."""
    def __init__(self, path, device, collector, real=True, execution=None, *, _loader=None):
        self.closed = False
        self.last_timing = {}
        # CUDA must not be initialized in a forked copy of the ROS/Qt process.
        self._pool = ProcessPoolExecutor(max_workers=1,
            mp_context=multiprocessing.get_context('spawn'), initializer=_initialize_worker)
        try:
            info = self._pool.submit(_load_worker, path, device, collector, real, execution, _loader).result()
        except BaseException:
            self.close()
            raise
        for key, value in info.items():
            setattr(self, key, value)

    def predict(self, history):
        if self.closed:
            raise RuntimeError('Inference process is closed')
        submitted = time.monotonic()
        actions, noise, timing = self._pool.submit(_predict_worker, history, submitted).result()
        received = time.monotonic()
        timing.update(return_ms=(received-timing.pop('finished'))*1000, received=received)
        self.last_timing = timing
        return actions, noise

    def close(self, wait=False):
        if not self.closed:
            self.closed = True
            self._pool.shutdown(wait=wait, cancel_futures=True)
