"""Read-only ROS timing probe: no target publishers, action goals or service calls."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import threading
import time

import cv2
import numpy as np
import yaml

from my_dice_rl.env.real.policy_observation import ObservationBuffer
from my_dice_rl.util.config import PROJECT_ROOT


def summary(values):
    if not values:
        return None
    return dict(zip(('min', 'p50', 'p95', 'p99', 'max'),
                    map(float, np.percentile(values, [0, 50, 95, 99, 100]))))


class TimingBuffer(ObservationBuffer):
    def __init__(self):
        super().__init__()
        self.wall_now = time.time
        self.started = float('inf')
        self.arrivals = defaultdict(list)

    def put(self, key, value, source=0., received=None):
        received = time.monotonic() if received is None else received
        super().put(key, value, source, received)
        if received >= self.started and key in self.keys:
            self.arrivals[key].append((received, float(source), self.wall_now()-source))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=str(PROJECT_ROOT/'cfg/execute_dp.yaml'))
    parser.add_argument('--seconds', type=float, default=60.)
    parser.add_argument('--warmup', type=float, default=3.)
    parser.add_argument('--preview', action='store_true', help='Include the two-camera Qt preview workload')
    parser.add_argument('--save-events', action='store_true', help='Save camera [receive, source] times for cross-process comparison')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.seconds <= 0 or args.warmup < 0:
        parser.error('seconds must be positive; warmup must be nonnegative')
    output = Path(args.output).expanduser().resolve()
    if output.exists():
        parser.error('Choose a new output filename')
    path = Path(args.config).expanduser().resolve()
    config = yaml.safe_load(path.read_text())
    collector = yaml.safe_load((path.parent/config['collector_config']).read_text())
    collector['max_age_s'] = config['max_age_s']
    cv2.setNumThreads(1)

    import rclpy
    from rclpy.executors import SingleThreadedExecutor
    from my_dice_rl.env.real.ros_backend import CollectorNode
    from my_dice_rl.env.real.policy_backend import PolicyROSNode

    rclpy.init(args=[])
    buffer = TimingBuffer()
    costs = defaultdict(list)

    class TimingNode(CollectorNode):
        # Reuse the production crop/validation without constructing PolicyROSNode:
        # this CollectorNode has no target publisher and never calls a service.
        image = PolicyROSNode.image
        robot = PolicyROSNode.robot

        def guarded(self, key, fn, *values):
            start = time.monotonic()
            super().guarded(key, fn, *values)
            if start >= buffer.started and key in buffer.keys:
                costs[key].append((time.monotonic()-start)*1000)

    node = TimingNode(collector, buffer, node_name='dice_read_only_sync_probe')
    buffer.wall_now = lambda: node.get_clock().now().nanoseconds*1e-9
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    failures, counts, skews, control_gaps = [], Counter(), [], []
    started = time.monotonic()+args.warmup
    buffer.started = started
    end = started+args.seconds
    last_tick, next_history, next_record = None, started, started

    def check(kind, query):
        counts[kind+'_queries'] += 1
        try:
            frame = buffer.snapshot(query, config['max_age_s'], config['max_sensor_skew_s'],
                                    config.get('max_gripper_age_s'))
            stamps = [frame[k].received for k in ('wrist', 'external', 'eef', 'robot')]
            skews.append((max(stamps)-min(stamps))*1000)
        except ValueError as exc:
            counts[kind+'_failures'] += 1
            if len(failures) < 100:
                failures.append(dict(kind=kind, elapsed_s=time.monotonic()-started,
                    message=str(exc), diagnostics=getattr(exc, 'diagnostics', None)))

    def probe():
        nonlocal last_tick, next_history, next_record
        now = time.monotonic()
        if now < started:
            return
        if last_tick is not None:
            control_gaps.append((now-last_tick)*1000)
        last_tick = now
        check('current', now)
        if now >= next_history:
            check('history', now-.1)
            next_history = now+.4
        if now >= next_record:
            check('record', next_record)
            next_record += .1

    node.create_timer(1/config['control_hz'], probe)
    print('Read-only timing probe: sensors only, no robot commands.', flush=True)
    worker = None
    try:
        if args.preview:
            from my_dice_rl.script.collect_gui import QtCore, QtWidgets, show_rgb
            from my_dice_rl.env.real.ros_backend import spin_until_shutdown
            app = QtWidgets.QApplication([])
            window = QtWidgets.QWidget()
            window.setWindowTitle('只读同步诊断 · 不发送机械臂指令')
            layout = QtWidgets.QHBoxLayout(window)
            labels = {}
            for key in ('wrist', 'external'):
                labels[key] = QtWidgets.QLabel('等待 '+key)
                labels[key].setFixedSize(500, 400)
                layout.addWidget(labels[key])
            def refresh():
                for key, label in labels.items():
                    sample = buffer.latest(key)
                    if sample is not None:
                        show_rgb(label, sample.value)
                if time.monotonic() >= end:
                    app.quit()
            timer = QtCore.QTimer()
            timer.timeout.connect(refresh)
            timer.start(100)
            worker = threading.Thread(target=spin_until_shutdown, args=(executor,), daemon=True)
            worker.start()
            window.show()
            app.exec_()
        else:
            while rclpy.ok() and time.monotonic() < end:
                executor.spin_once(timeout_sec=.05)
    finally:
        executor.shutdown(timeout_sec=2.)
        if worker is not None:
            worker.join(timeout=2.)
        report = dict(read_only=True, robot_commands_sent=False, config=str(path),
            seconds=args.seconds, preview=args.preview, limits={k: config[k] for k in
                ('max_age_s', 'max_sensor_skew_s', 'max_gripper_age_s')},
            queries=dict(counts), accepted_skew_ms=summary(skews),
            control_interval_ms=summary(control_gaps), channels={},
            failures=failures, latest_errors=dict(buffer.errors))
        for key in buffer.keys:
            rows = buffer.arrivals[key]
            recv = [row[0] for row in rows]
            source = [row[1] for row in rows]
            report['channels'][key] = dict(count=len(rows),
                receive_interval_ms=summary((np.diff(recv)*1000).tolist()),
                source_interval_ms=summary((np.diff(source)*1000).tolist()),
                source_age_at_callback_ms=summary([row[2]*1000 for row in rows]),
                callback_cost_ms=summary(costs[key]))
        report['observation_channels_complete'] = all(v['count'] > 0 for v in report['channels'].values())
        if args.save_events:
            report['camera_events'] = {k: [row[:2] for row in buffer.arrivals[k]] for k in ('wrist', 'external')}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        node.destroy_node()
        rclpy.try_shutdown()
        print(json.dumps(dict(output=str(output), queries=report['queries'],
                              channels={k: v['count'] for k, v in report['channels'].items()},
                              observation_channels_complete=report['observation_channels_complete'])))
    if not report['observation_channels_complete']:
        raise SystemExit('Incomplete probe: one or more observation channels are missing; see report.')


if __name__ == '__main__':
    main()
