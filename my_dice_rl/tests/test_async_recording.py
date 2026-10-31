"""Exercise slow/failing storage without ROS or any hardware publishers."""
from contextlib import contextmanager
import json
import threading
import time
from types import SimpleNamespace

import cv2
import h5py
import numpy as np
import pytest

from my_dice_rl.script.process_gear_dataset import convert_dataset
from my_dice_rl.tests.test_recording import config, samples
from my_dice_rl.util.recording import EpisodeWriter, Recorder, Sample, StreamBuffer


class SyntheticBuffers(StreamBuffer):
    """Fresh source samples; intentionally reuse an image like some camera SDKs."""
    def __init__(self):
        super().__init__()
        self.image = np.full((48, 64, 3), [200, 20, 10], np.uint8)

    def snapshot(self, tick, max_age, max_skew):
        frame = samples(tick)
        frame['wrist'] = Sample(tick-.01, 1700000000+tick, self.image)
        return frame


def wait_until(predicate, timeout=3.):
    deadline = time.monotonic()+timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(.005)
    assert predicate(), 'Timed out waiting for recorder state'


@contextmanager
def recorder_with_blocked_writer(tmp_path, monkeypatch, capacity=20):
    entered, release = threading.Event(), threading.Event()
    original = EpisodeWriter.append
    def append(writer, *args, **kwargs):
        if writer.count == 0:
            entered.set()
            if not release.wait(timeout=5.):
                raise RuntimeError('test did not release writer')
        return original(writer, *args, **kwargs)
    monkeypatch.setattr(EpisodeWriter, 'append', append)
    cfg = dict(config(tmp_path), task='gear_insertion', save_width=224, save_height=224,
               save_raw_rgb=True, min_episode_frames=3, writer_queue_capacity=capacity)
    buffers = SyntheticBuffers()
    recorder = Recorder(cfg, buffers, demo=True)
    recorder.start('gear_insertion')
    try:
        assert entered.wait(timeout=3.)
        yield recorder, buffers, release
    finally:
        recorder.stop('discarded')
        release.set()
        recorder.thread.join(timeout=5.)
        assert not recorder.active


def test_slow_write_keeps_sampling_and_stop_drains_immutable_frames(tmp_path, monkeypatch):
    with recorder_with_blocked_writer(tmp_path, monkeypatch) as (recorder, buffers, release):
        wait_until(lambda: recorder.sampled_frames >= 5)
        recorder.stop('success', 'finished while writer was delayed')
        assert recorder.sampling_done.wait(timeout=1.)
        expected = recorder.sampled_frames
        assert recorder.active and recorder.writer.count == 0
        assert not (recorder.writer.path/'trajectory.h5').exists()
        # Reusing/mutating the original source image cannot change already queued samples.
        buffers.image[:] = 0
        released_wall = time.time()
        release.set()
        recorder.thread.join(timeout=5.)
        assert not recorder.active
        path = recorder.last_path
        meta = json.loads((path/'metadata.json').read_text())
        assert meta['status'] == 'success'
        assert meta['frames'] == expected == recorder.writer.count
        assert meta['notes'] == 'finished while writer was delayed'
        performance = meta['performance']
        assert performance['sampled_frames'] == performance['written_frames'] == expected
        assert 1 <= performance['queue_peak'] <= performance['queue_capacity']
        assert performance['writer']['worker_total_ms']['max_ms'] > 200.
        assert performance['sampler']['sampling_delay_ms']['max_ms'] < 100.
        rows = [json.loads(line) for line in (path/'timing.jsonl').read_text().splitlines()]
        assert [row['frame'] for row in rows] == list(range(expected))
        assert max(row['queue_wait_ms'] for row in rows) > 100.
        with h5py.File(path/'trajectory.h5') as file:
            np.testing.assert_allclose(np.diff(file['timestamp'][:]), .1, atol=1e-7)
            assert file['wall_timestamp'][0] < released_wall-.2
            rgb = cv2.imdecode(file['rgb_raw/wrist'][0], cv2.IMREAD_COLOR)
            assert rgb[..., 2].mean() > 190
            for stream in ('wrist', 'external', 'eef', 'target', 'gripper', 'teleop', 'robot'):
                ages = file['timestamp'][:]-file['receive_timestamp/'+stream][:]
                assert np.all((ages >= 0) & (ages <= .2))
        result = convert_dataset(path, tmp_path/'converted.zarr', allow_demo=True)
        assert result['frames'] == expected and result['simulated']


