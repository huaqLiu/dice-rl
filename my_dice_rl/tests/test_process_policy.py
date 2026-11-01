"""Real spawned workers with a lightweight CPU policy; no ROS or robot devices."""
import os
import time

import numpy as np
import pytest

from my_dice_rl.agent.eval.process_policy import ProcessPolicy
from my_dice_rl.util.recording import Sample
from my_dice_rl.util.rotation import pose7_to_pose9


class FakeWorkerPolicy:
    obs_steps, dt, horizon, act_steps, image_size = 2, .1, 20, 4, 224

    def __init__(self, path):
        self.path = path
        self.identity = dict(path=path, loaded_in_pid=os.getpid())
        self.calls = 0

    def predict(self, history):
        if self.path == 'slow':
            time.sleep(.4)
        self.calls += 1
        action = np.r_[pose7_to_pose9(history[-1]['eef'].value), 0.].astype(np.float32)
        action = np.repeat(action[None], self.horizon, axis=0)
        return action, np.full_like(action, self.calls)


def fake_loader(path, device, collector, real, execution):
    if path == 'invalid':
        raise ValueError('invalid test policy')
    return FakeWorkerPolicy(path)


def test_worker_is_a_separate_persistent_process_and_preserves_observations():
    proxy = ProcessPolicy('fake', 'cpu', {}, False, _loader=fake_loader)
    try:
        assert proxy.identity['inference_worker_pid'] != os.getpid()
        assert proxy.identity['loaded_in_pid'] == proxy.identity['inference_worker_pid']
        assert proxy.identity['inference_backend'] == 'process'
        pose = np.array([.45, 0., .3, 1., 0., 0., 0.])
        history = [dict(eef=Sample(0., 0., pose)) for _ in range(2)]
        first, first_noise = proxy.predict(history)
        second, second_noise = proxy.predict(history)
        assert first.shape == (20, 10)
        np.testing.assert_array_equal(first, second)
        np.testing.assert_allclose(first[:, :3], np.repeat(pose[None, :3], 20, axis=0))
        assert np.all(first_noise == 1) and np.all(second_noise == 2)
        assert proxy.last_timing['worker_pid'] == proxy.identity['inference_worker_pid']
        assert all(proxy.last_timing[k] >= 0 for k in ('queue_ms', 'compute_ms', 'return_ms'))
    finally:
        proxy.close(wait=True)
    with pytest.raises(RuntimeError, match='closed'):
        proxy.predict(history)


def test_worker_load_error_reaches_parent():
    with pytest.raises(ValueError, match='invalid test policy'):
        ProcessPolicy('invalid', 'cpu', {}, False, _loader=fake_loader)


def test_stop_discards_late_process_result_and_preserves_device_lock(tmp_path):
    from my_dice_rl.agent.eval.dp_executor import DPExecutor
    from my_dice_rl.env.real.policy_demo import DemoBackend
    from my_dice_rl.env.real.policy_observation import ObservationBuffer
    from my_dice_rl.script.rl_collect_gui import load_config
    from my_dice_rl.tests.test_dp_execution import pump
    from my_dice_rl.util.config import PROJECT_ROOT
    from my_dice_rl.util.rl_coordination import DeviceLease
    config, collector = load_config(PROJECT_ROOT/'cfg/execute_dp.yaml')
    config.update(data_root=str(tmp_path/'rollouts'), min_free_gb=0.,
                  device_lock=str(tmp_path/'device.lock'), max_inference_latency_s=1.)
    policy = ProcessPolicy('slow', 'cpu', {}, False, _loader=fake_loader)
    buffer = ObservationBuffer()
    backend = DemoBackend(buffer)
    engine = DPExecutor(config, collector, buffer, backend, True, policy)
    other = DeviceLease(config['device_lock'])
    try:
        pump(engine, buffer, backend, .25)
        engine.submit('ready', held_confirmed=True)
        pump(engine, buffer, backend, .5, until=lambda: engine.state == 'READY')
        engine.submit('start')
        pump(engine, buffer, backend, .06)
        engine.submit('stop')
        pump(engine, buffer, backend, .5, until=lambda: engine.state == 'LABEL')
        assert not other.acquire()
        count = len(backend.commands)
        pump(engine, buffer, backend, 1., until=lambda: engine.future is None)
        assert len(backend.commands) == count and engine.plan is None and engine.plan_id == -1
        assert other.acquire()
    finally:
        other.release()
        engine.submit('close')
        pump(engine, buffer, backend, 3., until=lambda: engine.status()['can_close'])
        engine.shutdown()
