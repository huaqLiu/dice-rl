"""Local Qt interface; recording and ROS callbacks run independently of previews."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time

import cv2
import h5py
import numpy as np
import yaml

# opencv-python wheels set a private Qt plugin path incompatible with system PyQt.
for key in ("QT_QPA_PLATFORM_PLUGIN_PATH", "QT_QPA_FONTDIR"):
    if "cv2" in os.environ.get(key, ""):
        os.environ.pop(key)
from PyQt5 import QtCore, QtGui, QtWidgets

from my_dice_rl.util.recording import Recorder, STREAMS, StreamBuffer, letterbox, policy_image, mark_reviewed_success
from my_dice_rl.script.gui_keys import bind_button_keys, install_numeric_editor_filter


def show_rgb(label, rgb):
    rgb = letterbox(rgb, max(320, label.width()), max(240, label.height()))
    h, w = rgb.shape[:2]
    img = QtGui.QImage(rgb.data, w, h, rgb.strides[0], QtGui.QImage.Format_RGB888).copy()
    label.setPixmap(QtGui.QPixmap.fromImage(img))


class Playback(QtWidgets.QDialog):
    def __init__(self, path, parent):
        super().__init__(parent)
        self.setWindowTitle("演示回放 · " + path.parent.name)
        self.resize(1040, 490)
        self.file = h5py.File(path, "r")
        self.count = len(self.file["timestamp"])
        self.seen_last = False
        layout = QtWidgets.QVBoxLayout(self)
        images = QtWidgets.QHBoxLayout()
        self.labels = []
        for name in ("腕部 D435i", "外部 Femto Bolt"):
            label = QtWidgets.QLabel(name)
            label.setMinimumSize(400, 300)
            label.setAlignment(QtCore.Qt.AlignCenter)
            images.addWidget(label)
            self.labels.append(label)
        layout.addLayout(images)
        self.detail = QtWidgets.QLabel()
        layout.addWidget(self.detail)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setRange(0, max(0, self.count - 1))
        self.slider.valueChanged.connect(self.render)
        layout.addWidget(self.slider)
        self.play = QtWidgets.QPushButton("播放 / 暂停")
        self.play.clicked.connect(self.toggle)
        layout.addWidget(self.play)
        config = json.loads(self.file.attrs["config_json"])
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(round(1000 / config["record_hz"]))
        self.timer.timeout.connect(self.advance)
        if self.count:
            self.render(0)

    def render(self, index):
        try:
            for label, key in zip(self.labels, ("wrist", "external")):
                bgr = cv2.imdecode(self.file[f"rgb/{key}"][index], cv2.IMREAD_COLOR)
                if bgr is None:
                    raise ValueError("JPEG 无法解码")
                show_rgb(label, cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
            actual = self.file["gripper_width"][index, 0] * 1000
            target = self.file["gripper_target_width"][index, 0] * 1000
            self.detail.setText(f"帧 {index + 1} / {self.count}    夹爪实际 {actual:.1f} mm    目标 {target:.1f} mm")
            if index == self.count - 1:
                self.seen_last = True
        except Exception as exc:
            self.timer.stop()
            self.detail.setText(f"读取失败：{exc}")

    def toggle(self):
        self.timer.stop() if self.timer.isActive() else self.timer.start()

    def advance(self):
        if self.slider.value() >= self.count - 1:
            self.timer.stop()
        else:
            self.slider.setValue(self.slider.value() + 1)

    def done(self, result):
        self.timer.stop()
        self.file.close()
        super().done(result)


class Window(QtWidgets.QMainWindow):
    def __init__(self, config, buffers, demo=False, backend=None):
        super().__init__()
        self.config, self.buffers = config, buffers
        self.backend = backend
        self.closing = False
        self.recorder = Recorder(config, buffers, demo)
        self.was_active = False
        self.recording_lock_future = None
        self.recording_locked = False
        self.unlock_future = None
        install_numeric_editor_filter(self)
        self.reviewed_paths = set()
        self.setWindowTitle("DICE-RL · Panda 齿轮装配采集" + (" [模拟模式]" if demo else ""))
        self.resize(1240, 880)
        self.setStyleSheet("""
            QMainWindow, QDialog {background:#111827; color:#e5e7eb;}
            QWidget {font-size:14px; color:#e5e7eb;}
            QLineEdit, QListWidget {background:#1f2937; border:1px solid #374151; padding:8px; border-radius:5px;}
            QPushButton {background:#334155; padding:12px; border-radius:6px; font-weight:600;}
            QPushButton:hover {background:#475569;}
            QPushButton:disabled {color:#6b7280; background:#1f2937;}
            QLabel#title {font-size:24px; font-weight:700;}
            QLabel#preview {background:#030712; border:1px solid #374151; border-radius:8px;}
        """)
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(22, 18, 22, 18)
        title = QtWidgets.QLabel("Panda  /  齿轮装配示教采集")
        title.setObjectName("title")
        layout.addWidget(title)
        layout.addWidget(QtWidgets.QLabel("齿轮已夹持 → 对准 → 插入  ·  左键启用/暂停  ·  右键切换夹爪" +
                         ("    模拟数据保存至 _demo，不会混入真实数据" if demo else "")))
        controls = QtWidgets.QHBoxLayout()
        self.control_buttons = {}
        for text, shortcut, action in (("启用遥操作", "1", "enable"),
                                       ("暂停 / 停止复位", "2", "pause"),
                                       ("登记当前夹持", "3", "hold"),
                                       ("回到初始位姿", "8", "reset")):
            button = QtWidgets.QPushButton(text + "  " + shortcut)
            bind_button_keys(self, button, shortcut, *(['0', 'Esc'] if action == 'pause' else []))
            button.clicked.connect(lambda checked=False, a=action: self.control(a))
            controls.addWidget(button)
            self.control_buttons[action] = button
        self.crop_preview = QtWidgets.QCheckBox("查看策略画面 224×224")
        self.crop_preview.setChecked(True)
        controls.addWidget(self.crop_preview)
        layout.addLayout(controls)
        layout.addWidget(QtWidgets.QLabel('8：直线复位，不改变夹爪。请先结束录制、退出轴并确认路径畅通；2 / 0 / Esc 可停止。'))
        row = QtWidgets.QHBoxLayout()
        self.previews = {}
        for key, title in (("wrist", "腕部 · D435i"), ("external", "机械臂前方 · Femto Bolt")):
            column = QtWidgets.QVBoxLayout()
            column.addWidget(QtWidgets.QLabel(title))
            label = QtWidgets.QLabel("等待相机图像")
            label.setObjectName("preview")
            label.setMinimumSize(400, 280)
            label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Expanding)
            label.setAlignment(QtCore.Qt.AlignCenter)
            self.previews[key] = label
            column.addWidget(label, 1)
            row.addLayout(column)
        layout.addLayout(row, 1)
        self.health = QtWidgets.QLabel()
        self.health.setWordWrap(True)
        layout.addWidget(self.health)
        self.robot_detail = QtWidgets.QLabel("等待机器人状态")
        layout.addWidget(self.robot_detail)
        form = QtWidgets.QHBoxLayout()
        form.addWidget(QtWidgets.QLabel("任务"))
        self.task = QtWidgets.QLineEdit(config["task"])
        self.task.setMaximumWidth(220)
        form.addWidget(self.task)
        form.addWidget(QtWidgets.QLabel("本条备注"))
        self.notes = QtWidgets.QLineEdit()
        self.notes.setPlaceholderText("可填写齿轮规格、初始偏移、卡住原因等")
        form.addWidget(self.notes)
        layout.addLayout(form)
        self.path_label = QtWidgets.QLabel(str(config["data_root"]))
        self.path_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.path_label.setWordWrap(True)
        layout.addWidget(self.path_label)
        buttons = QtWidgets.QHBoxLayout()
        self.start_button = QtWidgets.QPushButton("开始录制  4")
        self.start_button.clicked.connect(self.start)
        bind_button_keys(self, self.start_button, '4')
        buttons.addWidget(self.start_button)
        self.stop_buttons = []
        for text, key, status in (("结束 · 成功", "5", "success"),
                                  ("结束 · 失败", "6", "failed"), ("丢弃本条", "7", "discarded")):
            button = QtWidgets.QPushButton(text + "  " + key)
            bind_button_keys(self, button, key)
            button.clicked.connect(lambda checked=False, s=status: self.stop(s))
            buttons.addWidget(button)
            self.stop_buttons.append(button)
        layout.addLayout(buttons)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addWidget(QtWidgets.QLabel("结束录制仅结束数据记录；机械臂与夹爪继续由 SpaceMouse 控制。"))
        history_row = QtWidgets.QHBoxLayout()
        history_row.addWidget(QtWidgets.QLabel("历史演示 · 双击回放"))
        refresh = QtWidgets.QPushButton("刷新列表")
        refresh.clicked.connect(self.refresh_history)
        history_row.addWidget(refresh)
        self.review_button = QtWidgets.QPushButton("回放后确认成功")
        self.review_button.setEnabled(False)
        self.review_button.clicked.connect(self.review_selected)
        history_row.addWidget(self.review_button)
        folder = QtWidgets.QPushButton("打开数据目录")
        folder.clicked.connect(self.open_folder)
        history_row.addWidget(folder)
        layout.addLayout(history_row)
        self.history = QtWidgets.QListWidget()
        self.history.setMaximumHeight(140)
        self.history.itemDoubleClicked.connect(self.playback)
        self.history.currentItemChanged.connect(self.update_review_button)
        layout.addWidget(self.history)
        self.task.editingFinished.connect(self.refresh_history)
        self.refresh_history()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.update_status)
        self.timer.start(100)
        self.update_status()

    def control(self, action):
        if self.recorder.demo:
            self.status.setText("模拟模式：不发送机器人指令")
            return
        if action in ('hold', 'reset') and (self.recorder.active or self.recording_lock_future is not None
                                           or self.recording_locked or self.closing):
            return
        if self.backend is not None:
            self.backend.request_control(action)

    def start(self):
        if (self.closing or self.recorder.active or self.recording_lock_future is not None
                or self.recording_locked or self.unlock_future is not None):
            return
        try:
            self.recorder.ready()
            if self.backend is not None:
                self.recording_lock_future = self.backend.request_recording_lock(True)
            else:
                self._begin_recording()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "无法开始录制", str(exc))

    def _begin_recording(self):
        try:
            self.recorder.start(self.task.text().strip())
            self.was_active = True
        except Exception as exc:
            self.recorder.message = '无法开始录制：' + str(exc)

    def _update_recording_lock(self):
        if self.unlock_future is not None and self.unlock_future.done():
            future, self.unlock_future = self.unlock_future, None
            try:
                if not future.result().success:
                    raise ValueError(future.result().message)
                self.recording_locked = False
            except Exception as exc:
                self.recorder.message = '释放录制锁失败：' + str(exc)
        if self.recording_lock_future is not None and self.recording_lock_future.done():
            future, self.recording_lock_future = self.recording_lock_future, None
            try:
                response = future.result()
                if not response.success:
                    raise ValueError(response.message)
                self.recording_locked = True
                if not self.closing:
                    self._begin_recording()
            except Exception as exc:
                self.recorder.message = '无法开始录制：' + str(exc)
        if self.recording_locked and not self.recorder.active and self.unlock_future is None:
            try:
                self.unlock_future = self.backend.request_recording_lock(False)
            except ValueError as exc:
                self.recorder.message = str(exc)

    def stop(self, status):
        if self.recorder.active:
            self.recorder.stop(status, self.notes.text())

    def update_status(self):
        self._update_recording_lock()
        now = time.monotonic()
        for key, label in self.previews.items():
            sample = self.buffers.latest(key)
            if sample is not None:
                image = policy_image(sample.value) if self.crop_preview.isChecked() else sample.value
                show_rgb(label, image)
        health = []
        names = dict(wrist="腕部相机", external="外部相机", eef="末端位姿", target="目标位姿",
                     gripper="夹爪状态", teleop="遥操作", robot="关节状态")
        for key in STREAMS:
            sample = self.buffers.latest(key)
            age = now - sample.received if sample else float("inf")
            color = "#34d399" if age <= self.config["max_age_s"] else "#fb7185"
            detail = f"{age * 1000:.0f} ms" if sample else "未连接"
            health.append(f'<span style="color:{color}">● {names[key]} {detail}</span>')
        self.health.setText(" &nbsp; ".join(health))
        ready, reason = True, "设备就绪，可以开始"
        try:
            self.recorder.ready()
        except ValueError as exc:
            ready, reason = False, str(exc)
        active = self.recorder.active
        busy = active or self.recording_lock_future is not None or self.recording_locked or self.closing
        self.start_button.setEnabled(ready and not busy)
        self.task.setEnabled(not busy)
        telemetry = self.buffers.latest('teleop')
        resetting = bool(telemetry and telemetry.value.get('resetting'))
        for action in ('hold', 'reset'):
            self.control_buttons[action].setEnabled(not busy and not resetting)
        self.control_buttons['enable'].setEnabled(not resetting and not self.closing)
        for button in self.stop_buttons:
            button.setEnabled(active and not self.recorder.stop_event.is_set())
        if self.was_active and not active:
            self.refresh_history()
            self.notes.clear()
        self.was_active = active
        root = Path(self.config["data_root"])
        disk_path = root
        while not disk_path.exists():
            disk_path = disk_path.parent
        free = shutil.disk_usage(disk_path).free / 1e9
        frames = self.recorder.writer.count if self.recorder.writer else 0
        text = f"{self.recorder.message} · {frames} 帧 · {frames / self.config['record_hz']:.1f} s · 剩余 {free:.1f} GB"
        if self.recorder.writer is not None:
            text += (f"\n已采 {self.recorder.sampled_frames} / 已写 {frames} · "
                     f"待写 {self.recorder.pending.qsize()}/{self.recorder.queue_capacity} 帧")
        if not active:
            text += "\n" + reason
        self.status.setText(text)
        self.path_label.setText("保存路径：" + str(self.recorder.writer.path if self.recorder.writer else root))
        eef, grip = self.buffers.latest("eef"), self.buffers.latest("gripper")
        if eef is not None and grip is not None:
            p = eef.value[:3]
            telemetry = self.buffers.latest('teleop')
            state = telemetry.value if telemetry else {}
            enabled = '复位中' if state.get('resetting') else ("启用" if state.get('enabled') else "暂停")
            force = np.linalg.norm(state.get('force', [0., 0., 0.]))
            message = state.get('reset_message') or (self.backend.control_message if self.backend else "")
            self.robot_detail.setText(f"末端 / m：{p[0]:.3f}, {p[1]:.3f}, {p[2]:.3f}  "
                f"夹爪：{grip.value * 1000:.1f} mm  外力：{force:.1f} N  遥操作：{enabled}  {message}")

    def refresh_history(self):
        self.history.clear()
        root = Path(self.config["data_root"])
        if self.recorder.demo:
            root /= "_demo"
        task = self.task.text().strip()
        # Use only direct task children: never interpret user input as a glob/path.
        import re
        if not re.fullmatch(r"[\w-]{1,64}", task):
            return
        for path in sorted((root / task).glob("*/episode_*/metadata.json"), reverse=True)[:300]:
            try:
                m = json.loads(path.read_text())
                item = QtWidgets.QListWidgetItem(
                    f"{path.parent.parent.name} / {path.parent.name}    {m['status']}    {m['frames']} 帧    {m.get('notes', '')}")
                item.setData(QtCore.Qt.UserRole, str(path.parent / "trajectory.h5"))
                self.history.addItem(item)
            except (OSError, ValueError, KeyError):
                continue

    def playback(self, item):
        if self.recorder.active:
            return
        path = Path(item.data(QtCore.Qt.UserRole))
        if not path.exists():
            QtWidgets.QMessageBox.information(self, "尚未完成", "本条未完成保存，partial 文件不会进入训练。")
            return
        try:
            player = Playback(path, self)
            player.exec_()
            if player.seen_last:
                self.reviewed_paths.add(str(path.parent))
            self.update_review_button()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "回放失败", str(exc))

    def update_review_button(self, *_):
        item = self.history.currentItem()
        allowed = False
        if item is not None and not self.recorder.active:
            path = Path(item.data(QtCore.Qt.UserRole)).parent
            if str(path) in self.reviewed_paths:
                try:
                    metadata = json.loads((path / "metadata.json").read_text())
                    allowed = (metadata.get("status") == "invalid"
                               and "相机" in metadata.get("reason", ""))
                except (OSError, ValueError):
                    pass
        self.review_button.setEnabled(allowed)

    def review_selected(self):
        item = self.history.currentItem()
        if item is None or self.recorder.active:
            return
        path = Path(item.data(QtCore.Qt.UserRole)).parent
        if str(path) not in self.reviewed_paths:
            return
        reply = QtWidgets.QMessageBox.question(
            self, "人工确认成功",
            "你已回放这条演示，并确认在自动停止前任务已完成吗？\n"
            "确认后该条会进入下一次训练导出；原中止原因会保留在审核记录中。",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        if reply != QtWidgets.QMessageBox.Yes:
            return
        try:
            mark_reviewed_success(path, "人工回放确认任务已完成")
            self.refresh_history()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "审核失败", str(exc))

    def open_folder(self):
        path = Path(self.config["data_root"])
        path.mkdir(parents=True, exist_ok=True)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))

    def closeEvent(self, event):
        if self.backend is not None and not self.closing:
            self.closing = True
            self.backend.request_control('pause')
            event.ignore()
            QtCore.QTimer.singleShot(300, self.close)
            return
        if self.recorder.active:
            self.recorder.stop("failed", "窗口关闭时结束录制")
            self.status.setText("正在完成保存，请稍候……")
            event.ignore()
            QtCore.QTimer.singleShot(200, self.close)
            return
        self._update_recording_lock()
        if self.recording_lock_future is not None or self.recording_locked or self.unlock_future is not None:
            event.ignore()
            QtCore.QTimer.singleShot(200, self.close)
            return
        event.accept()


def demo_loop(buffers, stop):
    while not stop.is_set():
        now = time.monotonic()
        for key, color in (("wrist", (38, 80, 110)), ("external", (40, 95, 65))):
            rgb = np.full((480, 640, 3), color, np.uint8)
            cv2.circle(rgb, (320 + int(80 * np.sin(now)), 240), 45, (220, 170, 40), -1)
            cv2.putText(rgb, "SIMULATED / " + key, (25, 45), cv2.FONT_HERSHEY_SIMPLEX, .8, (255, 255, 255), 2)
            buffers.put(key, rgb, time.time(), now)
        pose = [.45, .02 * np.sin(now), .3, 1., 0., 0., 0.]
        buffers.put("eef", pose, time.time(), now)
        buffers.put("target", pose, time.time(), now)
        buffers.put("gripper", .08, time.time(), now)
        buffers.put("robot", dict(q=np.zeros(7), dq=np.zeros(7)), time.time(), now)
        buffers.put("teleop", dict(enabled=True, safety_latched=False, gripper_known=True,
                    gripper_target_width=.08, gripper_busy=False, gripper_error="",
                    gripper_command_mode="move",
                    gripper_speed=.03, gripper_force=20.), time.time(), now)
        stop.wait(1 / 30)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(Path(__file__).resolve().parents[1] / 'cfg/collect.yaml'))
    parser.add_argument("--data-root", help="Override output folder")
    parser.add_argument("--demo", action="store_true", help="Local synthetic buffers; no ROS publishers")
    parser.add_argument("--smoke-seconds", type=float, default=0, help="Demo-only automated recording test")
    parser.add_argument("--screenshot", help="Optional path for a UI screenshot")
    args = parser.parse_args()
    if args.smoke_seconds and (not args.demo or args.smoke_seconds < 2):
        parser.error("--smoke-seconds requires --demo and >=2 seconds")
    with open(args.config) as f:
        config = yaml.safe_load(f)
    root = Path(__file__).resolve().parents[1]
    data_root = Path(args.data_root or config['data_root']).expanduser()
    config['data_root'] = str(data_root if data_root.is_absolute() else root / data_root)
    if not 1 <= config["record_hz"] <= 30 or config["max_age_s"] <= 0:
        parser.error("invalid recording frequency or freshness threshold")
    app = QtWidgets.QApplication(sys.argv[:1])
    buffers = StreamBuffer()
    shutdown = threading.Event()
    node = None
    executor = None
    if args.demo:
        worker = threading.Thread(target=demo_loop, args=(buffers, shutdown), daemon=True)
    else:
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from my_dice_rl.env.real.ros_backend import CollectorNode, spin_until_shutdown
        rclpy.init(args=[])
        node = CollectorNode(config, buffers)
        executor = SingleThreadedExecutor()
        executor.add_node(node)
        worker = threading.Thread(target=spin_until_shutdown, args=(executor,), daemon=True)
    worker.start()
    window = Window(config, buffers, args.demo, node)
    window.show()
    if args.screenshot:
        QtCore.QTimer.singleShot(1500, lambda: window.grab().save(args.screenshot))
    if args.smoke_seconds:
        QtCore.QTimer.singleShot(500, window.start)
        QtCore.QTimer.singleShot(round(500 + args.smoke_seconds * 1000), lambda: window.stop("success"))
        QtCore.QTimer.singleShot(round(1500 + args.smoke_seconds * 1000), window.close)
    try:
        app.exec_()
    finally:
        shutdown.set()
        if executor:
            executor.shutdown(timeout_sec=2)
        worker.join(timeout=2)
        if node:
            node.destroy_node()
            rclpy.try_shutdown()
    if args.smoke_seconds:
        print(window.recorder.message)
        print(window.recorder.last_path)
        if window.recorder.writer is None or window.recorder.writer.metadata["status"] != "success":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
