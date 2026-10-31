import json
import time

import h5py
import numpy as np
import pytest

from my_dice_rl.agent.eval.dp_executor import DPExecutor
from my_dice_rl.agent.eval.dp_policy import DemoPolicy
from my_dice_rl.agent.eval.command_limits import limit_command
from my_dice_rl.env.real.policy_observation import ObservationBuffer
from my_dice_rl.env.real.policy_demo import DemoBackend
from my_dice_rl.script.rl_collect_gui import load_config
from my_dice_rl.util.config import PROJECT_ROOT
from my_dice_rl.util.rotation import (pose7_to_pose9, matrix_to_quaternion,
                                     quaternion_to_matrix, quaternion_angle)


def fill(buffer, backend, tick):
    for key in ('wrist', 'external'):
        buffer.put(key, np.full((224, 224, 3), 80, np.uint8), 1700000000+tick, tick)
    buffer.put('eef', backend.pose.copy(), 1700000000+tick, tick)
    buffer.put('gripper', .035, 1700000000+tick, tick)
    buffer.put('robot', dict(q=np.zeros(7), dq=np.zeros(7), wrench=np.zeros(6), mode=2),
               1700000000+tick, tick)


def pump(engine, buffer, backend, seconds=.2, until=None):
    end = time.monotonic()+seconds
    while time.monotonic() < end:
        tick = time.monotonic()
        fill(buffer, backend, tick)
        engine.tick(tick)
        if until is not None and until():
            return
        time.sleep(.02)
    if until is not None:
        assert until(), engine.status()


@pytest.fixture
def runtime(tmp_path):
    config, collector = load_config(PROJECT_ROOT/'cfg/execute_dp.yaml')
    config.update(data_root=str(tmp_path), min_free_gb=0.,device_lock=str(tmp_path/'device.lock'))
    buffer = ObservationBuffer()
    backend = DemoBackend(buffer)
    engine = DPExecutor(config, collector, buffer, backend, simulated=True, policy=DemoPolicy())
    pump(engine, buffer, backend, .22)
    try:
        yield engine, buffer, backend
    finally:
        engine.submit('close')
        pump(engine, buffer, backend, 2., until=lambda: engine.status()['can_close'])
        engine.shutdown()


def test_history_is_causal_and_rejects_skew():
    buffer = ObservationBuffer()
    backend = DemoBackend(buffer)
    for tick in (10., 10.1, 10.2):
        backend.pose[0] = tick
        fill(buffer, backend, tick)
    history = buffer.history(10.1, 2, .1, .2, .08)
    assert [f['eef'].value[0] for f in history] == [10., 10.1]
    with pytest.raises(ValueError, match='新鲜'):
        buffer.snapshot(11., .2, .08)
    buffer.buffers['wrist'].clear()
    buffer.put('wrist', np.zeros((224, 224, 3), np.uint8), 1, 10.02)
    with pytest.raises(ValueError, match='时间差'):
        buffer.snapshot(10.2, .2, .08)


def test_slow_gripper_channel_has_independent_age_without_relaxing_fast_alignment():
    buffer = ObservationBuffer()
    backend = DemoBackend(buffer)
    fill(buffer, backend, 10.)
    buffer.buffers['gripper'].clear()
    buffer.put('gripper', .035, 1., 9.90)
    with pytest.raises(ValueError, match='时间差'):
        buffer.snapshot(10., .2, .08)  # Legacy callers retain strict all-stream skew.
    frame = buffer.snapshot(10., .2, .08, max_gripper_age=.15)
    assert frame['gripper'].value == .035
    buffer.buffers['gripper'].clear()
    buffer.put('gripper', .035, 1., 9.84)
    with pytest.raises(ValueError, match='夹爪观测更新超时'):
        buffer.snapshot(10., .2, .08, max_gripper_age=.15)
    buffer.put('gripper', .035, 1., 9.95)
    buffer.buffers['wrist'].clear()
    buffer.put('wrist', np.zeros((224,224,3), np.uint8), 1., 9.85)
    with pytest.raises(ValueError, match='时间差'):
        buffer.snapshot(10., .2, .08, max_gripper_age=.15)


def test_rotation_decode_and_cartesian_speed_limit(runtime):
    engine, _, backend = runtime
    q = backend.pose[3:]
    np.testing.assert_allclose(quaternion_to_matrix(matrix_to_quaternion(quaternion_to_matrix(q))),
                               quaternion_to_matrix(q), atol=1e-7)
    action = np.r_[pose7_to_pose9(backend.pose), 0.]
    action[0] += .02
    command = limit_command(action, backend.pose, backend.pose, backend.pose, .02, engine.config)
    assert np.linalg.norm(command[:3]-backend.pose[:3]) <= .025*.02 + 1e-9
    assert quaternion_angle(command[3:], backend.pose[3:]) < 1e-6
    action[0] += .3
    with pytest.raises(ValueError, match='范围'):
        limit_command(action, backend.pose, backend.pose, backend.pose, .02, engine.config)


