"""Three-view data contracts, real gradients and offline deployment round trip."""
import json
from pathlib import Path

import h5py
import numpy as np
import pytest
import torch
import yaml

from my_dice_rl.util.cameras import camera_names, camera_keys, matched_images, TRIPLE_CAMERAS
from my_dice_rl.util.recording import Sample, StreamBuffer
from my_dice_rl.env.real.policy_observation import ObservationBuffer, ObservationSyncError
from my_dice_rl.script.check_rl_pipeline import synthetic_demonstrations, check_pipeline
from my_dice_rl.script.process_gear_dataset import convert_dataset
from my_dice_rl.agent.dataset.gear_dataset import GearDataset, make_datasets
from my_dice_rl.agent.dataset.rl_dataset import RLDataset
from my_dice_rl.model.diffusion.diffusion_policy import DiffusionPolicy
from my_dice_rl.util.checkpoint import load_policy, load_checkpoint, policy_model_config
from my_dice_rl.util.camera_launch import load_camera_config
from my_dice_rl.util.config import PROJECT_ROOT, load_train_config, model_config


@pytest.fixture(scope='module')
def triple_pipeline(tmp_path_factory):
    return check_pipeline(tmp_path_factory.mktemp('three_pipeline'), 'cpu', TRIPLE_CAMERAS)


def test_all_three_views_pass_dp_rl_resume_and_deployment(triple_pipeline):
    report = triple_pipeline
    assert report['camera_names'] == list(TRIPLE_CAMERAS)
    assert report['global_step'] == 4 and report['frozen_teacher_unchanged']
    assert report['export_matches_learner'] and report['synthetic_real_deployment_rejected']
    assert not report['real_robot_commands_sent']
    dataset = RLDataset(report['online_episodes'][0], image_size=32, allow_demo=True)
    assert dataset.camera_names == TRIPLE_CAMERAS
    sample, plan = dataset[0], dataset.get_plan(0, 0)
    for name in TRIPLE_CAMERAS:
        key = 'camera_' + name
        assert sample['obs'][key].shape == (2, 3, 32, 32)
        assert plan['obs'][key].shape == (2, 3, 32, 32)
    # Distinct side pixels survived writing, conversion, replay and plan storage.
    assert sample['obs']['camera_side'][:, 0].mean() > sample['obs']['camera_side'][:, 1].mean()
    policy, state = load_policy(report['teacher_checkpoint'])
    assert tuple(policy.obs_encoder.camera_keys) == camera_keys({'camera_names': TRIPLE_CAMERAS})
    bad = dict(state['model_config'], camera_keys=['camera_wrist','camera_external'])
    with pytest.raises(ValueError, match='cameras disagree'):
        policy_model_config(bad, state['model'])


def test_third_camera_required_and_model_gradient_reaches_each_view():
    torch.set_num_threads(2)
    keys = camera_keys({'camera_names': TRIPLE_CAMERAS})
    model = DiffusionPolicy(image_size=32, camera_keys=keys, horizon_steps=4, act_steps=2,
                            down_dims=[16,32], time_embed_dim=16, train_diffusion_steps=4, inference_steps=2)
    model.fit_normalizer(torch.randn(20,9), torch.randn(20,9))
    images = {key: torch.rand(1,2,3,32,32,requires_grad=True) for key in keys}
    obs = dict(images, robot_eef_pose=torch.randn(1,2,9))
    loss = model.compute_loss(dict(obs=obs, action=torch.randn(1,4,9)))
    loss.backward()
    for key in keys:
        assert images[key].grad is not None and images[key].grad.abs().sum() > 0
    assert model.obs_encoder.feature_dim == 512*3+9
    with pytest.raises(KeyError, match='camera_side'):
        model.encode_obs({k:v for k,v in obs.items() if k != 'camera_side'})
    assert model.eval().predict_action(obs)['action_pred'].shape == (1,4,9)


def test_three_camera_sync_uses_total_spread_and_never_future_frames():
    buffer = ObservationBuffer({'camera_names':TRIPLE_CAMERAS})
    rgb = np.zeros((224,224,3), np.uint8)
    for name in ('wrist','external'):
        buffer.put(name,rgb,received=9.99)
    for name,value in [('eef',[.4,0,.2,1,0,0,0]),('gripper',.03),('robot',{})]:
        buffer.put(name,value,received=9.99)
    with pytest.raises(ObservationSyncError,match='side'):
        buffer.snapshot(10,.2,.06)
    buffer.put('side',rgb,received=9.91)
    buffer.put('side',rgb,received=10.01)
    with pytest.raises(ObservationSyncError,match='配对'):
        buffer.snapshot(10,.2,.06)
    buffer.put('side',rgb,received=9.98)
    frame=buffer.snapshot(10,.2,.06)
    assert frame['side'].received==9.98
    assert max(frame[k].received for k in TRIPLE_CAMERAS)-min(frame[k].received for k in TRIPLE_CAMERAS)<=.06
    assert 'side' in StreamBuffer({'camera_names':TRIPLE_CAMERAS}).streams


