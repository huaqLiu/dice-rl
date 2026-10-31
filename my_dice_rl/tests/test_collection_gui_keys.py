"""Exercise real Qt key dispatch and the collection/reset mutual exclusion."""
from concurrent.futures import Future
from types import SimpleNamespace

import pytest
from PyQt5 import QtCore, QtWidgets, QtTest

from my_dice_rl.script.collect_gui import Window
from my_dice_rl.script.gui_keys import bind_button_keys, install_numeric_editor_filter
from my_dice_rl.tests.test_recording import config, fill
from my_dice_rl.util.recording import StreamBuffer
import time


@pytest.fixture
def app():
    instance = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield instance


def test_digits_trigger_once_and_remain_available_in_notes(app):
    window = QtWidgets.QWidget()
    install_numeric_editor_filter(window)
    layout = QtWidgets.QVBoxLayout(window)
    button = QtWidgets.QPushButton('reset 8')
    stop = QtWidgets.QPushButton('stop Esc')
    editor = QtWidgets.QLineEdit()
    for widget in (button, stop, editor):
        layout.addWidget(widget)
    bind_button_keys(window, button, '8')
    bind_button_keys(window, stop, 'Esc')
    calls = []
    button.clicked.connect(lambda: calls.append('reset'))
    stop.clicked.connect(lambda: calls.append('stop'))
    window.show()
    window.activateWindow()
    QtTest.QTest.qWait(30)
    button.setFocus()
    QtTest.QTest.keyClick(button, QtCore.Qt.Key_8)
    assert calls == ['reset']
    editor.setFocus()
    QtTest.QTest.keyClicks(editor, '123456780')
    assert editor.text() == '123456780'
    assert calls == ['reset']
    QtTest.QTest.keyClick(editor, QtCore.Qt.Key_Escape)
    assert calls == ['reset', 'stop']
    button.setEnabled(False)
    editor.clearFocus()
    QtTest.QTest.keyClick(window, QtCore.Qt.Key_8)
    assert calls == ['reset', 'stop']
    window.close()


def test_collection_waits_for_lock_and_blocks_reset_until_saved(app, tmp_path):
    class Backend:
        control_message = ''
        def __init__(self):
            self.controls, self.locks = [], []
        def request_control(self, action):
            self.controls.append(action)
        def request_recording_lock(self, locked):
            future = Future()
            self.locks.append((locked, future))
            return future
    buffers, backend = StreamBuffer(), Backend()
    fill(buffers, time.monotonic())
    window = Window(config(tmp_path), buffers, backend=backend)
    try:
        window.start()
        assert not window.recorder.active and backend.locks[0][0]
        window.control('reset')
        assert not backend.controls
        backend.locks[0][1].set_result(SimpleNamespace(success=True))
        window.update_status()
        assert window.recorder.active
        window.control('reset')
        assert not backend.controls
        window.stop('discarded')
        window.recorder.thread.join(timeout=3.)
        window.update_status()
        assert not backend.locks[-1][0]
        window.control('reset')
        assert not backend.controls
        backend.locks[-1][1].set_result(SimpleNamespace(success=True))
        window.update_status()
        window.control('reset')
        assert backend.controls == ['reset']
    finally:
        window.timer.stop()
        if window.recorder.active:
            window.recorder.stop('discarded')
            window.recorder.thread.join(timeout=3.)
        window.backend = None
        window.close()
