"""Idle supervisor and graceful DP resume tests never allocate a GPU."""
from datetime import datetime, timedelta, timezone
import copy
import json
import signal
import threading

import pytest
import torch

from my_dice_rl.util.gpu_idle import IdleWindow, read_gpu
from my_dice_rl.script import train_dp_when_idle
from my_dice_rl.agent.pretrain import train_dp_agent
from my_dice_rl.tests.test_training_resume import TinyPolicy
from my_dice_rl.script.process_gear_dataset import convert_dataset
from my_dice_rl.util.checkpoint import load_checkpoint, rng_state
from my_dice_rl.util.config import load_train_config


def sample(processes=(), utilization=0., used=300.):
    return dict(uuid='GPU-test', processes=list(processes), utilization=utilization,
                used_mib=used, total_mib=24000.)


def test_idle_requires_stable_window_and_resets_on_busy_memory_or_processes():
    window = IdleWindow(seconds=60.)
    assert not window.update(sample(), 0.)
    assert not window.update(sample(), 59.)
    assert window.update(sample(), 60.)
    for busy in [sample(utilization=90.), sample(used=2000.), sample([dict(pid=5)])]:
        assert not window.update(busy, 61.)
        assert not window.update(sample(), 62.)
        assert not window.update(sample(), 121.)
        assert window.update(sample(), 122.)


def test_gpu_probe_selects_uuid_and_filters_other_devices(monkeypatch):
    def output(command, **kwargs):
        if '--query-gpu=' in ' '.join(command):
            return 'GPU-test, 0, 400, 24000\n'
        return 'GPU-other, 7, python, 9000\nGPU-test, 8, python, 2000\n'
    monkeypatch.setattr('my_dice_rl.util.gpu_idle.subprocess.check_output', output)
    result = read_gpu()
    assert result['uuid'] == 'GPU-test' and result['used_mib'] == 400.
    assert [p['pid'] for p in result['processes']] == [8]


def test_graphics_baseline_can_be_allowed_but_compute_processes_still_block():
    window=IdleWindow(seconds=60,max_utilization=60,max_used_mib=6144)
    assert not window.update(sample(utilization=40,used=779),0.)
    assert window.update(sample(utilization=40,used=779),60.)
    assert not window.update(sample([dict(pid=99)],utilization=1,used=1000),61.)
    assert not window.update(sample(utilization=80,used=779),62.)


@pytest.mark.parametrize('allowed_pids', [(), (77,)])
def test_supervisor_yields_only_own_child_then_resumes_checkpoint(tmp_path, monkeypatch, allowed_pids):
    checkpoint = tmp_path/'last.pt'
    checkpoint.write_text('fake child checkpoint; never loaded')
    reads, children = [], []
    def probe(gpu):
        reads.append(None)
        processes = [dict(pid=pid) for pid in allowed_pids]
        return sample(processes+[dict(pid=999)]) if len(reads) == 2 else sample(processes)
    class Child:
        def __init__(self, command, **kwargs):
            self.pid = 100+len(children)
            self.signals = []
            self.done = bool(children)
            self.command = command
            self.result = tmp_path/command[command.index('--result')+1]
            self.result.write_text(json.dumps(dict(state='completed' if self.done else 'initializing',
                training_complete=self.done, last_checkpoint=str(checkpoint))))
            children.append(self)
        def poll(self):
            return 0 if self.done else None
        def send_signal(self, number):
            self.signals.append(number)
            self.done = True
            self.result.write_text(json.dumps(dict(state='paused', training_complete=False,
                                                  last_checkpoint=str(checkpoint))))
    monkeypatch.setattr(train_dp_when_idle, 'read_gpu', probe)
    monkeypatch.setattr(train_dp_when_idle.subprocess, 'Popen', Child)
    result = train_dp_when_idle.supervise(tmp_path/'cfg.yaml', tmp_path,
        datetime.now(timezone.utc)+timedelta(seconds=2), poll_seconds=.001, idle_seconds=0.,
        allowed_compute_pids=allowed_pids)
    assert result['state'] == 'training_completed'
    assert len(children) == 2 and children[0].signals == [signal.SIGUSR1]
    assert children[1].signals == []
    assert children[1].command[-2:] == ['--resume', str(checkpoint)]


