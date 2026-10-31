"""Small YAML loader with explicit dotted overrides; paths are project-relative."""
import copy
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRAIN_CONFIG = PROJECT_ROOT / 'cfg/pretrain/train_dp.yaml'


def project_path(path):
    path = Path(path).expanduser()
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def load_train_config(path=DEFAULT_TRAIN_CONFIG, overrides=()):
    path = Path(path).expanduser().resolve()
    with path.open(encoding='utf-8') as stream:
        config = yaml.safe_load(stream)
    if 'task_config' in config:
        task_path = (path.parent / config.pop('task_config')).resolve()
        with task_path.open(encoding='utf-8') as stream:
            config['task'] = yaml.safe_load(stream)
    elif not isinstance(config.get('task'), dict):
        raise ValueError('Configuration must contain task_config or an inline task mapping')
    config = copy.deepcopy(config)
    for item in overrides:
        key, separator, value = item.partition('=')
        if not separator:
            raise ValueError(f'Override must be key=value: {item}')
        target = config
        parts = key.split('.')
        for part in parts[:-1]:
            if part not in target or not isinstance(target[part], dict):
                raise ValueError(f'Unknown configuration key: {key}')
            target = target[part]
        if parts[-1] not in target:
            raise ValueError(f'Unknown configuration key: {key}')
        target[parts[-1]] = yaml.safe_load(value)
    config['task']['dataset_path'] = str(project_path(config['task']['dataset_path']))
    config['training']['output_root'] = str(project_path(config['training']['output_root']))
    if config['training'].get('device_lock'):
        config['training']['device_lock'] = str(project_path(config['training']['device_lock']))
    return config


def model_config(config):
    result = copy.deepcopy(config['model'])
    for key in ('obs_steps', 'horizon_steps', 'act_steps'):
        result[key] = config['task'][key]
    return result
