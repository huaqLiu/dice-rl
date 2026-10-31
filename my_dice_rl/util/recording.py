"""ROS/Qt independent recording, causal sampling and atomic episode storage."""
from collections import deque
import copy
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import re
import shutil
import threading
import time

import cv2
import h5py
import numpy as np

SCHEMA = "dice_panda_gear_dual_rgb_v1"
STREAMS = ("wrist", "external", "eef", "target", "gripper", "teleop", "robot")


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, path)


def letterbox(rgb, width, height):
    """Preserve the full field of view; black-pad instead of stretching/cropping."""
    h, w = rgb.shape[:2]
    scale = min(width / w, height / h)
    rw, rh = max(1, round(w * scale)), max(1, round(h * scale))
    small = cv2.resize(rgb, (rw, rh), interpolation=cv2.INTER_AREA)
    result = np.zeros((height, width, 3), dtype=np.uint8)
    x, y = (width - rw) // 2, (height - rh) // 2
    result[y:y + rh, x:x + rw] = small
    return result


def policy_image(rgb, width=224, height=224):
    """DICE-RL-Robot get_image_transform: resize to fill, then center crop."""
    import math
    h, w = rgb.shape[:2]
    if w / h >= width / height:
        rh, rw = height, math.ceil(height / h * w)
    else:
        rw, rh = width, math.ceil(width / w * h)
    resized = cv2.resize(rgb, (rw, rh), interpolation=(
        cv2.INTER_AREA if rh <= h and rw <= w else cv2.INTER_LINEAR))
    x, y = (rw - width) // 2, (rh - height) // 2
    return resized[y:y + height, x:x + width].copy()


@dataclass(frozen=True)
class Sample:
    received: float  # host CLOCK_MONOTONIC seconds
    source: float    # unmodified ROS header timestamp; not used across clock domains
    value: object


class StreamBuffer:
    def __init__(self):
        self.lock = threading.Lock()
        # Keep enough camera history to match a delayed frame to its peer.
        self.buffers = {k: deque(maxlen=16 if k in ("wrist", "external") else 120) for k in STREAMS}
        self.errors = {}
        self.camera_info = {}

    def put(self, key, value, source=0.0, received=None):
        item = Sample(time.monotonic() if received is None else received, source, value)
        with self.lock:
            self.buffers[key].append(item)
            self.errors.pop(key, None)

    def error(self, key, message):
        with self.lock:
            self.errors[key] = str(message)

    def latest(self, key):
        with self.lock:
            return self.buffers[key][-1] if self.buffers[key] else None

    def snapshot(self, tick, max_age, max_skew):
        with self.lock:
            # A protective pause stops target publication. Show the cause before its stale target.
            telemetry = next((s for s in reversed(self.buffers["teleop"]) if s.received <= tick), None)
            if telemetry is not None and telemetry.value.get('resetting'):
                raise ValueError('正在复位，禁止录制示范')
            if ("teleop" not in self.errors and telemetry is not None
                    and tick - telemetry.received <= max_age
                    and not telemetry.value.get("enabled") and telemetry.value.get("pause_reason")):
                raise ValueError("遥操作已暂停：" + telemetry.value["pause_reason"])
            result = {}
            for key, buffer in self.buffers.items():
                if key in self.errors:
                    raise ValueError(f"{key}: {self.errors[key]}")
                if key in ("wrist", "external"):
                    continue
                sample = next((s for s in reversed(buffer) if s.received <= tick), None)
                if sample is None or tick - sample.received > max_age:
                    raise ValueError(f"{key}: 等待数据或数据超时")
                result[key] = sample
            camera_candidates = {}
            for key in ("wrist", "external"):
                camera_candidates[key] = [
                    sample for sample in self.buffers[key]
                    if 0 <= tick - sample.received <= max_age
                ]
                if not camera_candidates[key]:
                    raise ValueError(f"{key}: 等待数据或数据超时")
            # Pair buffered images by host receive time. The freshest image from
            # each camera can have >max_skew separation when one callback is
            # delayed by a frame; an older causal pair may still be valid.
            pairs = (
                (wrist, external)
                for wrist in camera_candidates["wrist"]
                for external in camera_candidates["external"]
                if abs(wrist.received - external.received) <= max_skew
            )
            pair = min(pairs, key=lambda p: (
                max(tick - p[0].received, tick - p[1].received),
                abs(p[0].received - p[1].received),
            ), default=None)
            if pair is None:
                raise ValueError("两路相机在允许时间内没有可配对的图像")
            result["wrist"], result["external"] = pair
        telemetry = result["teleop"].value
        if not telemetry.get("gripper_known"):
            raise ValueError("夹爪目标未知：登记现有夹持，或用右键操作夹爪")
        if not telemetry.get("enabled") or telemetry.get("safety_latched"):
            raise ValueError("遥操作未启用或保护已触发")
        if telemetry.get("gripper_error"):
            raise ValueError("夹爪指令失败：" + telemetry["gripper_error"])
        return result


