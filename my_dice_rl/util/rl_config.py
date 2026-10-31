"""RL 配置、数值校验和待确认设置；不导入机器人驱动。"""
import copy
import hashlib
import json
from pathlib import Path

import numpy as np
import yaml

from my_dice_rl.util.config import PROJECT_ROOT, project_path

DEFAULT_RL_CONFIG = PROJECT_ROOT / 'cfg/finetune/train_dice.yaml'
CONTROL_KEYS = ('control_hz', 'max_age_s', 'max_sensor_skew_s', 'max_gripper_age_s', 'control_timeout_s',
    'max_inference_latency_s', 'max_plan_age_s', 'max_episode_s', 'max_linear_speed',
    'max_angular_speed', 'max_position_error', 'max_orientation_error',
    'max_goal_position_error', 'max_goal_orientation_error', 'max_translation_from_reset',
    'max_rotation_from_reset', 'reset_drift_m', 'reset_drift_rad', 'max_force', 'max_torque',
    'gripper_speed', 'gripper_force', 'grasp_threshold', 'gripper_width_change',
    'gripper_min_interval_s', 'gripper_timeout_s', 'cancel_timeout_s', 'controller_name',
    'controller_command_interface')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def file_digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def load_rl_config(path=DEFAULT_RL_CONFIG, overrides=(), require_paths=True):
    config = yaml.safe_load(Path(path).expanduser().resolve().read_text())
    if config.get('schema') != 'dice_gear_rl_config_v1':
        raise ValueError('Unexpected RL config schema')
    for override in overrides:
        key, separator, value = override.partition('=')
        if not separator:
            raise ValueError('Expected dotted key=value override')
        target = config
        parts = key.split('.')
        for part in parts[:-1]:
            if part not in target or not isinstance(target[part], dict):
                raise ValueError('Unknown override: ' + key)
            target = target[part]
        if parts[-1] not in target:
            raise ValueError('Unknown override: ' + key)
        target[parts[-1]] = yaml.safe_load(value)
    for key in ('teacher_checkpoint', 'expert_dataset', 'rollout_root', 'execution_config', 'review_config', 'device_lock'):
        if config[key] is not None:
            config[key] = str(project_path(config[key]))
    config['training']['output_root'] = str(project_path(config['training']['output_root']))
    if require_paths and config['teacher_checkpoint'] is None:
        raise ValueError('请设置 teacher_checkpoint 为正式 DP checkpoint')
    if require_paths:
        execution = yaml.safe_load(Path(config['execution_config']).read_text())
        if str(project_path(execution.get('device_lock', ''))) != config['device_lock']:
            raise ValueError('Learner and execution must share the same device_lock')
        if str(project_path(execution.get('deployment_review', ''))) != config['review_config']:
            raise ValueError('Learner and execution must share the same review file')
    m, t = config['model'], config['training']
    integers = [(m, x) for x in ('chunk_steps', 'ensemble_size', 'target_subset', 'multi_z')]
    integers += [(t, x) for x in ('torch_threads', 'batch_size', 'encoding_batch_size', 'actor_update_freq',
        'total_updates', 'updates_per_round', 'min_new_episodes', 'chunk_stride', 'checkpoint_every',
        'replay_capacity', 'expert_capacity')]
    for group, key in integers:
        if not isinstance(group[key], int) or isinstance(group[key], bool) or group[key] < 1:
            raise ValueError(key + ' must be a positive integer')
    for group, key in [(m, 'filter_warmup'), (t, 'critic_warmup'), (t, 'min_online_chunks'), (t, 'seed')]:
        if not isinstance(group[key], int) or isinstance(group[key], bool) or group[key] < 0:
            raise ValueError(key + ' must be a nonnegative integer')
    for key in ('actor_lr', 'critic_lr', 'grad_clip_norm', 'poll_seconds'):
        if not isinstance(t[key], (int, float)) or isinstance(t[key], bool) or not np.isfinite(t[key]) or t[key] <= 0:
            raise ValueError(key + ' must be finite and positive')
    for group, key in [(m, 'bc_weight'), (m, 'q_margin'), (m, 'self_imitation_weight'), (t, 'weight_decay')]:
        if not isinstance(group[key], (int, float)) or isinstance(group[key], bool) or not np.isfinite(group[key]) or group[key] < 0:
            raise ValueError(key + ' must be finite and nonnegative')
    if any(isinstance(t[k], bool) or not isinstance(t[k], (int, float)) for k in ('gamma', 'target_tau', 'expert_ratio')):
        raise ValueError('Discount/replay settings must be numeric')
    if not 0 < t['gamma'] <= 1 or not 0 < t['target_tau'] <= 1 or not 0 <= t['expert_ratio'] <= 1:
        raise ValueError('Invalid gamma, target_tau or expert_ratio')
    if not np.isfinite([t['gamma'], t['target_tau'], t['expert_ratio']]).all():
        raise ValueError('Nonfinite discount/replay settings')
    if not m['hidden_dims'] or any(not isinstance(v, int) or isinstance(v, bool) or v < 1 for v in m['hidden_dims']):
        raise ValueError('hidden_dims must be positive integers')
    limit = np.asarray(m['residual_limit'], dtype=float)
    if limit.shape != (10,) or not np.isfinite(limit).all() or np.any((limit < 0) | (limit > 1)):
        raise ValueError('residual_limit must be ten normalized values in [0,1]')
    if m['ensemble_size'] < 2 or not 1 <= m['target_subset'] <= m['ensemble_size'] or m['td_loss'] not in ('mse', 'huber'):
        raise ValueError('Invalid critic configuration')
    for key in ('normalize_q', 'retain_expert_bc'):
        if not isinstance(m[key], bool):
            raise ValueError(key + ' must be boolean')
    if not isinstance(config['data']['allow_demo'], bool) or not isinstance(t['allow_expert_only'], bool):
        raise ValueError('Dataset mode flags must be boolean')
    if not np.isfinite(list(config['reward'].values())).all():
        raise ValueError('Reward settings must be finite')
    config['review'] = yaml.safe_load(Path(config['review_config']).read_text())
    if not isinstance(config['review'].get('confirmed'), bool):
        raise ValueError('review.confirmed must be boolean')
    return copy.deepcopy(config)


