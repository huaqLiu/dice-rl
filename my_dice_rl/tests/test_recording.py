import json
from pathlib import Path
import time

import h5py
import numpy as np
import pytest

from my_dice_rl.util.recording import EpisodeWriter, Recorder, Sample, STREAMS, StreamBuffer, letterbox, policy_image, mark_reviewed_success


def config(tmp_path):
    return dict(data_root=str(tmp_path), task="pick_place", record_hz=10.,
                save_width=64, save_height=48, jpeg_quality=92, max_age_s=.2,
                max_camera_skew_s=.06, min_free_gb=0, min_episode_frames=16,
                base_frame="panda_link0", ee_frame="panda_hand_tcp")


def samples(tick):
    pose = [.4, .1, .3, 0., 0., 0., 1.]
    values = dict(wrist=np.full((24, 32, 3), [200, 20, 10], np.uint8),
                  external=np.full((24, 32, 3), [20, 200, 10], np.uint8),
                  eef=pose, target=pose, gripper=.04,
                  teleop=dict(enabled=True, safety_latched=False, gripper_known=True,
                    gripper_target_width=0., gripper_busy=False, gripper_error="",
                    gripper_speed=.03, gripper_force=20.),
                  robot=dict(q=np.zeros(7), dq=np.zeros(7)))
    return {key: Sample(tick - .01, 1700000000 + tick, value) for key, value in values.items()}


def fill(buffers, tick):
    for key, item in samples(tick).items():
        buffers.put(key, item.value, item.source, item.received)


def test_causal_sampling_and_stale_rejection():
    buffers = StreamBuffer()
    fill(buffers, 100)
    buffers.put("target", "future target", 123, 100.02)
    assert buffers.snapshot(100, .2, .06)["target"].value != "future target"
    with pytest.raises(ValueError, match="超时"):
        buffers.snapshot(101, .2, .06)


def test_skew_unknown_gripper_and_disable_rejected():
    buffers = StreamBuffer()
    fill(buffers, 100)
    value = buffers.latest("wrist").value
    buffers.buffers["wrist"].clear()
    buffers.put("wrist", value, 100, 99.90)
    with pytest.raises(ValueError, match="没有可配对"):
        buffers.snapshot(100, .2, .06)
    fill(buffers, 100)
    teleop = dict(buffers.latest("teleop").value, gripper_known=False)
    buffers.put("teleop", teleop, 100, 100)
    with pytest.raises(ValueError, match="夹爪目标未知"):
        buffers.snapshot(100, .2, .06)
    buffers.put("teleop", dict(teleop, gripper_known=True, enabled=False), 100, 100)
    with pytest.raises(ValueError, match="未启用"):
        buffers.snapshot(100, .2, .06)


def test_matches_buffered_camera_pair_when_latest_frames_are_skewed():
    buffers = StreamBuffer()
    fill(buffers, 100)
    image = buffers.latest("wrist").value
    buffers.buffers["wrist"].clear()
    buffers.buffers["external"].clear()
    buffers.put("wrist", image, 1, 99.91)
    buffers.put("external", image, 2, 99.92)
    buffers.put("wrist", image, 3, 99.99)
    selected = buffers.snapshot(100, .2, .06)
    assert selected["wrist"].source == 1
    assert selected["external"].source == 2
    assert selected["wrist"].received <= 100
    assert selected["external"].received <= 100


def test_camera_pair_still_rejects_stale_or_unmatched_frames():
    buffers = StreamBuffer()
    fill(buffers, 100)
    image = buffers.latest("wrist").value
    buffers.buffers["wrist"].clear()
    buffers.buffers["external"].clear()
    buffers.put("wrist", image, 1, 99.99)
    buffers.put("external", image, 2, 99.90)
    with pytest.raises(ValueError, match="没有可配对"):
        buffers.snapshot(100, .2, .06)
    buffers.put("external", image, 3, 100.01)
    with pytest.raises(ValueError, match="没有可配对"):
        buffers.snapshot(100, .2, .06)
    with pytest.raises(ValueError, match="超时"):
        buffers.snapshot(100.3, .2, .06)