class EpisodeWriter:
    def __init__(self, session, config, camera_info=None):
        self.config = config
        session = Path(session)
        session.mkdir(parents=True, exist_ok=True)
        # Atomic mkdir prevents two collectors from overwriting an episode.
        for index in range(1, 1000000):
            path = session / f"episode_{index:06d}"
            try:
                path.mkdir()
                break
            except FileExistsError:
                continue
        else:
            raise RuntimeError("演示编号已耗尽")
        self.path = path
        self.metadata = dict(
            schema=SCHEMA, status="recording", frames=0,
            created_at=datetime.now().astimezone().isoformat(), config=config,
            camera_info=camera_info or {},
            pose_convention="base_frame -> ee_frame; [x,y,z,qx,qy,qz,qw]; metres",
            action_convention="absolute Cartesian target XYZW; gripper_mode: 0=hold_existing, 1=move, 2=grasp; hold width is measured initial width, not an accepted command",
            image_transform="RGB; resize to fill then center crop (DICE-RL-Robot); raw JPEG retained separately",
            synchronization="latest received sample <= fixed host monotonic tick; source stamps retained; no exposure synchronization guarantee",
        )
        atomic_json(path / "metadata.json", self.metadata)
        self.file = h5py.File(path / "trajectory.partial.h5", "w")
        self.file.attrs["schema"] = SCHEMA
        self.file.attrs["config_json"] = json.dumps(config)
        self.count = 0
        self.closed = False

    def _append(self, key, value):
        value = np.asarray(value)
        if key not in self.file:
            self.file.create_dataset(key, shape=(0,) + value.shape,
                                     maxshape=(None,) + value.shape, dtype=value.dtype,
                                     chunks=(64,) + value.shape)
        ds = self.file[key]
        ds.resize(self.count + 1, axis=0)
        ds[self.count] = value

    def append(self, tick, samples, wall_timestamp=None):
        started = time.monotonic()
        values = {
            "timestamp": np.float64(tick),
            "wall_timestamp": np.float64(time.time() if wall_timestamp is None else wall_timestamp),
            "robot_eef_pose": np.asarray(samples["eef"].value, dtype=np.float64),
            "target_eef_pose": np.asarray(samples["target"].value, dtype=np.float64),
            "gripper_width": np.array([samples["gripper"].value], dtype=np.float32),
            "gripper_target_width": np.array([samples["teleop"].value["gripper_target_width"]], dtype=np.float32),
            "joint_position": np.asarray(samples["robot"].value["q"], dtype=np.float64),
            "joint_velocity": np.asarray(samples["robot"].value["dq"], dtype=np.float64),
            "gripper_busy": np.uint8(samples["teleop"].value["gripper_busy"]),
            "gripper_speed": np.float32(samples["teleop"].value["gripper_speed"]),
            "gripper_force": np.float32(samples["teleop"].value["gripper_force"]),
            "gripper_mode": np.uint8({"hold_existing": 0, "move": 1, "grasp": 2}[
                samples["teleop"].value.get("gripper_command_mode", "grasp")]),
            "external_wrench": np.asarray(samples["robot"].value.get("wrench", np.zeros(6)), dtype=np.float64),
        }
        for key, value in values.items():
            if not np.all(np.isfinite(value)):
                raise ValueError(f"{key}: 非有限值")
        for key in ("robot_eef_pose", "target_eef_pose"):
            if values[key].shape != (7,) or abs(np.linalg.norm(values[key][3:]) - 1) > .02:
                raise ValueError(f"{key}: 位姿或四元数无效")
        # Encode first; a failed row remains partial and can never be exported.
        encoded = {}
        for key in ("wrist", "external"):
            rgb = policy_image(samples[key].value, self.config["save_width"], self.config["save_height"])
            ok, jpeg = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR),
                                    [cv2.IMWRITE_JPEG_QUALITY, self.config["jpeg_quality"]])
            if not ok:
                raise IOError("JPEG 编码失败")
            encoded[key] = jpeg.reshape(-1)
            if self.config.get("save_raw_rgb", False):
                ok, raw = cv2.imencode('.jpg', cv2.cvtColor(samples[key].value, cv2.COLOR_RGB2BGR),
                                      [cv2.IMWRITE_JPEG_QUALITY, self.config["jpeg_quality"]])
                if not ok:
                    raise IOError("原始 RGB 编码失败")
                encoded['raw_' + key] = raw.reshape(-1)
        encoded_at = time.monotonic()
        for key, value in values.items():
            self._append(key, value)
        for key in STREAMS:
            self._append(f"source_timestamp/{key}", np.float64(samples[key].source))
            self._append(f"receive_timestamp/{key}", np.float64(samples[key].received))
        for key, jpeg in encoded.items():
            name = f"rgb_raw/{key[4:]}" if key.startswith('raw_') else f"rgb/{key}"
            if name not in self.file:
                self.file.create_dataset(name, (0,), maxshape=(None,),
                                         dtype=h5py.vlen_dtype(np.dtype("uint8")))
            ds = self.file[name]
            ds.resize(self.count + 1, axis=0)
            ds[self.count] = jpeg
        self.count += 1
        if self.count % 10 == 0:
            self.file.flush()
        finished = time.monotonic()
        return dict(encode_ms=(encoded_at-started)*1000,
                    hdf5_write_ms=(finished-encoded_at)*1000,
                    append_total_ms=(finished-started)*1000)

    def abort(self, reason):
        """A failed row must remain partial; never rename it as a complete trajectory."""
        if self.closed:
            return self.path
        self.closed = True
        try:
            self.file.attrs['frames'] = self.count
            self.file.flush()
        finally:
            self.file.close()
        self.metadata.update(status='invalid', frames=self.count, reason=reason,
                             finished_at=datetime.now().astimezone().isoformat())
        atomic_json(self.path / 'metadata.json', self.metadata)
        return self.path

    def close(self, status, reason="", notes=""):
        if self.closed:
            return self.path
        self.closed = True
        try:
            self.file.attrs["frames"] = self.count
            self.file.flush()
        finally:
            self.file.close()
        source = self.path / "trajectory.partial.h5"
        with source.open("rb") as f:
            os.fsync(f.fileno())
        os.replace(source, self.path / "trajectory.h5")
        self.metadata.update(status=status, frames=self.count, reason=reason, notes=notes,
                             finished_at=datetime.now().astimezone().isoformat())
        atomic_json(self.path / "metadata.json", self.metadata)
        return self.path