def execution_limits(config):
    return {key: config[key] for key in CONTROL_KEYS}


def require_review(review):
    if review.get('confirmed') is not True:
        raise ValueError('RL 数值尚待统一确认：请先完成 cfg/rl_review.yaml')
    fields = ('success_definition', 'success_insertion_depth_mm',
              'payload_mass_kg', 'payload_center_of_mass_m',
              'reset_position_m', 'reset_orientation_xyzw')
    missing = [key for key in fields if review.get(key) is None]
    if missing:
        raise ValueError('待确认字段未填写：' + ', '.join(missing))
    if not isinstance(review['success_definition'], str) or not review['success_definition'].strip():
        raise ValueError('请定义人工成功标注标准')
    # 成功由人工标注；容差和保持时间只是可选的标注约定，不驱动自动检测。
    for key in ('success_insertion_depth_mm', 'success_position_tolerance_mm',
                'success_hold_seconds', 'payload_mass_kg'):
        value = review.get(key)
        if value is None and key in ('success_position_tolerance_mm', 'success_hold_seconds'):
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or value < 0:
            raise ValueError('Invalid reviewed value: ' + key)
    for key, size in [('payload_center_of_mass_m', 3), ('reset_position_m', 3), ('reset_orientation_xyzw', 4)]:
        value = np.asarray(review[key], dtype=float)
        if value.shape != (size,) or not np.isfinite(value).all():
            raise ValueError('Invalid reviewed vector: ' + key)
    if abs(np.linalg.norm(review['reset_orientation_xyzw']) - 1) > .02:
        raise ValueError('Reviewed reset quaternion must be normalized XYZW')