def test_queue_overflow_stops_without_dropping_accepted_frames(tmp_path, monkeypatch):
    with recorder_with_blocked_writer(tmp_path, monkeypatch, capacity=2) as (recorder, buffers, release):
        wait_until(recorder.stop_event.is_set)
        assert recorder.sampling_done.wait(timeout=1.)
        assert recorder.active  # Saving still owns the recording lock.
        expected = recorder.sampled_frames
        assert expected <= 3  # One in service plus two queued.
        release.set()
        recorder.thread.join(timeout=5.)
        meta = json.loads((recorder.last_path/'metadata.json').read_text())
        assert meta['status'] == 'invalid' and '队列已满' in meta['reason']
        assert meta['frames'] == expected == meta['performance']['written_frames']
        assert meta['performance']['queue_capacity'] == 2
        assert (recorder.last_path/'trajectory.h5').exists()


@pytest.mark.parametrize('phase', ['jpeg', 'hdf5', 'disk'])
def test_encoding_or_partial_row_failure_stays_partial(tmp_path, monkeypatch, phase):
    if phase == 'jpeg':
        monkeypatch.setattr(cv2, 'imencode', lambda *args, **kwargs: (False, None))
    elif phase == 'hdf5':
        original = EpisodeWriter._append
        def append(writer, key, value):
            if key == 'target_eef_pose':
                raise OSError('injected HDF5 write failure')
            return original(writer, key, value)
        monkeypatch.setattr(EpisodeWriter, '_append', append)
    else:
        from my_dice_rl.util import recording
        original = recording.shutil.disk_usage
        monkeypatch.setattr(recording.shutil, 'disk_usage', lambda path:
            SimpleNamespace(free=0) if threading.current_thread().name == 'dice_demo_writer' else original(path))
    recorder = Recorder(dict(config(tmp_path), min_episode_frames=2, min_free_gb=1e-9), SyntheticBuffers(), demo=True)
    recorder.start('pick_place')
    recorder.thread.join(timeout=3.)
    assert not recorder.active
    path = recorder.last_path
    meta = json.loads((path/'metadata.json').read_text())
    assert meta['status'] == 'invalid' and '编码/写盘线程异常' in meta['reason']
    assert (path/'trajectory.partial.h5').exists()
    assert not (path/'trajectory.h5').exists()
    assert recorder.pending.empty()
    recorder.stop('success')
    assert json.loads((path/'metadata.json').read_text())['status'] == 'invalid'


@pytest.mark.parametrize('stage', ['sampling_schedule', 'snapshot'])
def test_real_sampling_overrun_still_stops_instead_of_skipping_ticks(tmp_path, monkeypatch, stage):
    buffers = SyntheticBuffers()
    recorder = Recorder(config(tmp_path), buffers, demo=True)
    if stage == 'snapshot':
        original = buffers.snapshot
        calls = []
        def snapshot(*args):
            calls.append(None)
            if len(calls) > 1:  # ready() is synchronous; delay the actual sampling stage.
                time.sleep(.15)
            return original(*args)
        monkeypatch.setattr(buffers, 'snapshot', snapshot)
    else:
        original = recorder.stop_event.wait
        monkeypatch.setattr(recorder.stop_event, 'wait', lambda timeout=None: original(timeout+.15))
    recorder.start('pick_place')
    recorder.thread.join(timeout=3.)
    assert not recorder.active
    meta = json.loads((recorder.last_path/'metadata.json').read_text())
    assert meta['status'] == 'invalid'
    assert ('快照/复制' if stage == 'snapshot' else '采样线程调度') in meta['reason']
    assert meta['frames'] <= 1
