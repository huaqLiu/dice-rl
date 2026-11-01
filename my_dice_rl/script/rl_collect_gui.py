"""DP execution and human-labelled rollouts. Demo mode has no ROS publishers."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time

import numpy as np
import cv2
import torch
import yaml

from my_dice_rl.agent.eval.dp_executor import DPExecutor
from my_dice_rl.agent.eval.dp_policy import DemoPolicy
from my_dice_rl.env.real.policy_observation import ObservationBuffer
from my_dice_rl.util.config import PROJECT_ROOT, project_path
from my_dice_rl.script.collect_gui import show_rgb, QtCore, QtGui, QtWidgets
from my_dice_rl.script.gui_keys import bind_button_keys, install_numeric_editor_filter


def load_config(path):
    path = Path(path).expanduser().resolve()
    config = yaml.safe_load(path.read_text())
    collector_path = (path.parent/config.pop('collector_config')).resolve()
    collector = yaml.safe_load(collector_path.read_text())
    config['data_root'] = str(project_path(config['data_root']))
    config.setdefault('inference_backend', 'process')
    if config['inference_backend'] not in ('process', 'thread'):
        raise ValueError('inference_backend must be process or thread')
    for key in ('device_lock', 'deployment_review'):
        if config.get(key):
            config[key] = str(project_path(config[key]))
    for key in ('control_hz', 'max_age_s', 'max_sensor_skew_s', 'control_timeout_s',
                'max_inference_latency_s', 'max_plan_age_s', 'max_episode_s', 'handover_timeout_s',
                'max_linear_speed', 'max_angular_speed', 'max_position_error', 'max_orientation_error',
                'max_goal_position_error', 'max_goal_orientation_error', 'max_translation_from_reset',
                'max_rotation_from_reset', 'reset_drift_m', 'reset_drift_rad', 'max_force', 'max_torque',
                'gripper_speed', 'gripper_force', 'gripper_width_change', 'gripper_min_interval_s',
                'gripper_timeout_s', 'cancel_timeout_s'):
        if not isinstance(config[key], (float, int)) or not np.isfinite(config[key]) or config[key] <= 0:
            raise ValueError(f'{key} must be positive and finite')
    if not 0 <= config['grasp_threshold'] <= .08 or config['min_free_gb'] < 0:
        raise ValueError('Invalid gripper threshold or free-space limit')
    if 'max_gripper_age_s' in config:
        value = config['max_gripper_age_s']
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or value <= 0:
            raise ValueError('max_gripper_age_s must be positive and finite')
    if config['task'] != collector['task']:
        raise ValueError('Collection and execution task must agree')
    return config, collector


class RLWindow(QtWidgets.QMainWindow):
    names = dict(RESET='待人工复位', LOCKING='交接控制权', READY='已就绪', RUNNING='执行中',
                 STOPPING='停止中', LABEL='等待人工标注', SAVING='保存中', FAULT='停止未确认：需现场检查')

    def __init__(self, engine, buffer, demo=False):
        super().__init__()
        self.engine, self.buffer, self.demo = engine, buffer, demo
        self.closing = False
        install_numeric_editor_filter(self)
        self.setWindowTitle('DICE-RL · DP/RL 执行与人工标注' + (' [模拟模式]' if demo else ''))
        self.resize(1180, 860)
        self.setStyleSheet('''
            QMainWindow {background:#101827;} QWidget {color:#e5e7eb;font-size:14px;}
            QLineEdit,QListWidget {background:#1f2937;padding:8px;border:1px solid #374151;}
            QPushButton {background:#334155;padding:12px;border-radius:5px;}
            QPushButton:hover {background:#475569;}
            QPushButton:disabled {color:#6b7280;background:#1f2937;}
            QLabel#preview {background:#030712;} QLabel#title {font-size:24px;font-weight:700;}
        ''')
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        title = QtWidgets.QLabel('Panda / DP 与 RL 策略执行 · 人工复位 · 奖励标注')
        title.setObjectName('title')
        layout.addWidget(title)
        layout.addWidget(QtWidgets.QLabel('每轮：人工复位 → 3 就绪 → 4 执行；插入完成后立即 2 停止 → 5 保存成功（奖励 1）'))
        model_row = QtWidgets.QHBoxLayout()
        self.checkpoint = QtWidgets.QLineEdit()
        self.checkpoint.setPlaceholderText('正式 DP .pt、RL 部署包 .pt 或 latest_policy.json；模拟模式可用演示策略')
        model_row.addWidget(self.checkpoint)
        self.browse = QtWidgets.QPushButton('加载 DP / RL 策略')
        self.browse.clicked.connect(self.load_policy)
        model_row.addWidget(self.browse)
        layout.addLayout(model_row)
        previews = QtWidgets.QHBoxLayout()
        self.previews = {}
        for key, text in (('wrist', '腕部 D435i · 策略视野'), ('external', '前方 Femto Bolt · 策略视野')):
            column = QtWidgets.QVBoxLayout()
            column.addWidget(QtWidgets.QLabel(text))
            label = QtWidgets.QLabel('等待图像')
            label.setAlignment(QtCore.Qt.AlignCenter)
            label.setObjectName('preview')
            label.setMinimumSize(400, 280)
            label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Expanding)
            column.addWidget(label)
            previews.addLayout(column)
            self.previews[key] = label
        layout.addLayout(previews, 1)
        self.health, self.robot, self.status = QtWidgets.QLabel(), QtWidgets.QLabel(), QtWidgets.QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.health)
        layout.addWidget(self.robot)
        manual_row = QtWidgets.QHBoxLayout()
        self.manual_enable = QtWidgets.QPushButton('启用 SpaceMouse 遥操作 1')
        self.reset = QtWidgets.QPushButton('回到初始位姿 8')
        for button, key, operation in ((self.manual_enable, '1', 'manual_enable'),
                                       (self.reset, '8', 'manual_reset')):
            bind_button_keys(self, button, key)
            button.clicked.connect(lambda checked=False, action=operation: self.manual_control(action))
            manual_row.addWidget(button)
        self.manual_state = QtWidgets.QLabel()
        self.manual_state.setWordWrap(True)
        self.manual_state.setMinimumWidth(240)
        manual_row.addWidget(self.manual_state, 1)
        layout.addLayout(manual_row)
        hint = QtWidgets.QLabel('1 启用遥操作，8 直线回初始位姿，2 / 0 / Esc 暂停。'
                              '先保存标注、退出轴并确认路径；复位不改变夹爪。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.confirmation = QtWidgets.QCheckBox('我已完成本轮人工复位：齿轮夹稳、工装就位、机械臂处于起始区域')
        layout.addWidget(self.confirmation)
        controls = QtWidgets.QHBoxLayout()
        self.ready = QtWidgets.QPushButton('复位就绪确认 3')
        bind_button_keys(self, self.ready, '3')
        self.ready.clicked.connect(lambda: self.engine.submit('ready', held_confirmed=self.confirmation.isChecked()))
        controls.addWidget(self.ready)
        self.start = QtWidgets.QPushButton('开始执行 4')
        bind_button_keys(self, self.start, '4')
        self.start.clicked.connect(lambda: self.engine.submit('start', notes=self.notes.text()))
        controls.addWidget(self.start)
        self.stop = QtWidgets.QPushButton('停止策略 / 暂停遥操 / 停止复位 2 / 0 / Esc')
        bind_button_keys(self, self.stop, '2', '0', 'Esc')
        self.stop.setStyleSheet('background:#9f1239;')
        self.stop.clicked.connect(lambda: self.engine.submit('stop'))
        controls.addWidget(self.stop)
        layout.addLayout(controls)
        layout.addWidget(self.status)
        self.notes = QtWidgets.QLineEdit()
        self.notes.setPlaceholderText('插入深度、成功依据、失败原因或复位情况')
        layout.addWidget(self.notes)
        labels = QtWidgets.QHBoxLayout()
        self.label_buttons = []
        for text, shortcut, outcome in (('成功：末端奖励 1', '5', 'success'),
                                        ('失败：奖励 0', '6', 'failed'),
                                        ('放弃：不用于 RL', '7', 'aborted')):
            button = QtWidgets.QPushButton(text+' '+shortcut)
            bind_button_keys(self, button, shortcut)
            button.clicked.connect(lambda checked=False, value=outcome:
                self.engine.submit('label', outcome=value, notes=self.notes.text()))
            labels.addWidget(button)
            self.label_buttons.append(button)
        layout.addLayout(labels)
        self.path = QtWidgets.QLabel()
        self.path.setWordWrap(True)
        self.path.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.path)
        layout.addWidget(QtWidgets.QLabel('停止为软件停止；复位仅由按 8 显式请求。仅标注停止前已实际完成的结果。'))
        self.history = QtWidgets.QListWidget()
        self.history.setMaximumHeight(100)
        self.history.itemDoubleClicked.connect(self.open_episode)
        layout.addWidget(self.history)
        self.last_state = None
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(100)
        self.refresh()

    def load_policy(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, '加载 DP / RL 策略（仅复位阶段）',
            str(PROJECT_ROOT/'log_dir'), '策略 / 最新策略索引 (*.pt *.json)')
        if path:
            self.checkpoint.setText(path)
            self.engine.submit('load', path=path)

    def manual_control(self, operation):
        self.confirmation.setChecked(False)
        self.engine.submit(operation)

    def refresh(self):
        status = self.engine.status()
        state, now = status['state'], time.monotonic()
        manual = status.get('manual', {})
        for key, preview in self.previews.items():
            sample = self.buffer.latest(key)
            if sample is not None:
                show_rgb(preview, sample.value)
        health = []
        for key in self.buffer.keys:
            sample = self.buffer.latest(key)
            age = now-sample.received if sample else float('inf')
            health.append(f'{key}: {age*1000:.0f}ms' if sample else key+': 未连接')
        self.health.setText(' | '.join(health))
        pose, robot, grip = (self.buffer.latest(k) for k in ('eef', 'robot', 'gripper'))
        if pose and robot and grip:
            xyz = pose.value[:3]
            force = np.linalg.norm(robot.value['wrench'][:3])
            self.robot.setText(f'末端 XYZ / m：{xyz[0]:.3f}, {xyz[1]:.3f}, {xyz[2]:.3f}  '
                               f'夹爪：{grip.value*1000:.1f}mm  外力：{force:.1f}N')
        manual_busy = manual.get('busy', False) or manual.get('resetting', False)
        self.manual_enable.setEnabled(manual.get('can_enable', False) and not manual.get('enabled', False) and not self.closing)
        self.reset.setEnabled(manual.get('can_reset', False) and not self.closing)
        self.manual_state.setText(('正在复位' if manual.get('resetting') else
            'SpaceMouse 遥操作已启用' if manual.get('enabled') else
            'SpaceMouse 已暂停' if manual.get('available') else '等待遥操作节点')+
            ' · '+manual.get('message', ''))
        self.ready.setEnabled(state == 'RESET' and bool(status['model']) and not status['busy'] and not manual_busy and not self.closing)
        self.start.setEnabled(state == 'READY' and not self.closing)
        self.stop.setEnabled(state in ('LOCKING', 'READY', 'RUNNING') or manual.get('can_pause', False))
        self.browse.setEnabled(state == 'RESET' and not status['busy'] and not self.closing)
        self.confirmation.setEnabled(state == 'RESET' and not manual_busy and not self.closing)
        for button in self.label_buttons:
            button.setEnabled(state == 'LABEL' and status['frames'] >= 2)
        if state == 'RESET' and self.last_state not in (None, 'RESET'):
            self.confirmation.setChecked(False)
            self.refresh_history()
        self.last_state = state
        self.status.setText(f'{self.names[state]} · {status["frames"]} 帧\n{status["message"]}')
        timing = status.get('inference_timing', {})
        if timing:
            self.status.setText(self.status.text()+f'\n推理进程：计算 {timing["compute_ms"]:.0f} ms · '
                f'传入 {timing["queue_ms"]:.0f} ms · 返回 {timing["return_ms"]:.0f} ms · '
                f'接收调度 {timing.get("poll_ms", 0):.0f} ms')
        self.path.setText('轨迹：'+status['path'])
        if status['model'] and not self.checkpoint.text():
            self.checkpoint.setText(status['model'])
        if self.closing and status['can_close']:
            self.close()

    def refresh_history(self):
        self.history.clear()
        root = Path(self.engine.config['data_root'])/('_demo' if self.demo else 'real')/self.engine.config['task']
        for path in sorted(root.glob('*/metadata.json'), reverse=True)[:100]:
            try:
                meta = json.loads(path.read_text())
                item = QtWidgets.QListWidgetItem(f'{path.parent.name}  {meta["status"]}  '
                    f'{meta.get("transitions", 0)} transitions  eligible={meta.get("eligible_for_rl", False)}')
                item.setData(QtCore.Qt.UserRole, str(path.parent))
                self.history.addItem(item)
            except (OSError, ValueError, KeyError):
                pass

    def open_episode(self, item):
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(item.data(QtCore.Qt.UserRole)))

    def closeEvent(self, event):
        if not self.closing:
            self.closing = True
            self.engine.submit('close')
            event.ignore()
        elif not self.engine.status()['can_close']:
            event.ignore()
        else:
            event.accept()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=str(PROJECT_ROOT/'cfg/execute_dp.yaml'))
    parser.add_argument('--checkpoint')
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--smoke-seconds', type=float, default=0)
    parser.add_argument('--screenshot')
    args = parser.parse_args()
    if args.smoke_seconds and (not args.demo or args.smoke_seconds < 2):
        parser.error('--smoke-seconds requires --demo and at least 2 seconds')
    config, collector = load_config(args.config)
    # Match offline timing and leave CPU capacity for the 50 Hz control thread.
    torch.set_num_threads(2)
    cv2.setNumThreads(1)
    buffer = ObservationBuffer()
    stop_event, executor, node = threading.Event(), None, None
    app = QtWidgets.QApplication(sys.argv[:1])
    if args.demo:
        from my_dice_rl.env.real.policy_demo import DemoBackend, feed_demo
        backend = DemoBackend(buffer)
        feed_thread = threading.Thread(target=feed_demo, args=(buffer, backend, stop_event), daemon=True)
        feed_thread.start()
    else:
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from my_dice_rl.env.real.policy_backend import PolicyROSNode
        from my_dice_rl.env.real.ros_backend import spin_until_shutdown
        rclpy.init(args=[])
        node = PolicyROSNode(collector, config, buffer)
        backend = node
    engine = DPExecutor(config, collector, buffer, backend, args.demo, DemoPolicy() if args.demo else None)
    if args.demo:
        def control_loop():
            while not stop_event.is_set():
                engine.tick()
                stop_event.wait(1/config['control_hz'])
        worker = threading.Thread(target=control_loop, daemon=True)
    else:
        node.create_timer(1/config['control_hz'], engine.tick)
        executor = SingleThreadedExecutor()
        executor.add_node(node)
        worker = threading.Thread(target=spin_until_shutdown, args=(executor,), daemon=True)
    worker.start()
    window = RLWindow(engine, buffer, args.demo)
    window.show()
    checkpoint = args.checkpoint or config['checkpoint']
    if checkpoint:
        engine.submit('load', path=str(project_path(checkpoint)))
    if args.smoke_seconds:
        def demo_ready():
            window.confirmation.setChecked(True)
            engine.submit('ready', held_confirmed=True)
        QtCore.QTimer.singleShot(500, demo_ready)
        QtCore.QTimer.singleShot(900, lambda: engine.submit('start', notes='synthetic UI check'))
        QtCore.QTimer.singleShot(round(900+args.smoke_seconds*1000), lambda: engine.submit('stop'))
        QtCore.QTimer.singleShot(round(1400+args.smoke_seconds*1000),
                                lambda: engine.submit('label', outcome='success'))
        QtCore.QTimer.singleShot(round(2300+args.smoke_seconds*1000), window.close)
    if args.screenshot:
        QtCore.QTimer.singleShot(1800, lambda: window.grab().save(args.screenshot))
    try:
        app.exec_()
    finally:
        stop_event.set()
        if executor:
            executor.shutdown(timeout_sec=2)
        worker.join(timeout=2)
        if args.demo:
            feed_thread.join(timeout=2)
        engine.shutdown()
        if node:
            node.destroy_node()
            rclpy.try_shutdown()
    if args.smoke_seconds:
        path = engine.last_path
        print(path)
        if path is None or json.loads((path/'metadata.json').read_text()).get('outcome') != 'success':
            raise SystemExit(1)


if __name__ == '__main__':
    main()
