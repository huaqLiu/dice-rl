"""Actual small DP/RL optimizer and deployment round trip, entirely in-process simulation."""
import copy
import json
from pathlib import Path

import pytest
import torch
import yaml

from my_dice_rl.agent.eval.rl_policy import load_execution_policy, resolve_policy_path
from my_dice_rl.agent.finetune.train_dice_agent import TrainDiceAgent
from my_dice_rl.script.check_rl_pipeline import check_pipeline
from my_dice_rl.util.checkpoint import atomic_save
from my_dice_rl.util.rl_checkpoint import load_rl_checkpoint, export_rl_policy, RL_POLICY_SCHEMA
from my_dice_rl.util.rl_config import load_rl_config, file_digest, execution_limits, digest


@pytest.fixture(scope='module')
def pipeline(tmp_path_factory):
    report = check_pipeline(tmp_path_factory.mktemp('rl_training') / 'run', 'cpu')
    return report


def test_full_pipeline_updates_freezes_teacher_and_executes_export(pipeline):
    assert pipeline['global_step'] == 4 and len(pipeline['online_episodes']) == 3
    assert pipeline['export_matches_learner'] and pipeline['frozen_teacher_unchanged']
    assert pipeline['synthetic_real_deployment_rejected'] and not pipeline['real_robot_commands_sent']
    state = load_rl_checkpoint(Path(pipeline['rl_run']) / 'checkpoint/last.pt')
    assert state['replay']['online']['size'] > 0 and state['replay']['expert']['size'] > 0
    assert state['global_step'] == 4 and state['simulated']
    assert state['model']['actor']['network.6.weight'].abs().sum() > 0


def test_exact_resume_and_mismatched_settings(pipeline):
    run = Path(pipeline['rl_run'])
    config = load_rl_config(run / 'config.yaml')
    config['training']['total_updates'] = 5
    # 两次从同一个原子 checkpoint 恢复，下一步采样、目标网络与参数更新必须一致。
    first = TrainDiceAgent(copy.deepcopy(config), run / 'checkpoint/last.pt')
    second = TrainDiceAgent(copy.deepcopy(config), run / 'checkpoint/last.pt')
    saved_rng = torch.get_rng_state()
    first_metrics = first.update()
    torch.set_rng_state(saved_rng)
    second_metrics = second.update()
    assert first_metrics == second_metrics
    for name in ('actor', 'critic', 'target_actor', 'target_critic'):
        for key, value in getattr(first.model, name).state_dict().items():
            torch.testing.assert_close(value, getattr(second.model, name).state_dict()[key], rtol=0, atol=0)
    changed = copy.deepcopy(config)
    changed['training']['actor_lr'] *= 2
    with pytest.raises(ValueError, match='Resume settings differ'):
        TrainDiceAgent(changed, run / 'checkpoint/last.pt')


def test_export_is_self_contained_and_rejects_drafts_and_wrong_index(pipeline, tmp_path):
    run = Path(pipeline['rl_run'])
    with pytest.raises(ValueError, match='统一确认'):
        export_rl_policy(run / 'checkpoint/last.pt', tmp_path / 'formal.pt')
    output = export_rl_policy(run / 'checkpoint/last.pt', tmp_path / 'draft.pt', allow_draft=True)
    payload = torch.load(output, map_location='cpu', weights_only=False)
    assert payload['schema'] == RL_POLICY_SCHEMA and not payload['deployable']
    # 原始 DP 文件路径可以不存在；部署读取内嵌权重。
    payload['teacher']['task']['dataset_path'] = '/nonexistent/expert.zarr'
    atomic_save(output, payload)
    collector = dict(task='gear_insertion', base_frame='panda_link0', ee_frame='panda_hand_tcp')
    policy = load_execution_policy(output, 'cpu', collector, real=False)
    assert policy.act_steps == 2 and policy.horizon == 8
    with pytest.raises(ValueError, match='模拟/草稿'):
        load_execution_policy(output, 'cpu', collector, real=True, execution={})
    index = tmp_path / 'latest.json'
    index.write_text(json.dumps(dict(schema='dice_gear_rl_index_v1', policy='draft.pt', sha256='wrong')))
    with pytest.raises(ValueError, match='hash'):
        resolve_policy_path(index)


def test_reviewed_formal_loader_checks_runtime_limits(pipeline, tmp_path):
    # 仅在临时测试文件中模拟已确认的来源标记；不会发布 ROS 或接入任何硬件。
    run = Path(pipeline['rl_run'])
    state = load_rl_checkpoint(run / 'checkpoint/last.pt')
    exported = export_rl_policy(run / 'checkpoint/last.pt', tmp_path / 'policy.pt', allow_draft=True)
    payload = torch.load(exported, map_location='cpu', weights_only=False)
    review = dict(confirmed=True, success_definition='synthetic loader test',
        success_insertion_depth_mm=1., success_position_tolerance_mm=1., success_hold_seconds=.1,
        payload_mass_kg=.83, payload_center_of_mass_m=[0., 0., 0.],
        reset_position_m=[.45, 0., .3], reset_orientation_xyzw=[1., 0., 0., 0.])
    review_path = tmp_path / 'review.yaml'
    review_path.write_text(yaml.safe_dump(review))
    execution = yaml.safe_load(Path(state['config']['execution_config']).read_text())
    execution['deployment_review'] = str(review_path)
    payload.update(simulated=False, deployable=True, review=review, review_digest=digest(review),
                   execution_limits=execution_limits(execution))
    payload['teacher']['task']['allow_demo'] = False
    atomic_save(exported, payload)
    collector = dict(task='gear_insertion', base_frame='panda_link0', ee_frame='panda_hand_tcp')
    policy = load_execution_policy(exported, 'cpu', collector, real=True, execution=execution)
    assert policy.act_steps == 2
    execution['max_force'] += 1
    with pytest.raises(ValueError, match='不一致'):
        load_execution_policy(exported, 'cpu', collector, real=True, execution=execution)