def test_explicit_gpu_sharing_preserves_real_gpu_sample_and_blocks_unlisted_jobs(tmp_path, monkeypatch):
    reads, children = [], []
    def probe(_):
        reads.append(None)
        return sample([dict(pid=77)]+([dict(pid=999)] if len(reads)==1 else []),
                      utilization=90,used=14000)
    class Child:
        pid = 100
        def __init__(self,command,**kwargs):
            assert len(reads)>=2  # An unlisted process prevented the first start.
            self.polls=0
            self.result=tmp_path/'result.json'
            from pathlib import Path
            self.result=Path(command[command.index('--result')+1])
            self.result.write_text(json.dumps(dict(state='running',training_complete=False)))
            children.append(self)
        def poll(self):
            self.polls+=1
            self.result.write_text(json.dumps(dict(state='completed',training_complete=True)))
            return None if self.polls<2 else 0
        def send_signal(self,number):
            pytest.fail('Explicitly allowed process must not pause the DP child')
    monkeypatch.setattr(train_dp_when_idle,'read_gpu',probe)
    monkeypatch.setattr(train_dp_when_idle.subprocess,'Popen',Child)
    result=train_dp_when_idle.supervise(tmp_path/'cfg',tmp_path,
        datetime.now(timezone.utc)+timedelta(seconds=2),poll_seconds=.001,idle_seconds=0.,
        max_utilization=100,max_used_mib=18000,allowed_compute_pids=(77,))
    assert result['state']=='training_completed' and len(children)==1
    assert result['gpu_mode']=='shared' and result['allowed_compute_pids']==[77]
    assert result['gpu']['processes']==[dict(pid=77)]


def test_shared_mode_resumes_saved_attempt_and_tolerates_restarted_external_pid(tmp_path,monkeypatch):
    checkpoint=tmp_path/'last.pt';checkpoint.write_text('mock checkpoint')
    first=tmp_path/'attempt_001_result.json';first.write_text('preserve previous attempt')
    reads=[];children=[]
    def probe(_):
        reads.append(None)
        # First probe is too full; then a fresh external PID appears while DP runs.
        return sample([dict(pid=77 if len(reads)<3 else 88)],utilization=95,
                      used=20000 if len(reads)==1 else 14000)
    class Child:
        pid=100
        def __init__(self,command,**kwargs):
            from pathlib import Path
            assert len(reads)==2  # Memory guard remains active in shared mode.
            assert command[-2:]==['--resume',str(checkpoint)]
            self.result=Path(command[command.index('--result')+1]);self.polls=0
            self.result.write_text(json.dumps(dict(state='running',training_complete=False)))
            children.append(self)
        def poll(self):
            self.polls+=1
            self.result.write_text(json.dumps(dict(state='completed',training_complete=True)))
            return None if self.polls==1 else 0
        def send_signal(self,number):
            pytest.fail('Restarted external PID must not interrupt shared training')
    monkeypatch.setattr(train_dp_when_idle,'read_gpu',probe)
    monkeypatch.setattr(train_dp_when_idle.subprocess,'Popen',Child)
    result=train_dp_when_idle.supervise(tmp_path/'cfg',tmp_path,
        datetime.now(timezone.utc)+timedelta(seconds=2),poll_seconds=.001,idle_seconds=0.,
        max_used_mib=18000,max_utilization=100,share_gpu=True,initial_resume=checkpoint)
    assert result['state']=='training_completed' and result['attempts']==2
    assert result['allow_any_compute'] and result['gpu']['processes']==[dict(pid=88)]
    assert first.read_text()=='preserve previous attempt'
    assert (tmp_path/'attempt_002_result.json').is_file() and len(children)==1


