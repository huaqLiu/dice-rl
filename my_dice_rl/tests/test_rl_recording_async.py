"""Slow metadata storage must not block the caller that starts control."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading

from my_dice_rl.util import rl_recording


def test_initial_metadata_write_does_not_block_constructor(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    atomic_json = rl_recording.atomic_json

    def blocked_metadata(path, value):
        if value['status'] == 'recording':
            entered.set()
            assert release.wait(5), 'Test did not release the simulated disk write'
        return atomic_json(path, value)

    monkeypatch.setattr(rl_recording, 'atomic_json', blocked_metadata)
    writer = None
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(rl_recording.RolloutWriter, tmp_path, 'gear_insertion',
                             {}, {'simulated': True}, True)
        try:
            assert entered.wait(2)
            writer = future.result(timeout=2)
            assert not writer.closed
            writer.finish('aborted', 'test shutdown', unsafe=True)
        finally:
            release.set()
            # Always close a constructor that returns after an assertion failure.
            if writer is None:
                writer = future.result(timeout=2)
                writer.finish('aborted', 'test cleanup', unsafe=True)
            writer.worker.join(timeout=3)
    assert writer.closed and not writer.error
    assert json.loads((writer.path/'metadata.json').read_text())['status'] == 'aborted'
