"""Virtual-time fake-robot checks; no ROS initialization or physical publishers."""
import json
import time
from pathlib import Path

import h5py
import numpy as np
import pytest
import yaml

from my_dice_rl.agent.eval.auto_cycle import AutoCycleExecutor
from my_dice_rl.agent.eval.dp_executor import DPExecutor
from my_dice_rl.agent.eval.dp_policy import DemoPolicy
from my_dice_rl.env.real.policy_demo import DemoBackend
from my_dice_rl.env.real.policy_observation import ObservationBuffer
from my_dice_rl.script.rl_collect_gui import load_config
from my_dice_rl.tests.test_dp_execution import fill
from my_dice_rl.util.config import PROJECT_ROOT
from my_dice_rl.util.rl_config import execution_limits
from my_dice_rl.util.rl_coordination import DeviceLease


class LowPolicy(DemoPolicy):
    identity = dict(DemoPolicy.identity, global_step=0, trained_rollouts=[])
    def predict(self, history):
        actions, noise = super().predict(history)
        actions[:, 2] = .059
        return actions, noise


class StationaryPolicy(DemoPolicy):
    def predict(self, history):
        actions, noise = super().predict(history)
        actions[:, 2] = history[-1]['eef'].value[2]
        return actions, noise


class Runtime:
    def __init__(self, tmp_path, policy=None, **settings):
        self.config, collector = load_config(PROJECT_ROOT/'cfg/execute_dp.yaml')
        self.config.update(data_root=str(tmp_path/'data'), min_free_gb=0.,
            device_lock=str(tmp_path/'device.lock'), max_translation_from_reset=.5,
            max_episode_s=2., auto_cycle=dict(wait_for_training=False, max_episodes=2,
                batch_episodes=1, latest_policy_index=str(tmp_path/'latest.json'), **settings))
        ref = tmp_path/'review.yaml'
        ref.write_text(yaml.safe_dump(dict(reset_base_frame='panda_link0', reset_ee_frame='panda_hand_tcp',
            reset_position_m=[.45, 0., .064], reset_orientation_xyzw=[1., 0., 0., 0.])))
        self.config['deployment_review'] = str(ref)
        self.buffer = ObservationBuffer()
        self.backend = DemoBackend(self.buffer)
        self.backend.pose[2] = .064
        self.engine = AutoCycleExecutor(self.config, collector, self.buffer, self.backend,
                                       simulated=True, policy=policy or LowPolicy())
        self.now = time.monotonic()
        self.trace = []
        self.step(12)

    def step(self, count=1):
        for _ in range(count):
            self.now += .02
            fill(self.buffer, self.backend, self.now)
            self.engine.tick(self.now)
            self.trace.append((self.engine.state, self.backend.pose.copy()))
            time.sleep(.002)

    def until(self, predicate, steps=2000):
        for _ in range(steps):
            self.step()
            if predicate():
                return
        raise AssertionError(self.engine.status())

    def start(self):
        self.engine.submit('auto_start', held_confirmed=True)
        self.until(lambda: self.engine.state == 'RUNNING')

    def close(self):
        self.engine.submit('close')
        self.until(lambda: self.engine.status()['can_close'])
        self.engine.shutdown()


def test_automatic_success_reset_and_reuse_without_gripper_open(tmp_path):
    r = Runtime(tmp_path)
    try:
        r.start()
        r.until(lambda: r.engine.auto_count == 2 and r.engine.state == 'RESET')
        assert not r.engine.auto_active
        assert r.backend.gripper_target == 0.
        assert not r.backend.has_lock
        np.testing.assert_allclose(r.backend.pose[:3], [.45, 0., .064], atol=.003)
        episodes = list((tmp_path/'data/_demo').rglob('metadata.json'))
        assert len(episodes) == 2
        for p in episodes:
            m = json.loads(p.read_text())
            assert m['status'] == 'success' and m['eligible_for_rl']
            assert m['stop_diagnostics']['automatic_label']['threshold_m'] == .06
            with h5py.File(p.with_name('rollout.h5')) as f:
                assert f['transitions/reward'][-1] == 1
                assert f['transitions/terminated'][-1]
                assert f['observations/pose'][-1, 2] < .06
        retreat = [pose for state, pose in r.trace if state == 'AUTO_RETRACT']
        assert retreat
        for pose in retreat:
            np.testing.assert_allclose(pose[:2], [.45, 0.], atol=1e-12)
            np.testing.assert_allclose(pose[3:], [1., 0., 0., 0.], atol=1e-12)
    finally:
        r.close()


