"""Offline evaluation aggregation and workspace diagnostics; no device access."""
import json
from pathlib import Path

import numpy as np
import torch
import zarr

from my_dice_rl.script import evaluate_dp
from my_dice_rl.script.preflight_deployment import check_limits
from my_dice_rl.script.process_gear_dataset import convert_dataset
from my_dice_rl.util.config import load_train_config,PROJECT_ROOT


def test_evaluator_covers_held_out_episode_boundaries_and_masks(collected_episodes,tmp_path,monkeypatch):
    data=tmp_path/'data.zarr'
    result=convert_dataset(collected_episodes,data,allow_demo=True)
    config=load_train_config(overrides=['task.allow_demo=true','model.image_size=64'])
    config['task']['dataset_path']=str(data)
    class Model:
        image_size=64
        def predict_action(self,obs,**kwargs):
            action=torch.tensor([.4,0.,.3,1.,0.,0.,0.,1.,0.,0.])
            return dict(action_pred=action.repeat(1,20,1))
    monkeypatch.setattr(evaluate_dp,'load_policy',lambda *a,**k:(Model(),dict(config=config,
        dataset_id=result['dataset_id'],global_step=5,epoch=1,training_complete=True)))
    output=tmp_path/'evaluation.json'
    report=evaluate_dp.evaluate('dummy.pt',output,max_windows=3)
    assert report['complete'] and report['split']=='validation' and report['simulated']
    assert report['completed_windows']==3
    assert report['position_error_mm']['count']==sum(c['valid_steps'] for c in report['cases'])
    assert report['position_error_mm']['count']<60  # End padding does not contribute.
    assert report['execution_prefix_position_error_mm']['count']==9  # 4 + 4 + final valid step.
    assert len({c['episode_id'] for c in report['cases']})==1
    assert output.exists() and output.with_suffix('.npz').exists()


def test_preflight_reports_workspace_mismatch_without_changing_config(tmp_path):
    dataset=tmp_path/'data.zarr'
    root=zarr.open_group(str(dataset),mode='w')
    root.attrs.update(complete=True,simulated=False,dataset_id='synthetic unit-test schema fixture',
                      episodes=[dict(source='unit-test-only')])
    pose=np.array([[.4,0.,.3,1,0,0,0,1,0],[.46,0.,.3,1,0,0,0,1,0],
                   [.53,0.,.3,1,0,0,0,1,0]],np.float32)
    action=np.c_[pose,np.zeros(3)].astype(np.float32)
    root.create_dataset('data/robot_eef_pose',data=pose)
    root.create_dataset('data/action',data=action)
    root.create_dataset('meta/episode_ends',data=np.array([3]))
    config=PROJECT_ROOT/'cfg/execute_dp.yaml'
    original=config.read_bytes()
    report=check_limits(dataset,config,tmp_path/'report.json')
    assert report['episodes_exceeding_limits']==1
    assert report['observed_translation_envelope_m']>.129
    assert 'workspace_translation' in report['episodes'][0]['flags']
    assert config.read_bytes()==original
