"""GPU handover checks use only the in-process DemoBackend, never ROS/hardware."""
import time

from my_dice_rl.agent.eval.dp_executor import DPExecutor
from my_dice_rl.agent.eval.dp_policy import DemoPolicy
from my_dice_rl.env.real.policy_demo import DemoBackend
from my_dice_rl.env.real.policy_observation import ObservationBuffer
from my_dice_rl.script.rl_collect_gui import load_config
from my_dice_rl.tests.test_dp_execution import fill, pump
from my_dice_rl.util.config import PROJECT_ROOT
from my_dice_rl.util.rl_coordination import DeviceLease


def test_training_lease_blocks_start_and_policy_reload_waits_for_reset(tmp_path):
    config, collector = load_config(PROJECT_ROOT / 'cfg/execute_dp.yaml')
    config.update(data_root=str(tmp_path / 'rollouts'), min_free_gb=0., device_lock=str(tmp_path / 'gpu.lock'))
    buffer = ObservationBuffer()
    backend = DemoBackend(buffer)
    engine = DPExecutor(config, collector, buffer, backend, simulated=True, policy=DemoPolicy())
    training = DeviceLease(config['device_lock'])
    try:
        pump(engine, buffer, backend, .25)
        engine.submit('ready', held_confirmed=True)
        pump(engine, buffer, backend, .5, until=lambda: engine.state == 'READY')
        assert training.acquire()
        engine.submit('start')
        pump(engine, buffer, backend, .04)
        assert engine.state == 'READY' and 'GPU' in engine.message
        assert engine.writer is None and not backend.commands
        training.release()
        engine.submit('start')
        pump(engine, buffer, backend, .24)
        assert engine.state == 'RUNNING' and not training.acquire()
        policy = engine.policy
        engine.submit('load', path='not-a-real-checkpoint.pt')
        pump(engine, buffer, backend, .04)
        assert engine.state == 'RUNNING' and engine.policy is policy
        engine.submit('stop')
        pump(engine, buffer, backend, .5, until=lambda: engine.state == 'LABEL')
        assert training.acquire()  # 人工标注时不再占 GPU，控制权锁仍保留。
        assert backend.has_lock
        training.release()
    finally:
        training.release()
        engine.submit('close')
        pump(engine, buffer, backend, 3., until=lambda: engine.status()['can_close'])
        engine.shutdown()


def test_late_inference_retains_device_lease_until_future_finishes(tmp_path):
    class SlowPolicy(DemoPolicy):
        def predict(self, history):
            time.sleep(.45)
            return super().predict(history)

    config, collector = load_config(PROJECT_ROOT / 'cfg/execute_dp.yaml')
    config.update(data_root=str(tmp_path / 'rollouts'), min_free_gb=0., device_lock=str(tmp_path / 'gpu.lock'),
                  max_inference_latency_s=1.)
    buffer = ObservationBuffer()
    backend = DemoBackend(buffer)
    engine = DPExecutor(config, collector, buffer, backend, simulated=True, policy=SlowPolicy())
    training = DeviceLease(config['device_lock'])
    try:
        pump(engine, buffer, backend, .25)
        engine.submit('ready', held_confirmed=True)
        pump(engine, buffer, backend, .5, until=lambda: engine.state == 'READY')
        engine.submit('start')
        pump(engine, buffer, backend, .04)
        engine.submit('stop')
        pump(engine, buffer, backend, .4, until=lambda: engine.state == 'LABEL')
        assert engine.future is not None and not training.acquire()
        pump(engine, buffer, backend, 1., until=lambda: engine.future is None)
        assert training.acquire()
    finally:
        training.release()
        engine.submit('close')
        pump(engine, buffer, backend, 3., until=lambda: engine.status()['can_close'])
        engine.shutdown()