def test_processed_dataset_rejects_missing_side_and_mixed_layout(tmp_path):
    raw=tmp_path/'raw';synthetic_demonstrations(raw,TRIPLE_CAMERAS)
    episode=next(raw.rglob('trajectory.h5'))
    with h5py.File(episode,'r+') as f:del f['rgb/side']
    with pytest.raises(ValueError,match='RGB: side'):
        convert_dataset(raw,tmp_path/'missing.zarr',allow_demo=True)
    assert not (tmp_path/'missing.zarr').exists()
    mixed=tmp_path/'mixed'
    synthetic_demonstrations(mixed/'two')
    synthetic_demonstrations(mixed/'three',TRIPLE_CAMERAS)
    with pytest.raises(ValueError,match='mix camera layouts'):
        convert_dataset(mixed,tmp_path/'mixed.zarr',allow_demo=True)
    assert not (tmp_path/'mixed.zarr').exists()


def test_three_camera_configs_preserve_architecture_and_pin_orbbec_devices():
    train=load_train_config(PROJECT_ROOT/'cfg/pretrain/train_dp_three.yaml')
    model=model_config(train)
    assert model['camera_keys']==['camera_wrist','camera_external','camera_side']
    assert model['vision_encoder']=='clip_vit_b32' and model['pretrained_vision']
    assert model['state_dim']==model['action_dim']==9
    assert model['down_dims']==[256,512,1024]
    hardware=load_camera_config(PROJECT_ROOT/'cfg/cameras_three.yaml')
    assert hardware['devices']['external']['serial_number']!=hardware['devices']['side']['serial_number']
    rl=yaml.safe_load((PROJECT_ROOT/'cfg/finetune/train_dice_three.yaml').read_text())
    assert rl['model']['hidden_dims']==[1024,1024,1024] and rl['model']['ensemble_size']==5
    assert rl['model']['bc_weight']==100


def test_first_online_round_passes_critic_warmup_and_publishes_run_alias(triple_pipeline,tmp_path):
    from my_dice_rl.util.rl_config import load_rl_config
    from my_dice_rl.agent.finetune.train_dice_agent import TrainDiceAgent
    old_run=Path(triple_pipeline['rl_run'])
    config=load_rl_config(old_run/'config.yaml')
    config['training'].update(total_updates=4,initial_updates=4,updates_per_round=2,
        critic_warmup=2,actor_update_freq=1,output_root=str(tmp_path/'new_runs'),
        run_alias=str(tmp_path/'current'),min_new_episodes=1)
    agent=TrainDiceAgent(config)
    run=agent.run(watch=True)
    assert agent.global_step==4 and agent.actor_updates==2
    assert (tmp_path/'current').is_symlink() and (tmp_path/'current').resolve()==run.resolve()
    index=json.loads((tmp_path/'current/latest_policy.json').read_text())
    assert index['global_step']==4
    exported=torch.load(index['policy'],map_location='cpu',weights_only=False)
    assert exported['actor_updates']==2


def test_repeated_topics_and_unbound_orbbec_devices_are_rejected(tmp_path):
    from my_dice_rl.util.cameras import validate_camera_topics
    config=dict(camera_names=list(TRIPLE_CAMERAS),wrist_topic='/wrist/rgb',external_topic='/front/rgb',side_topic='/front/rgb')
    with pytest.raises(ValueError,match='distinct'):validate_camera_topics(config)
    config['side_topic']='/side/rgb'
    assert validate_camera_topics(config)==TRIPLE_CAMERAS
    hardware=yaml.safe_load((PROJECT_ROOT/'cfg/cameras_three.yaml').read_text())
    hardware['devices']['side']['serial_number']=hardware['devices']['external']['serial_number']
    p=tmp_path/'duplicate.yaml';p.write_text(yaml.safe_dump(hardware))
    with pytest.raises(ValueError,match='different explicit'):load_camera_config(p)
