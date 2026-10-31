"""A tiny network makes exact optimizer/RNG resume checks quick; CLI smoke uses real DP."""
import copy

import pytest
import torch
from torch import nn

pytest.importorskip('zarr')
pytest.importorskip('diffusers')

from my_dice_rl.agent.pretrain import train_dp_agent
from my_dice_rl.model.common.normalizer import FeatureNormalizer
from my_dice_rl.script.process_gear_dataset import convert_dataset
from my_dice_rl.util.checkpoint import load_checkpoint
from my_dice_rl.util.config import load_train_config


class TinyPolicy(nn.Module):
    def __init__(self, **kwargs):
        super().__init__()
        self.linear = nn.Linear(10, 10)
        self.state_normalizer = FeatureNormalizer(10)
        self.action_normalizer = FeatureNormalizer(10)

    def fit_normalizer(self, states, actions):
        self.state_normalizer.fit(states)
        self.action_normalizer.fit(actions)

    def compute_loss(self, batch):
        state = torch.cat([batch['obs']['robot_eef_pose'][:, -1],
                           batch['obs']['gripper_width'][:, -1]], dim=-1)
        prediction = self.linear(self.state_normalizer.normalize(state))[:, None]
        target = self.action_normalizer.normalize(batch['action'])
        error = (prediction-target-torch.randn_like(target)*.1).square()
        return error[batch['valid_mask']].mean()


def test_resume_inside_epoch_preserves_rng_optimizer_ema_and_data_order(
        collected_episodes, tmp_path, monkeypatch):
    torch.set_num_threads(2)
    output = tmp_path/'data.zarr'
    convert_dataset(collected_episodes, output, allow_demo=True)
    config = load_train_config(overrides=[
        'task.allow_demo=true', 'training.device=cpu', 'training.num_workers=0',
        'training.epochs=2', 'training.batch_size=2', 'training.max_train_steps=2',
        'training.max_val_steps=1', 'training.max_total_steps=2',
        'training.use_amp=false', 'training.warmup_steps=0', 'model.image_size=64'])
    config['task']['dataset_path'] = str(output)
    config['training']['output_root'] = str(tmp_path/'complete')
    monkeypatch.setattr(train_dp_agent, 'DiffusionPolicy', TinyPolicy)
    uninterrupted = train_dp_agent.TrainDPAgent(copy.deepcopy(config))
    full_run = uninterrupted.run()
    full_state = load_checkpoint(full_run/'checkpoint/last.pt')
    config['training']['output_root'] = str(tmp_path/'interrupted')
    config['training']['max_total_steps'] = 1
    first = train_dp_agent.TrainDPAgent(copy.deepcopy(config))
    interrupted_run = first.run()
    assert load_train_config(interrupted_run/'config.yaml') == first.config
    partial = load_checkpoint(interrupted_run/'checkpoint/last.pt')
    assert partial['epoch'] == 0 and partial['batch_in_epoch'] == 1
    config['training']['max_total_steps'] = 2
    resumed = train_dp_agent.TrainDPAgent(copy.deepcopy(config), interrupted_run/'checkpoint/last.pt')
    resumed.run()
    final_state = load_checkpoint(interrupted_run/'checkpoint/last.pt')
    assert final_state['global_step'] == 2 and final_state['epoch'] == 1
    assert final_state['ema']['updates'] == full_state['ema']['updates']
    assert final_state['scheduler'] == full_state['scheduler']
    for key in full_state['model']:
        torch.testing.assert_close(final_state['model'][key], full_state['model'][key], rtol=0, atol=0)
    for key in full_state['ema']['model']:
        torch.testing.assert_close(final_state['ema']['model'][key], full_state['ema']['model'][key], rtol=0, atol=0)