def test_height_is_strict_and_uses_actual_pose(tmp_path):
    r = Runtime(tmp_path)
    try:
        r.start()
        r.backend.pose[2] = .060
        r.engine.last_command = r.backend.pose.copy()
        r.step()
        assert r.engine.state == 'RUNNING'  # Exactly 60 mm does not qualify.
        r.backend.pose[2] = .0599
        r.step()
        assert r.engine.state == 'STOPPING'
        assert r.engine.stop_reason == 'automatic height success'
    finally:
        r.close()


def test_timeout_saves_failed_with_bootstrap(tmp_path):
    r = Runtime(tmp_path, StationaryPolicy())
    try:
        r.start()
        r.until(lambda: r.engine.auto_count >= 1)
        p = r.engine.last_path
        m = json.loads((p/'metadata.json').read_text())
        assert m['status'] == 'failed' and m['label']['reason'] == 'timeout'
        with h5py.File(p/'rollout.h5') as f:
            assert f['transitions/reward'][-1] == 0
            assert f['transitions/truncated'][-1]
            assert not f['transitions/terminated'][-1]
    finally:
        r.close()


def test_force_guard_wins_over_success_height(tmp_path):
    r = Runtime(tmp_path)
    try:
        r.start()
        r.now += .02
        r.backend.pose[2] = .059
        fill(r.buffer, r.backend, r.now)
        r.buffer.put('robot', dict(q=np.zeros(7), dq=np.zeros(7), wrench=np.array([25., 0, 0, 0, 0, 0])),
                     1., r.now)
        r.engine.tick(r.now)
        assert not r.engine.auto_active and r.engine.unsafe
        r.until(lambda: r.engine.state == 'RESET')
        assert not r.engine.writer.meta['eligible_for_rl']
        assert not any(s.startswith('AUTO_RE') for s, _ in r.trace)
    finally:
        r.close()


@pytest.mark.parametrize('phase', ['AUTO_RETRACT', 'AUTO_RETURN', 'AUTO_WAIT'])
def test_stop_at_every_automatic_phase_cannot_restart(tmp_path, phase):
    r = Runtime(tmp_path)
    r.engine.auto_config['wait_for_training'] = phase == 'AUTO_WAIT'
    try:
        r.start()
        r.until(lambda: r.engine.state == phase)
        before = len(list((tmp_path/'data/_demo').rglob('metadata.json')))
        r.engine.submit('stop')
        r.until(lambda: r.engine.state == 'RESET')
        r.step(20)
        assert not r.engine.auto_active and not r.backend.has_lock
        assert len(list((tmp_path/'data/_demo').rglob('metadata.json'))) == before
    finally:
        r.close()


def test_blocked_retraction_halts_without_next_episode(tmp_path):
    r = Runtime(tmp_path)
    try:
        r.start()
        r.until(lambda: r.engine.state == 'AUTO_RETRACT')
        fixed = r.backend.pose.copy()
        r.engine.auto_config['tracking_position_error_m'] = .001
        original = r.backend.publish
        def blocked(pose):
            original(pose)
            r.backend.pose = fixed.copy()
        r.backend.publish = blocked
        r.until(lambda: not r.engine.auto_active and r.engine.state == 'RESET')
        assert r.engine.auto_count == 1
        assert '跟踪误差' in r.engine.message
    finally:
        r.close()


