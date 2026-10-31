"""Selection uses completed, comparable real-run metadata, without loading weights."""
import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace

import yaml

from my_dice_rl.script.compare_dp_runs import compare
from my_dice_rl.script import compare_dp_runs


def make_run(root,name,loss,dataset_id='same',complete=True,pretrained=False):
    output=root/name;run=output/'run';run.mkdir(parents=True)
    dataset=output/'data';dataset.mkdir()
    (dataset/'manifest.json').write_text(json.dumps(dict(dataset_id=dataset_id)))
    (run/'config.yaml').write_text(yaml.safe_dump(dict(task=dict(dataset_path=str(dataset)),
        model=dict(pretrained_vision=pretrained))))
    (run/'metrics.jsonl').write_text(json.dumps(dict(epoch=20,val_loss=loss))+'\n')
    checkpoint=run/'best.pt';checkpoint.write_text('unit-test placeholder; never loaded')
    (output/'status.json').write_text(json.dumps(dict(state='completed' if complete else 'training',
        child_result=dict(run_dir=str(run),training_complete=complete,best_checkpoint=str(checkpoint)))))
    return output/'status.json',str(checkpoint)


def test_selects_completed_lowest_loss_on_same_dataset(tmp_path):
    a,pa=make_run(tmp_path,'random',.1)
    b,pb=make_run(tmp_path,'pretrained',.08,pretrained=True)
    result=compare([a,b],tmp_path/'selection.json',tmp_path/'selection.md')
    assert result['all_terminal'] and result['recommended_checkpoint']==pb
    assert (tmp_path/'selection.md').exists()


def test_incomplete_or_different_dataset_is_not_compared(tmp_path):
    a,pa=make_run(tmp_path,'a',.1)
    b,pb=make_run(tmp_path,'b',.01,complete=False)
    result=compare([a,b],tmp_path/'selection.json',tmp_path/'selection.md')
    assert not result['all_terminal'] and result['recommended_checkpoint']==pa
    c,pc=make_run(tmp_path,'c',.001,dataset_id='different')
    result=compare([a,c],tmp_path/'selection.json',tmp_path/'selection.md')
    assert not result['comparable'] and result['recommended_checkpoint'] is None


def test_optional_profile_launches_only_after_ready_selection(tmp_path,monkeypatch):
    status,checkpoint=make_run(tmp_path,'a',.1)
    output=tmp_path/'selection.json';report=tmp_path/'selection.md';profile=tmp_path/'profile.json'
    monkeypatch.setattr('sys.argv',['compare','--statuses',str(status),'--output',str(output),
        '--report',str(report),'--deadline',(datetime.now(timezone.utc)+timedelta(seconds=2)).isoformat(),
        '--profile-output',str(profile)])
    class Ready:
        since=None
        def __init__(self,**kwargs):pass
        def update(self,*args):return True
    monkeypatch.setattr(compare_dp_runs,'IdleWindow',Ready)
    monkeypatch.setattr(compare_dp_runs,'read_gpu',lambda:dict(processes=[]))
    def run(command,**kwargs):
        Path(command[command.index('--output')+1]).write_text(json.dumps(dict(
            latency_ms=dict(mean=10.,p95=12.,max=15.),all_within_budget=True,robot_commands_sent=False)))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(compare_dp_runs.subprocess,'run',run)
    compare_dp_runs.main()
    result=json.loads(output.read_text())
    assert result['recommended_checkpoint']==checkpoint
    assert result['runtime_profile']['robot_commands_sent'] is False
    assert 'GPU 推理耗时' in report.read_text()
