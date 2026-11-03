"""Validated settings for the explicitly armed, vertical gear reuse cycle."""
import copy
import math

from my_dice_rl.util.config import project_path


DEFAULTS = dict(success_z_m=.060, linear_speed=.010, angular_speed=.10,
    linear_acceleration=.05, angular_acceleration=.20, max_distance_m=.25,
    position_tolerance_m=.003, orientation_tolerance_rad=.05,
    tracking_position_error_m=.015, tracking_orientation_error_rad=.20,
    settle_s=.30, motion_timeout_s=45., label_wait_timeout_s=5., max_episodes=20, batch_episodes=10,
    wait_for_training=True, training_wait_timeout_s=900., poll_s=2.,
    latest_policy_index='log_dir/finetune/gear_dice/20261101_190141_382874/latest_policy.json')


def auto_cycle_config(value, execution):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value)-set(DEFAULTS):
        raise ValueError('Unknown auto_cycle settings')
    result = {**copy.deepcopy(DEFAULTS), **value}
    for key in DEFAULTS:
        v = result[key]
        if key in ('latest_policy_index', 'wait_for_training'):
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
            raise ValueError('Invalid auto_cycle.'+key)
    for key in ('max_episodes', 'batch_episodes'):
        if not isinstance(result[key], int):
            raise ValueError('auto_cycle.'+key+' must be integer')
    if not isinstance(result['wait_for_training'], bool):
        raise ValueError('auto_cycle.wait_for_training must be boolean')
    for key, limit in [('linear_speed', 'max_linear_speed'), ('angular_speed', 'max_angular_speed'),
                       ('tracking_position_error_m', 'max_position_error'),
                       ('tracking_orientation_error_rad', 'max_orientation_error'),
                       ('max_distance_m', 'max_translation_from_reset')]:
        if result[key] > execution[limit]:
            raise ValueError('auto_cycle.'+key+' exceeds '+limit+f' ({result[key]} > {execution[limit]})')
    if not isinstance(result['latest_policy_index'], str) or not result['latest_policy_index']:
        raise ValueError('auto_cycle.latest_policy_index must be a path')
    result['latest_policy_index'] = str(project_path(result['latest_policy_index']))
    return result
