"""Kinesthetic labels and actual Qt controls; no ROS or robot motion."""
import copy
import json
import time

import h5py
import numpy as np
import pytest
import yaml
from PyQt5 import QtCore, QtTest, QtWidgets

from my_dice_rl.script.collect_drag_gui import DragWindow
from my_dice_rl.tests.test_recording import samples, fill
from my_dice_rl.util.config import PROJECT_ROOT
from my_dice_rl.util.drag_config import validate_drag_config
from my_dice_rl.util.recording import EpisodeWriter, StreamBuffer


def drag_config(tmp_path):
    config = yaml.safe_load((PROJECT_ROOT/'cfg/collect_drag.yaml').read_text())
    config.update(data_root=str(tmp_path), min_free_gb=0.)
    return config


def test_measured_labels_and_timestamps_override_unrelated_target(tmp_path):
    config = drag_config(tmp_path)
    validate_drag_config(config)
    values = samples(time.monotonic())
    # Simulate a snapshot between actual/target buffer updates, or another target publisher.
    values['target'] = type(values['target'])(values['target'].received-.05, 1., [.9, 0., .5, 0., 0., 0., 1.])
    writer = EpisodeWriter(tmp_path, config)
    writer.append(time.monotonic(), values)
    path = writer.close('success')
    with h5py.File(path/'trajectory.h5') as file:
        np.testing.assert_array_equal(file['target_eef_pose'][:], file['robot_eef_pose'][:])
        for key in ('receive_timestamp', 'source_timestamp'):
            np.testing.assert_array_equal(file[key+'/target'][:], file[key+'/eef'][:])


def test_drag_configuration_rejects_wrong_mode_or_limits(tmp_path):
    config = drag_config(tmp_path)
    for key, value in [('collection_mode', 'spacemouse'), ('action_source', 'commanded'),
                       ('controller_name', 'panda_cartesian_impedance_controller')]:
        changed = copy.deepcopy(config)
        changed[key] = value
        with pytest.raises(ValueError):
            validate_drag_config(changed)
    config['drag']['max_force_n'] = float('nan')
    with pytest.raises(ValueError):
        validate_drag_config(config)


def test_drag_gui_digits_and_end_recording_pause(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    class Backend:
        control_message = ''
        def __init__(self): self.calls = []
        def gui_tick(self): pass
        def request_control(self, action): self.calls.append(action)
    backend, buffers = Backend(), StreamBuffer()
    fill(buffers, time.monotonic())
    window = DragWindow(drag_config(tmp_path), buffers, backend=backend)
    try:
        window.show()
        window.activateWindow()
        QtTest.QTest.qWait(30)
        window.control_buttons['enable'].setFocus()
        QtTest.QTest.keyClick(window.control_buttons['enable'], QtCore.Qt.Key_1)
        assert backend.calls == ['enable']
        assert not window.control_buttons['reset'].isHidden()
        assert window.control_buttons['reset'].isEnabled()
        QtTest.QTest.keyClick(window.control_buttons['reset'], QtCore.Qt.Key_8)
        assert backend.calls == ['enable', 'reset']
        window.recording_locked = True
        window.control('reset')
        window.control('save_reset')
        assert backend.calls == ['enable', 'reset']
        window.recording_locked = False
        telemetry = dict(buffers.latest('teleop').value, enabled=False)
        buffers.put('teleop', telemetry)
        window.update_status()
        QtTest.QTest.keyClick(window.save_reset_button, QtCore.Qt.Key_9)
        assert backend.calls[-1] == 'save_reset'
        window.stop('success')
        assert backend.calls[-1] == 'pause'
    finally:
        window.timer.stop()
        window.backend = None
        window.close()