def test_manual_reset_stop_label_and_terminal_reward(runtime):
    engine, buffer, backend = runtime
    engine.submit('start')
    pump(engine, buffer, backend, .04)
    assert engine.state == 'RESET' and not backend.commands
    engine.submit('ready', held_confirmed=True)
    pump(engine, buffer, backend, .5, until=lambda: engine.state == 'READY')
    engine.submit('start')
    pump(engine, buffer, backend, .6,
         until=lambda: engine.state == 'RUNNING' and engine.plan_id >= 0)
    assert engine.state == 'RUNNING' and engine.plan_id >= 0, engine.status()
    engine.submit('label', outcome='success')
    pump(engine, buffer, backend, .04)
    assert engine.state == 'RUNNING', engine.status()  # Cannot label while motion is running.
    engine.submit('stop')
    pump(engine, buffer, backend, .5,
         until=lambda: engine.state == 'LABEL' and engine.writer.frames >= 2)
    assert not backend.active
    assert backend.has_lock  # Preserve the stopped scene until the human supplies a label.
    engine.submit('label', outcome='success', notes='synthetic test')
    pump(engine, buffer, backend, 2., until=lambda: engine.state == 'RESET')
    metadata = json.loads((engine.last_path/'metadata.json').read_text())
    assert metadata['eligible_for_rl'] and metadata['outcome'] == 'success'
    with h5py.File(engine.last_path/'rollout.h5') as file:
        reward = file['transitions/reward'][:]
        assert reward[-1] == 1 and reward.sum() == 1
        assert file['transitions/terminated'][-1]
        assert not file['transitions/truncated'][-1]
        assert len(file['observations/pose']) == len(reward)+1
        assert file['commands/requested_action'].shape[1] == 10
        assert file['plans/init_noise'].shape[1:] == (20, 10)
        assert np.all(file['transitions/command_end'][:] <= len(file['commands/tick']))
    before = len(backend.commands)
    engine.submit('start')
    pump(engine, buffer, backend, .05)
    assert engine.state == 'RESET' and len(backend.commands) == before


def test_stop_fences_off_late_inference(runtime):
    engine, buffer, backend = runtime
    class SlowPolicy(DemoPolicy):
        def predict(self, history):
            time.sleep(.4)
            return super().predict(history)
    engine.policy = SlowPolicy()
    engine.config['max_inference_latency_s'] = .8
    engine.submit('ready', held_confirmed=True)
    pump(engine, buffer, backend, .5, until=lambda: engine.state == 'READY')
    engine.submit('start')
    pump(engine, buffer, backend, .06)
    engine.submit('stop')
    pump(engine, buffer, backend, .6, until=lambda: engine.state == 'LABEL')
    count = len(backend.commands)
    pump(engine, buffer, backend, .5)
    assert len(backend.commands) == count and engine.plan is None
    engine.submit('label', outcome='aborted')
    pump(engine, buffer, backend, 2., until=lambda: engine.state == 'RESET')
    assert not engine.writer.meta['eligible_for_rl']


def test_force_abort_is_not_an_ordinary_failure(runtime):
    engine, buffer, backend = runtime
    engine.submit('ready', held_confirmed=True)
    pump(engine, buffer, backend, .5, until=lambda: engine.state == 'READY')
    engine.submit('start')
    pump(engine, buffer, backend, .2)
    now = time.monotonic()
    fill(buffer, backend, now)
    buffer.put('robot', dict(q=np.zeros(7), dq=np.zeros(7), wrench=np.array([30., 0, 0, 0, 0, 0]), mode=2),
               1700000000+now, now)
    engine.tick(now)
    assert engine.state == 'STOPPING'
    pump(engine, buffer, backend, 2., until=lambda: engine.state == 'RESET')
    assert engine.writer.meta['outcome'] == 'aborted'
    assert not engine.writer.meta['eligible_for_rl']


def test_unconfirmed_gripper_cancel_preserves_lock_and_prevents_next_round(runtime):
    engine, buffer, backend = runtime
    engine.config['cancel_timeout_s'] = .05
    engine.submit('ready', held_confirmed=True)
    pump(engine, buffer, backend, .5, until=lambda: engine.state == 'READY')
    engine.submit('start')
    pump(engine, buffer, backend, .2)
    backend.gripper_busy = True
    engine.submit('stop')
    pump(engine, buffer, backend, 2., until=lambda: engine.state == 'FAULT' and engine.writer.closed)
    assert backend.has_lock and not engine.writer.meta['eligible_for_rl']
    count = len(backend.commands)
    engine.submit('ready', held_confirmed=True)
    pump(engine, buffer, backend, .05)
    assert engine.state == 'FAULT' and len(backend.commands) == count


def test_real_execution_rejects_simulated_training_checkpoint(monkeypatch):
    from my_dice_rl.agent.eval import dp_policy
    config, collector = load_config(PROJECT_ROOT/'cfg/execute_dp.yaml')
    monkeypatch.setattr(dp_policy, 'load_policy', lambda *args, **kwargs:
                        (None, {'config': {'task': {'allow_demo': True}}}))
    with pytest.raises(ValueError, match='正式数据'):
        dp_policy.CheckpointPolicy('synthetic.pt', 'cpu', collector, real=True)