def test_atomic_episode_and_actual_vs_command(tmp_path):
    writer = EpisodeWriter(tmp_path / "session", config(tmp_path))
    assert not (writer.path / "trajectory.h5").exists()
    for i in range(16):
        writer.append(100 + i * .1, samples(100 + i * .1))
    path = writer.close("success")
    meta = json.loads((path / "metadata.json").read_text())
    assert meta["frames"] == 16 and meta["status"] == "success"
    assert not (path / "trajectory.partial.h5").exists()
    with h5py.File(path / "trajectory.h5") as f:
        assert f["robot_eef_pose"].shape == (16, 7)
        np.testing.assert_allclose(f["gripper_width"][:], .04)
        np.testing.assert_allclose(f["gripper_target_width"][:], 0)
        assert len(f["rgb/wrist"]) == 16
    second = EpisodeWriter(tmp_path / "session", config(tmp_path))
    assert second.path != path
    second.close("discarded")


def test_stream_loss_finishes_invalid(tmp_path):
    buffers = StreamBuffer()
    fill(buffers, time.monotonic())
    recorder = Recorder(config(tmp_path), buffers)
    recorder.start("pick_place")
    recorder.thread.join(timeout=2)
    assert not recorder.active
    assert recorder.writer.metadata["status"] == "invalid"
    assert "超时" in recorder.writer.metadata["reason"]


def test_letterbox_preserves_aspect_and_rgb():
    rgb = np.full((10, 20, 3), [255, 0, 0], np.uint8)
    result = letterbox(rgb, 20, 20)
    np.testing.assert_equal(result[:5], 0)
    np.testing.assert_equal(result[5:15], rgb)
    np.testing.assert_equal(result[15:], 0)


def test_policy_image_matches_original_center_crop_and_keeps_raw(tmp_path):
    cfg = dict(config(tmp_path), save_width=224, save_height=224, save_raw_rgb=True)
    sample = samples(100)
    original = np.full((480, 640, 3), [220, 30, 10], np.uint8)
    original[:, :60] = 0
    sample['wrist'] = Sample(99.99, 1., original)
    telemetry = dict(sample['teleop'].value, gripper_command_mode='hold_existing')
    sample['teleop'] = Sample(99.99, 1., telemetry)
    writer = EpisodeWriter(tmp_path / 'session', cfg)
    writer.append(100, sample)
    path = writer.close('success')
    import cv2
    with h5py.File(path / 'trajectory.h5') as f:
        rgb = cv2.imdecode(f['rgb/wrist'][0], cv2.IMREAD_COLOR)
        raw = cv2.imdecode(f['rgb_raw/wrist'][0], cv2.IMREAD_COLOR)
        assert rgb.shape == (224, 224, 3)
        assert raw.shape == (480, 640, 3)
        assert f['gripper_mode'][0] == 0
        assert np.all(rgb[:, 0, 2] > 200)  # Border was center-cropped, not padded.
    assert policy_image(original).shape == (224, 224, 3)


def test_path_traversal_rejected(tmp_path):
    recorder = Recorder(config(tmp_path), StreamBuffer())
    with pytest.raises(ValueError, match="任务名"):
        recorder.start("../escape")


def test_camera_interrupted_episode_can_be_audited_after_review(tmp_path):
    writer = EpisodeWriter(tmp_path / "session", config(tmp_path))
    for i in range(16):
        writer.append(100 + i * .1, samples(100 + i * .1))
    path = writer.close("invalid", "两路相机到达时间差超限")
    mark_reviewed_success(path, "人工回放确认已插入")
    metadata = json.loads((path / "metadata.json").read_text())
    assert metadata["status"] == "success"
    assert metadata["review_history"][0]["previous_reason"] == "两路相机到达时间差超限"
    with pytest.raises(ValueError, match="只允许"):
        mark_reviewed_success(path, "重复审核")


def test_failed_episode_cannot_be_relabelled_by_camera_review(tmp_path):
    writer = EpisodeWriter(tmp_path / "session", config(tmp_path))
    writer.close("invalid", "遥操作已停用")
    with pytest.raises(ValueError, match="相机"):
        mark_reviewed_success(writer.path, "未经确认")