def test_expired_supervisor_never_launches_training(tmp_path, monkeypatch):
    monkeypatch.setattr(train_dp_when_idle, 'read_gpu', lambda _: sample())
    monkeypatch.setattr(train_dp_when_idle.subprocess, 'Popen', lambda *a, **k: pytest.fail('unexpected child'))
    result = train_dp_when_idle.supervise(tmp_path/'cfg', tmp_path,
        datetime.now(timezone.utc)-timedelta(seconds=1))
    assert result['state'] == 'deadline_reached'


def test_project_lock_busy_is_retried_without_discarding_the_job(tmp_path, monkeypatch):
    children = []
    monkeypatch.setattr(train_dp_when_idle,'read_gpu',lambda _:sample())
    class Child:
        def __init__(self,command,**kwargs):
            self.pid = 100+len(children)
            self.code = 75 if not children else 0
            result = dict(state='resource_busy',training_complete=False) if self.code else dict(
                state='completed',training_complete=True)
            Path(command[command.index('--result')+1]).write_text(json.dumps(result))
            children.append(self)
        def poll(self):
            return self.code
    from pathlib import Path
    monkeypatch.setattr(train_dp_when_idle.subprocess,'Popen',Child)
    result=train_dp_when_idle.supervise(tmp_path/'cfg',tmp_path,
        datetime.now(timezone.utc)+timedelta(seconds=2),poll_seconds=.001,idle_seconds=0.)
    assert result['state']=='training_completed' and len(children)==2


def test_cpu_rng_snapshot_does_not_initialize_cuda(monkeypatch):
    monkeypatch.setattr(torch.cuda, 'is_initialized', lambda: False)
    monkeypatch.setattr(torch.cuda, 'get_rng_state_all', lambda: pytest.fail('CPU snapshot allocated CUDA'))
    assert rng_state()['cuda'] == []


def test_graceful_stop_after_update_resumes_exactly(collected_episodes, tmp_path, monkeypatch):
    data = tmp_path/'data.zarr'
    convert_dataset(collected_episodes, data, allow_demo=True)
    config = load_train_config(overrides=['task.allow_demo=true', 'training.device=cpu',
        'training.num_workers=0', 'training.epochs=2', 'training.batch_size=2',
        'training.max_train_steps=2', 'training.max_val_steps=1', 'training.max_total_steps=2',
        'training.use_amp=false', 'training.warmup_steps=0', 'model.image_size=64',
        'training.checkpoint_every=1'])
    config['task']['dataset_path'] = str(data)
    config['training']['output_root'] = str(tmp_path/'full')
    monkeypatch.setattr(train_dp_agent, 'DiffusionPolicy', TinyPolicy)
    full = train_dp_agent.TrainDPAgent(copy.deepcopy(config))
    full.run()
    expected = load_checkpoint(full.run_dir/'checkpoint/last.pt')
    event = threading.Event()
    class PausingPolicy(TinyPolicy):
        def compute_loss(self, batch):
            loss = super().compute_loss(batch)
            if self.training:
                event.set()
            return loss
    monkeypatch.setattr(train_dp_agent, 'DiffusionPolicy', PausingPolicy)
    config['training']['output_root'] = str(tmp_path/'paused')
    first = train_dp_agent.TrainDPAgent(copy.deepcopy(config))
    first.run(stop_event=event)
    checkpoint = first.run_dir/'checkpoint/last.pt'
    paused = load_checkpoint(checkpoint)
    assert paused['global_step'] == 1 and paused['batch_in_epoch'] == 1
    assert not paused['training_complete']
    monkeypatch.setattr(train_dp_agent, 'DiffusionPolicy', TinyPolicy)
    resumed = train_dp_agent.TrainDPAgent(copy.deepcopy(config), checkpoint)
    resumed.run()
    actual = load_checkpoint(checkpoint)
    assert actual['global_step'] == expected['global_step'] == 2
    for key in expected['model']:
        torch.testing.assert_close(actual['model'][key], expected['model'][key], rtol=0, atol=0)
    for key in expected['ema']['model']:
        torch.testing.assert_close(actual['ema']['model'][key], expected['ema']['model'][key], rtol=0, atol=0)