class Recorder:
    def __init__(self, config, buffers, demo=False):
        self.config = dict(config)
        self.buffers = buffers
        self.demo = demo
        self.session = None
        self.writer = None
        self.thread = None
        self.stop_event = threading.Event()
        self.requested_status = "failed"
        self.notes = ""
        self.message = "等待设备就绪"
        self.last_path = None
        capacity = self.config.get('writer_queue_capacity', 20)
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 1:
            raise ValueError('writer_queue_capacity 必须为正整数')
        self.queue_capacity = capacity
        self.pending = queue.Queue(maxsize=capacity)
        self.sampled_frames = 0
        self.queue_peak = 0
        self.sampling_done = threading.Event()
        self._writer_error = ''

    @property
    def active(self):
        return self.thread is not None and self.thread.is_alive()

    def ready(self):
        return self.buffers.snapshot(time.monotonic(), self.config["max_age_s"],
                                     self.config["max_camera_skew_s"])

    def start(self, task):
        if self.active:
            raise ValueError("正在录制")
        if not re.fullmatch(r"[\w-]{1,64}", task):
            raise ValueError("任务名只能包含字母、数字、汉字、下划线和短横线")
        self.ready()
        root = Path(self.config["data_root"]).expanduser()
        if self.demo:
            root = root / "_demo"
        root.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(root).free < self.config["min_free_gb"] * 1e9:
            raise ValueError("磁盘剩余空间不足")
        if self.session is None or self.session.parent.name != task:
            self.session = root / task / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            self.session.mkdir(parents=True)
            atomic_json(self.session / "session.json", dict(schema=SCHEMA, task=task,
                        config=self.config, simulated=self.demo))
        with self.buffers.lock:
            info = dict(self.buffers.camera_info)
        self.writer = EpisodeWriter(self.session, {**self.config, "task": task,
                                                   "simulated": self.demo}, info)
        self.stop_event.clear()
        self.sampling_done.clear()
        self.pending = queue.Queue(maxsize=self.queue_capacity)
        self.sampled_frames, self.queue_peak = 0, 0
        self._writer_error = ''
        self._sampler_stats, self._writer_stats = {}, {}
        self.requested_status, self.notes = "failed", ""
        self.message = "正在录制"
        self.thread = threading.Thread(target=self._run, daemon=False)
        self.thread.start()

    def stop(self, status, notes=""):
        if status not in ("success", "failed", "discarded"):
            raise ValueError(status)
        if self.stop_event.is_set():
            return
        self.requested_status, self.notes = status, notes
        self.message = '已停止采样，正在完成保存……'
        self.stop_event.set()

    @staticmethod
    def _update_stats(stats, metrics):
        for key, value in metrics.items():
            item = stats.setdefault(key, dict(count=0, total_ms=0., max_ms=0.))
            item['count'] += 1
            item['total_ms'] += float(value)
            item['max_ms'] = max(item['max_ms'], float(value))

    @staticmethod
    def _summarize_stats(stats):
        return {key: dict(count=v['count'], mean_ms=v['total_ms']/v['count'], max_ms=v['max_ms'])
                for key, v in stats.items()}

    def _write(self):
        """Only this worker encodes frames and accesses the HDF5 while sampling."""
        try:
            with (self.writer.path / 'timing.jsonl').open('x', encoding='utf-8') as diagnostics:
                while not self.sampling_done.is_set() or not self.pending.empty():
                    try:
                        tick, samples, sampled_wall, enqueued, sampling_metrics = self.pending.get(timeout=.05)
                    except queue.Empty:
                        continue
                    started = time.monotonic()
                    disk_ms = 0.
                    if self.writer.count % 10 == 0:
                        check_started = time.monotonic()
                        if shutil.disk_usage(self.session).free < self.config['min_free_gb'] * 1e9:
                            raise ValueError('剩余磁盘空间低于阈值')
                        disk_ms = (time.monotonic()-check_started)*1000
                    metrics = self.writer.append(tick, samples, wall_timestamp=sampled_wall)
                    metrics.update(queue_wait_ms=(started-enqueued)*1000, disk_check_ms=disk_ms,
                                   worker_total_ms=(time.monotonic()-started)*1000)
                    self._update_stats(self._writer_stats, metrics)
                    diagnostics.write(json.dumps(dict(frame=self.writer.count-1, tick=tick,
                        sampled_wall_timestamp=sampled_wall, queue_depth=self.pending.qsize(),
                        **sampling_metrics, **metrics), allow_nan=False) + '\n')
        except Exception as exc:
            self._writer_error = '编码/写盘线程异常：' + str(exc)
            self.stop_event.set()

    def _run(self):
        writer_thread = threading.Thread(target=self._write, name='dice_demo_writer', daemon=False)
        writer_thread.start()
        period = 1.0 / self.config["record_hz"]
        tick = time.monotonic()
        status, reason = "invalid", ""
        try:
            while not self.stop_event.is_set():
                now = time.monotonic()
                if now - tick > period:
                    raise ValueError(f'采样线程调度落后一个周期：{(now-tick)*1000:.1f} ms > {period*1000:.1f} ms，已停止本条')
                sampled_wall = time.time()
                snapshot_started = time.monotonic()
                samples = self.buffers.snapshot(tick, self.config["max_age_s"],
                                                self.config["max_camera_skew_s"])
                # Queued observations must not change when a sensor reuses its array.
                samples = copy.deepcopy(samples)
                enqueued = time.monotonic()
                if enqueued-tick > period:
                    raise ValueError('观测快照/复制落后一个采样周期，已停止本条')
                metrics = dict(sampling_delay_ms=(now-tick)*1000,
                               snapshot_copy_ms=(enqueued-snapshot_started)*1000)
                try:
                    self.pending.put_nowait((tick, samples, sampled_wall, enqueued, metrics))
                except queue.Full as exc:
                    raise ValueError(f'写盘队列已满（{self.queue_capacity} 帧），已停止本条；请降低计算/存储负载') from exc
                self.sampled_frames += 1
                self.queue_peak = max(self.queue_peak, self.pending.qsize())
                self._update_stats(self._sampler_stats, metrics)
                tick += period
                self.stop_event.wait(max(0, tick - time.monotonic()))
            status = self.requested_status
        except Exception as exc:
            reason = str(exc)
            self.stop_event.set()
        finally:
            self.sampling_done.set()
        self.message = (reason + '；' if reason else '') + '正在完成已采帧的保存……'
        # Keep active/recording lock until every accepted frame is saved and closed.
        writer_thread.join()
        if self._writer_error:
            status, reason = 'invalid', self._writer_error
        elif self.writer.count != self.sampled_frames:
            status, reason = 'invalid', '已采帧数与已写帧数不一致'
        if status == 'success' and self.writer.count < self.config['min_episode_frames']:
            status, reason = 'invalid', '演示过短'
        self.writer.metadata['wall_timestamp_convention'] = 'host wall time at sampling, not delayed disk write'
        self.writer.metadata['performance'] = dict(mode='async_sampler_writer_v1',
            queue_capacity=self.queue_capacity, queue_peak=self.queue_peak,
            sampled_frames=self.sampled_frames, written_frames=self.writer.count,
            sampler=self._summarize_stats(self._sampler_stats),
            writer=self._summarize_stats(self._writer_stats), diagnostic_file='timing.jsonl')
        try:
            if self._writer_error:
                self.writer.metadata['notes'] = self.notes
                self.last_path = self.writer.abort(reason)
            else:
                self.last_path = self.writer.close(status, reason, self.notes)
            self.message = f"{status} · {self.writer.count} 帧" + (f" · {reason}" if reason else "")
        except Exception as exc:
            self.message = f"保存失败，保留 partial 文件供检查：{exc}"
        finally:
            # Release unconsumed image arrays after a writer failure.
            while not self.pending.empty():
                try:
                    self.pending.get_nowait()
                except queue.Empty:
                    break