def test_training_wait_requires_new_weights_covering_this_batch(tmp_path):
    r = Runtime(tmp_path)
    r.engine.auto_config['wait_for_training'] = True
    index = Path(r.engine.auto_config['latest_policy_index'])
    try:
        r.start()
        r.until(lambda: r.engine.state == 'AUTO_WAIT')
        required = list(r.engine.auto_batch)
        def load(path, deadline):
            version = json.loads(Path(path).read_text())['global_step']
            p = LowPolicy()
            p.path = 'FAKE_NEW_POLICY'
            p.identity = dict(global_step=version, trained_rollouts=[] if version == 1 else required)
            return p
        r.engine._load_when_idle = load
        index.write_text(json.dumps(dict(global_step=1, sha256='first')))
        r.until(lambda: r.engine.policy.identity.get('global_step') == 1 and r.engine.future is None)
        r.step(5)
        assert r.engine.state == 'AUTO_WAIT' and r.engine.auto_count == 1
        index.write_text(json.dumps(dict(global_step=2, sha256='second')))
        r.until(lambda: r.engine.state == 'RUNNING')
        assert r.engine.policy.identity['global_step'] == 2
        assert r.engine.auto_batch == []
    finally:
        r.close()


def test_auto_settings_are_bound_into_export_limits():
    c, _ = load_config(PROJECT_ROOT/'cfg/execute_rl.yaml')
    first = execution_limits(c)
    c['auto_cycle']['success_z_m'] = .059
    assert execution_limits(c) != first


def test_automatic_start_needs_confirmation_and_actual_reset(tmp_path):
    r = Runtime(tmp_path)
    try:
        with pytest.raises(ValueError, match='确认'):
            r.engine._request('auto_start', {}, r.now)
        r.backend.pose[2] = .059
        fill(r.buffer, r.backend, r.now)
        with pytest.raises(ValueError, match='起点'):
            r.engine._request('auto_start', dict(held_confirmed=True), r.now)
        assert not r.engine.auto_active and not r.backend.has_lock and not r.backend.commands
    finally:
        r.close()


def test_manual_mode_still_waits_for_human_label(tmp_path):
    r = Runtime(tmp_path)
    try:
        r.engine.submit('ready', held_confirmed=True)
        r.until(lambda: r.engine.state == 'READY')
        r.engine.submit('start')
        r.until(lambda: r.engine.state == 'LABEL')
        assert not r.engine.auto_active and not r.engine.writer.closing
    finally:
        r.close()


def test_open_gripper_prediction_halts_automatic_session(tmp_path):
    class Opening(StationaryPolicy):
        action_dim = 10  # Legacy gripper-controlling policy still cannot open during auto cycles.
        def predict(self, history):
            a, n = super().predict(history)
            a, n = np.c_[a, np.zeros(len(a))], np.c_[n, np.zeros(len(n))]
            a[:, 9] = .08
            return a, n
    r = Runtime(tmp_path, Opening())
    try:
        r.start()
        r.until(lambda: not r.engine.auto_active and r.engine.state == 'RESET')
        assert r.backend.gripper_target == 0.
        assert '松爪' in r.engine.writer.meta['label']['reason']
        assert not r.engine.writer.meta['eligible_for_rl']
    finally:
        r.close()


def test_training_timeout_returns_control_without_more_episodes(tmp_path):
    r = Runtime(tmp_path)
    r.engine.auto_config.update(wait_for_training=True, training_wait_timeout_s=.2)
    try:
        r.start()
        r.until(lambda: not r.engine.auto_active and r.engine.state == 'RESET')
        assert r.engine.auto_count == 1 and not r.backend.has_lock
        assert '等待训练超时' in r.engine.message
    finally:
        r.close()


