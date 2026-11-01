"""Manual reset/teleop requests never bypass policy execution and labelling."""
from concurrent.futures import Future
from types import SimpleNamespace
import time

import pytest

from my_dice_rl.agent.eval.dp_executor import DPExecutor
from my_dice_rl.agent.eval.dp_policy import DemoPolicy
from my_dice_rl.env.real.policy_demo import DemoBackend
from my_dice_rl.env.real.policy_observation import ObservationBuffer
from my_dice_rl.script.rl_collect_gui import RLWindow, load_config
from my_dice_rl.tests.test_dp_execution import pump
from my_dice_rl.util.config import PROJECT_ROOT


def make_runtime(tmp_path, backend_type=DemoBackend):
    config, collector = load_config(PROJECT_ROOT/'cfg/execute_dp.yaml')
    config.update(data_root=str(tmp_path), min_free_gb=0., device_lock=str(tmp_path/'device.lock'))
    buffer = ObservationBuffer()
    backend = backend_type(buffer)
    return DPExecutor(config, collector, buffer, backend, True, DemoPolicy()), buffer, backend


@pytest.mark.parametrize('state', ['READY', 'LOCKING', 'RUNNING', 'STOPPING', 'LABEL', 'SAVING', 'FAULT'])
def test_manual_requests_rejected_until_round_finished(tmp_path, state):
    engine, _, backend = make_runtime(tmp_path)
    try:
        engine.state = state
        for operation in ['manual_enable', 'manual_reset']:
            with pytest.raises(ValueError, match='先停止策略'):
                engine._request(operation, {}, time.monotonic())
        assert not backend.manual_actions
    finally:
        engine.state = 'RESET'
        engine.shutdown()


def test_stop_is_sent_immediately_and_repeated_after_delayed_reset_ack(tmp_path):
    class DelayedBackend(DemoBackend):
        def request_control(self, action):
            if action != 'reset':
                return super().request_control(action)
            self.manual_actions.append(action)
            self.reset_ack = Future()
            return self.reset_ack
    engine, buffer, backend = make_runtime(tmp_path, DelayedBackend)
    try:
        pump(engine, buffer, backend, .25)
        engine.submit('manual_reset')
        pump(engine, buffer, backend, .04)
        assert backend.manual_actions == ['reset'] and engine.status()['manual']['busy']
        engine.submit('ready', held_confirmed=True)
        pump(engine, buffer, backend, .04)
        assert engine.state == 'RESET' and not backend.has_lock
        engine.submit('stop')
        pump(engine, buffer, backend, .04)
        assert backend.manual_actions == ['reset', 'pause']
        assert not engine.status()['manual']['can_enable']
        backend.reset_ack.set_result(SimpleNamespace(success=True, message='reset started'))
        pump(engine, buffer, backend, .04)
        assert backend.manual_actions == ['reset', 'pause', 'pause']
        assert engine.status()['manual']['can_enable'] and not backend.manual_enabled
    finally:
        engine.shutdown()


def test_manual_control_waits_for_teleop_unlock_and_reset_completion(tmp_path):
    engine, buffer, backend = make_runtime(tmp_path)
    engine.simulated = False  # Still a DemoBackend; requires real-mode telemetry checks.
    try:
        assert not engine.status()['manual']['can_reset']
        for flags in [dict(policy_locked=True), dict(resetting=True), dict(recording_locked=True)]:
            buffer.put('teleop', flags)
            assert not engine.status()['manual']['can_reset']
            with pytest.raises(ValueError):
                engine._request('manual_reset', {}, time.monotonic())
        buffer.put('teleop', dict(policy_locked=False, resetting=False, recording_locked=False))
        assert engine.status()['manual']['can_reset']
        engine._request('manual_enable', {}, time.monotonic())
        engine._poll_manual()
        assert backend.manual_actions == ['enable']
    finally:
        engine.shutdown()


def test_close_prevents_new_manual_requests_even_when_pause_service_is_unavailable(tmp_path):
    class Unavailable(DemoBackend):
        def request_control(self, action):
            raise ValueError('pause service unavailable')
    engine, _, _ = make_runtime(tmp_path, Unavailable)
    try:
        with pytest.raises(ValueError, match='unavailable'):
            engine._request('close', {}, time.monotonic())
        assert engine.closing and not engine.status()['manual']['can_enable']
        with pytest.raises(ValueError, match='窗口正在关闭'):
            engine._request('manual_enable', {}, time.monotonic())
    finally:
        engine.shutdown()


def test_execution_gui_numeric_manual_keys_and_round_interlock(tmp_path):
    from PyQt5 import QtWidgets, QtCore, QtTest
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    class Engine:
        config = dict(data_root=str(tmp_path), task='gear_insertion')
        state = 'RESET'
        requests = []
        def submit(self, operation, **kwargs):
            self.requests.append(operation)
        def status(self):
            manual = self.state == 'RESET'
            return dict(state=self.state, message='', model='FAKE', busy=False, frames=3,
                path='', can_close=True, manual=dict(available=True, can_enable=manual,
                can_reset=manual, can_pause=manual, busy=False, resetting=False, enabled=False))
    engine = Engine()
    window = RLWindow(engine, ObservationBuffer(), True)
    try:
        window.show(); window.activateWindow(); QtTest.QTest.qWait(30)
        window.confirmation.setChecked(True)
        window.manual_enable.setFocus()
        QtTest.QTest.keyClick(window.manual_enable, QtCore.Qt.Key_1)
        assert engine.requests == ['manual_enable'] and not window.confirmation.isChecked()
        QtTest.QTest.keyClick(window.reset, QtCore.Qt.Key_8)
        assert engine.requests[-1] == 'manual_reset'
        for state in ['RUNNING', 'LABEL', 'SAVING']:
            engine.state = state; window.refresh()
            QtTest.QTest.keyClick(window, QtCore.Qt.Key_8)
            QtTest.QTest.keyClick(window, QtCore.Qt.Key_1)
            assert engine.requests == ['manual_enable', 'manual_reset']
        engine.state = 'RESET'; window.refresh()
        window.notes.setFocus(); QtTest.QTest.keyClicks(window.notes, '18')
        assert window.notes.text() == '18' and len(engine.requests) == 2
        QtTest.QTest.keyClick(window.notes, QtCore.Qt.Key_Escape)
        assert engine.requests[-1] == 'stop'
    finally:
        window.timer.stop(); window.closing = True; window.close()
