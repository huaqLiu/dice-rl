"""复用相机预览、录制和回放；拖拽采集结束后自动请求当前位置保持。"""
from my_dice_rl.script.collect_gui import Window
from my_dice_rl.script.gui_keys import bind_button_keys
from PyQt5 import QtWidgets, QtCore
from my_dice_rl.util.config import project_path
from my_dice_rl.util.reset_motion import load_reset_pose


class DragWindow(Window):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setWindowTitle('DICE-RL · Panda 手把手拖拽示教采集')
        self.control_buttons['reset'].setText('随机复位  8')
        self.save_reset_button = QtWidgets.QPushButton('登记原始位姿  9')
        self.save_reset_button.clicked.connect(lambda: self.control('save_reset'))
        bind_button_keys(self, self.save_reset_button, '9')
        self.reset_info = QtWidgets.QLabel()
        self.reset_info.setWordWrap(True)
        self.reference_mtime, self.reference_xyz = None, None
        row = QtWidgets.QWidget()
        reset_layout = QtWidgets.QHBoxLayout(row)
        reset_layout.setContentsMargins(0, 0, 0, 0)
        reset_layout.addWidget(self.save_reset_button)
        reset_layout.addWidget(self.reset_info, 1)
        layout = self.centralWidget().layout()
        layout.insertWidget(3, row)

    def control(self, action):
        if action == 'save_reset' and (self.recorder.active or self.recording_locked
                or self.recording_lock_future is not None or self.closing):
            return
        if action == 'pause' and self.recorder.active:
            self.recorder.stop('failed', '人工暂停拖拽，本条未标注成功')
        super().control(action)

    def stop(self, status):
        # Set sampler stop before controller hold/disabled telemetry can invalidate the tail.
        super().stop(status)
        if self.backend is not None:
            self.backend.request_control('pause')

    def update_status(self):
        if self.backend is not None:
            self.backend.gui_tick()
        was_active = self.recorder.active or self.was_active
        super().update_status()
        if hasattr(self, 'save_reset_button'):
            telemetry = self.buffers.latest('teleop')
            state = telemetry.value if telemetry else {}
            self.save_reset_button.setEnabled(not (self.recorder.active or self.recording_locked
                or self.recording_lock_future is not None or self.closing
                or state.get('resetting') or state.get('enabled')))
            try:
                c = self.config['reset']
                path = project_path(c['reference_file'])
                mtime = path.stat().st_mtime_ns
                if mtime != self.reference_mtime:
                    self.reference_xyz = load_reset_pose(path, self.config['base_frame'], self.config['ee_frame'])[0]
                    self.reference_mtime = mtime
                xyz = ', '.join(f'{v*1000:.1f}' for v in self.reference_xyz)
                text = f"原点XYZ/mm：[ {xyz} ]；各轴±{c['position_noise_m']*1000:g} mm，姿态总偏角≤{c['rotation_noise_deg']:g}°"
                if state.get('reset_goal') is not None:
                    goal = ', '.join(f'{v*1000:.1f}' for v in state['reset_goal'][:3])
                    text += f'；本次目标：[ {goal} ] mm'
                self.reset_info.setText(text)
            except (OSError, ValueError, KeyError) as exc:
                self.reset_info.setText('复位参考未就绪：'+str(exc))
        if was_active and not self.recorder.active and self.backend is not None:
            self.backend.request_control('pause')

    def closeEvent(self, event):
        reset = getattr(self.backend, 'reset_controller', None)
        if reset is not None and reset.busy:
            self.closing = True
            reset.cancel('关闭窗口，复位已停止')
            self.status.setText('正在停止复位并确认恢复保持控制器……')
            event.ignore()
            QtCore.QTimer.singleShot(200, self.close)
            return
        super().closeEvent(event)