@pytest.mark.parametrize('stop_pending', [False, True])
def test_writer_lag_waits_without_motion_or_false_failure(tmp_path, stop_pending):
    r = Runtime(tmp_path)
    try:
        r.start()
        writer = r.engine.writer
        class LaggedWriter:
            lagging = True
            @property
            def frames(self):
                return 0 if self.lagging else writer.frames
            def __getattr__(self, name):
                return getattr(writer, name)
        delayed = LaggedWriter()
        r.engine.writer = delayed
        r.until(lambda: r.engine.state == 'AUTO_LABEL')
        assert r.engine.auto_active and r.engine.auto_count == 0
        if stop_pending:
            r.engine.submit('stop')
            r.step()
            assert r.engine.state == 'LABEL' and not r.engine.auto_active
        delayed.lagging = False
        if not stop_pending:
            # This test controls virtual write lag. Let the real async HDF5 worker
            # drain before advancing the accelerated clock into label timeout.
            deadline = time.monotonic()+3.
            while writer.frames < 2 and time.monotonic() < deadline:
                time.sleep(.01)
            assert writer.frames >= 2
            r.until(lambda: r.engine.state == 'AUTO_RETRACT')
            assert r.engine.auto_count == 1
    finally:
        r.close()


def test_gui_key_nine_arms_and_escape_disarms(tmp_path):
    from PyQt5 import QtWidgets, QtCore, QtTest
    from my_dice_rl.script.rl_collect_gui import RLWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    r = Runtime(tmp_path)
    window = RLWindow(r.engine, r.buffer, demo=True)
    window.show()
    try:
        window.activateWindow()
        QtTest.QTest.qWait(30)
        window.confirmation.setChecked(True)
        window.auto_start.setFocus()
        QtTest.QTest.keyClick(window.auto_start, QtCore.Qt.Key_9)
        r.until(lambda: r.engine.state == 'RUNNING')
        assert r.engine.auto_active
        QtTest.QTest.keyClick(window, QtCore.Qt.Key_Escape)
        r.until(lambda: r.engine.state == 'LABEL')
        assert not r.engine.auto_active
    finally:
        window.timer.stop()
        r.close()
        window.closing = True
        window.close()
        window.deleteLater()
        app.processEvents()


def test_initial_gpu_busy_waits_at_ready_then_starts(tmp_path):
    r = Runtime(tmp_path)
    holder = DeviceLease(r.config['device_lock'])
    try:
        assert holder.acquire()
        r.engine.submit('auto_start', held_confirmed=True)
        r.until(lambda: r.engine.state == 'READY')
        r.step(10)
        assert r.engine.auto_active and not r.backend.commands
        holder.release()
        r.until(lambda: r.engine.state == 'RUNNING')
    finally:
        holder.release()
        r.close()


def test_training_partial_boundary_and_incomplete_round_do_not_deadlock(tmp_path):
    r = Runtime(tmp_path)
    r.engine.auto_config.update(wait_for_training=True, batch_episodes=2, max_episodes=3)
    index = Path(r.engine.auto_config['latest_policy_index'])
    try:
        r.start()
        r.until(lambda: r.engine.state == 'AUTO_WAIT')
        required = list(r.engine.auto_batch)
        def load(path, deadline):
            v = json.loads(Path(path).read_text())['global_step']
            p = LowPolicy()
            p.identity = dict(global_step=v, trained_rollouts=required[:1], training_round_complete=v==2)
            return p
        r.engine._load_when_idle = load
        index.write_text(json.dumps(dict(global_step=1, sha256='partial-checkpoint')))
        r.until(lambda: r.engine.policy.identity.get('global_step') == 1 and r.engine.future is None)
        r.step(5)
        assert r.engine.state == 'AUTO_WAIT'  # A mid-round checkpoint cannot restart motion.
        index.write_text(json.dumps(dict(global_step=2, sha256='round-complete')))
        r.until(lambda: r.engine.state == 'RUNNING')
        assert r.engine.auto_batch == required[1:]  # Keep untrained paths until the next full batch.
    finally:
        r.close()