def mark_reviewed_success(episode_path, note):
    """Manually recover a complete, camera-skew interrupted demonstration.

    This only changes the label after a human has reviewed the saved episode;
    the original invalid status and reason remain in review_history.
    """
    path = Path(episode_path)
    if not note.strip():
        raise ValueError("请记录人工确认成功的依据")
    metadata_path = path / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("schema") != SCHEMA or metadata.get("status") != "invalid":
        raise ValueError("只允许审核本程序标记为 invalid 的演示")
    if "相机" not in metadata.get("reason", ""):
        raise ValueError("该条并非相机时间差造成的中止")
    count = metadata.get("frames", 0)
    if count < metadata["config"]["min_episode_frames"]:
        raise ValueError("演示帧数不足")
    with h5py.File(path / "trajectory.h5", "r") as file:
        if file.attrs.get("schema") != SCHEMA:
            raise ValueError("轨迹格式不匹配")
        for key in ("timestamp", "robot_eef_pose", "target_eef_pose",
                    "gripper_width", "gripper_target_width", "rgb/wrist", "rgb/external"):
            if key not in file or len(file[key]) != count:
                raise ValueError(f"轨迹不完整：{key}")
    metadata.setdefault("review_history", []).append(dict(
        previous_status=metadata["status"], previous_reason=metadata["reason"],
        reviewed_at=datetime.now().astimezone().isoformat(), note=note.strip()))
    metadata["status"] = "success"
    metadata["reason"] = ""
    metadata["notes"] = (metadata.get("notes", "") + " | " + note.strip()).strip(" |")
    atomic_json(metadata_path, metadata)
    return metadata_path
