"""只读 RL 轨迹接口：相邻观测、实际指令、奖励和终止标记，不接入 ROS。

一个样本对应一条 observations[i] -> observations[i+1] 转移。
action 是变长的实际控制目标序列，单位为米/rotation6d/夹爪米；不是完整预测计划。
使用 collate_rl_batch 组批，它会补零并生成 action_mask。模型归一化留给训练端。
"""
import json
from pathlib import Path

import cv2
import h5py
import numpy as np
import torch
from torch.nn.utils.rnn import pad_sequence
from torch.utils.data import Dataset, default_collate

from my_dice_rl.util.rl_recording import SCHEMA
from my_dice_rl.util.rotation import pose7_to_pose9


def _array(file, key, shape, kind=None):
    if key not in file or file[key].shape != tuple(shape):
        raise ValueError(f'Missing or inconsistent field: {key}; expected {tuple(shape)}')
    value = file[key][:]
    if kind is not None and value.dtype.kind not in kind:
        raise ValueError(f'Invalid dtype: {key}')
    if not np.isfinite(value).all():
        raise ValueError(f'Non-finite values: {key}')
    return value


def _check_pose(value, name):
    if np.any(np.abs(np.linalg.norm(value[..., 3:], axis=-1) - 1.) > .02):
        raise ValueError(f'Invalid XYZW quaternion: {name}')


class RLDataset(Dataset):
    """读取一个 episode、rollout.h5、metadata.json 或包含多条轨迹的目录。

    仅接受 eligible_for_rl=true 的 success/failed；异常中止和 partial 不参与训练。
    模拟数据需要 allow_demo=True，且不能与真实数据混合。
    obs_steps 是同一轨迹内连续的已记录观测数，起点不足时重复首帧，obs_mask 标记补帧。
    时间戳及 dt 保留真实记录值，不把 50 Hz 指令重解释成 10 Hz 策略动作。
    后续回放模块仍需按策略决策组织动作块，本类不计算折扣回报或拟合归一化。

    sample['action']: (M, 10)，XYZ + rotation6d（前两行）+ 已接受的夹爪目标。
    sample['commands']: 每条指令的时间、持续时长、计划编号、原预测动作与夹爪模式。
    若区间开始时已有指令在保持，序列首项带入该指令，is_new=False。
    uncovered_dt 表示第一条已知指令之前的时间；没有凭空补造初始动作。
    get_plan(episode_id, plan_id) 单独读取原预测计划、实际推理历史和初始噪声。
    """

    def __init__(self, path, obs_steps=2, image_size=224, allow_demo=False,
                 task='gear_insertion'):
        if (not isinstance(obs_steps, int) or isinstance(obs_steps, bool) or obs_steps < 1
                or not isinstance(image_size, int) or isinstance(image_size, bool) or image_size < 1):
            raise ValueError('obs_steps and image_size must be positive integers')
        self.path = Path(path).expanduser().resolve()
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        self.obs_steps, self.image_size = obs_steps, image_size
        self.episodes, self.skipped, self.indices = [], [], []
        if self.path.is_file():
            if self.path.name not in {'rollout.h5', 'metadata.json'}:
                raise ValueError('Expected rollout.h5 or metadata.json, not a partial file')
            paths = [self.path.parent / 'metadata.json']
        elif (self.path / 'metadata.json').is_file():
            paths = [self.path / 'metadata.json']
        else:
            paths = sorted(self.path.rglob('metadata.json'))
        for metadata_path in paths:
            metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
            config, policy = metadata.get('config', {}), metadata.get('policy', {})
            simulated = bool(metadata.get('simulated') or policy.get('simulated')
                             or policy.get('task', {}).get('allow_demo')
                             or '_demo' in metadata_path.parts)
            reason = None
            if metadata.get('schema') != SCHEMA:
                reason = 'different schema'
            elif metadata.get('status') not in ('success', 'failed'):
                reason = 'status=' + str(metadata.get('status'))
            elif metadata.get('eligible_for_rl') is not True or metadata.get('label', {}).get('unsafe'):
                reason = 'not eligible for RL'
            elif task is not None and config.get('task') != task:
                reason = 'different task'
            elif simulated and not allow_demo:
                reason = 'simulated data excluded'
            if reason:
                self.skipped.append(dict(path=str(metadata_path), reason=reason))
                continue
            rollout_path = metadata_path.with_name('rollout.h5')
            try:
                with h5py.File(rollout_path, 'r') as file:
                    episode = self._validate(file, metadata)
            except (OSError, ValueError, KeyError) as exc:
                raise ValueError(f'{rollout_path}: {exc}') from exc
            episode.update(path=rollout_path, metadata=metadata, simulated=simulated)
            episode_id = len(self.episodes)
            self.episodes.append(episode)
            self.indices.extend((episode_id, i) for i in range(len(episode['tick']) - 1))
        if not self.episodes:
            raise ValueError(f'No eligible RL rollouts; skipped {len(self.skipped)}. '
                             'Only synthetic checks should use allow_demo=True.')
        if len({e['simulated'] for e in self.episodes}) != 1:
            raise ValueError('Do not mix real and simulated rollouts')
        # Older synthetic files may lack coordinate metadata. Known definitions must agree.
        for key in ('base_frame', 'ee_frame'):
            values = {e['metadata']['config'].get('collector', {}).get(key) for e in self.episodes}
            values.discard(None)
            if len(values) > 1:
                raise ValueError(f'Rollouts disagree on {key}')

    def _validate(self, file, metadata):
        if file.attrs.get('schema') != SCHEMA or file.attrs.get('outcome') != metadata['status']:
            raise ValueError('HDF5 schema/outcome disagrees with metadata')
        if metadata.get('outcome') != metadata['status']:
            raise ValueError('Metadata status/outcome disagree')
        count = metadata['frames']
        if not isinstance(count, int) or isinstance(count, bool) or count < 2:
            raise ValueError('At least two observations are required')
        transitions = count - 1
        if metadata['transitions'] != transitions:
            raise ValueError('Metadata transition count disagrees with frames')
        tick = _array(file, 'observations/tick', (count,))
        if np.any(np.diff(tick) <= 0):
            raise ValueError('Observation timestamps must strictly increase')
        pose = _array(file, 'observations/pose', (count, 7))
        _check_pose(pose, 'observations/pose')
        width = _array(file, 'observations/gripper_width', (count,))
        if np.any((width < 0) | (width > .085)):
            raise ValueError('Actual gripper width outside [0, .085] m')
        for key, tail in [('joint_position', 7), ('joint_velocity', 7), ('wrench', 6)]:
            _array(file, 'observations/' + key, (count, tail))
        received = []
        config = metadata['config']
        max_age, max_skew = config['max_age_s'], config['max_sensor_skew_s']
        if not np.isfinite([max_age, max_skew]).all() or min(max_age, max_skew) <= 0:
            raise ValueError('Invalid recorded synchronization limits')
        for key in ('wrist', 'external', 'eef', 'gripper', 'robot'):
            receive = _array(file, 'observations/receive/' + key, (count,))
            _array(file, 'observations/source/' + key, (count,))
            if np.any(tick - receive < -1e-6) or np.any(tick - receive > max_age + 1e-6):
                raise ValueError(f'Stale or noncausal observation: {key}')
            received.append(receive)
        if np.any(np.ptp(received, axis=0) > max_skew + 1e-6):
            raise ValueError('Observation receive-time skew exceeds recorded limit')
        for camera in ('wrist', 'external'):
            if file['observations/rgb/' + camera].shape != (count, 1):
                raise ValueError(f'Missing/inconsistent observation RGB: {camera}')

        command_count = len(file['commands/tick']) if 'commands/tick' in file else 0
        plan_count = len(file['plans/tick']) if 'plans/tick' in file else 0
        for group, length in [('commands', command_count), ('plans', plan_count)]:
            if group in file and length == 0:
                raise ValueError(f'Incomplete {group} group without recorded timestamps')
        for group, expected in [('observations', count), ('commands', command_count), ('plans', plan_count)]:
            if metadata.get('counts', {}).get(group, 0) != expected:
                raise ValueError(f'Metadata count disagrees with {group}')
        command_tick = np.empty(0, np.float64)
        if command_count:
            command_tick = _array(file, 'commands/tick', (command_count,))
            if np.any(np.diff(command_tick) <= 0):
                raise ValueError('Command timestamps must strictly increase')
            _check_pose(_array(file, 'commands/pose', (command_count, 7)), 'commands/pose')
            requested = _array(file, 'commands/requested_action', (command_count, 10))
            target = _array(file, 'commands/gripper_target', (command_count,))
            if np.any((target < 0) | (target > .08)) or np.any((requested[:, 9] < 0) | (requested[:, 9] > .08)):
                raise ValueError('Command gripper width outside [0, .08] m')
            modes = _array(file, 'commands/gripper_mode', (command_count,), 'iu')
            busy = _array(file, 'commands/gripper_busy', (command_count,), 'biu')
            if not np.isin(modes, [0, 1, 2]).all() or not np.isin(busy, [0, 1]).all():
                raise ValueError('Invalid gripper mode/busy flag')
            plan_id = _array(file, 'commands/plan_id', (command_count,), 'iu')
            action_index = _array(file, 'commands/action_index', (command_count,), 'iu')
            if np.any((plan_id < -1) | (plan_id >= plan_count)) or np.any((plan_id == -1) != (action_index == -1)):
                raise ValueError('Invalid command plan/action reference')
        if plan_count:
            shape = file['plans/actions'].shape
            if len(shape) != 3 or shape[0] != plan_count or shape[1] < 1 or shape[2] != 10:
                raise ValueError('Expected plans/actions shape (P, horizon, 10)')
            actions = _array(file, 'plans/actions', shape)
            _array(file, 'plans/init_noise', shape)
            plan_tick = _array(file, 'plans/tick', (plan_count,))
            latency = _array(file, 'plans/latency', (plan_count,))
            if np.any(np.diff(plan_tick) <= 0) or np.any(latency < 0):
                raise ValueError('Invalid plan timing')
            history_shape = file['plans/history_ticks'].shape
            if len(history_shape) != 2 or history_shape[0] != plan_count or history_shape[1] < 1:
                raise ValueError('Invalid plan history shape')
            history = _array(file, 'plans/history_ticks', history_shape)
            if (np.any(np.diff(history, axis=1) <= 0) or np.any(history > plan_tick[:, None] + 1e-6)
                    or not np.allclose(history[:, -1], plan_tick, rtol=0, atol=1e-6)):
                raise ValueError('Noncausal plan history')
            _check_pose(_array(file, 'plans/actual_pose', (*history_shape, 7)), 'plans/actual_pose')
            plan_width = _array(file, 'plans/actual_gripper', history_shape)
            if np.any((plan_width < 0) | (plan_width > .085)) or np.any((actions[..., 9] < 0) | (actions[..., 9] > .08)):
                raise ValueError('Invalid plan gripper width')
            for camera in ('wrist', 'external'):
                if file['plans/rgb/' + camera].shape != history_shape:
                    raise ValueError(f'Inconsistent plan RGB: {camera}')
            if command_count:
                valid = plan_id >= 0
                indices = action_index[valid]
                ids = plan_id[valid]
                if np.any((indices < 0) | (indices >= shape[1])):
                    raise ValueError('Command action index outside prediction horizon')
                if np.any(plan_tick[ids] + latency[ids] > command_tick[valid] + 1e-6):
                    raise ValueError('Command references a plan before inference completed')
                if not np.allclose(requested[valid], actions[ids, indices], atol=1e-6, rtol=1e-5):
                    raise ValueError('Requested action disagrees with referenced plan')

        obs_command = _array(file, 'observations/command_index', (count,), 'iu')
        # A control tick can record an observation before/after a command with the same timestamp.
        left = np.searchsorted(command_tick, tick, side='left')
        right = np.searchsorted(command_tick, tick, side='right')
        if np.any((obs_command < left) | (obs_command > right)):
            raise ValueError('Observation command indices disagree with command timestamps')
        if obs_command[0] != 0 or obs_command[-1] != command_count:
            raise ValueError('Commands fall outside the recorded episode')
        starts = _array(file, 'transitions/command_start', (transitions,), 'iu')
        ends = _array(file, 'transitions/command_end', (transitions,), 'iu')
        for key, expected in [('obs_index', np.arange(transitions)), ('next_obs_index', np.arange(1, count)),
                              ('command_start', obs_command[:-1]), ('command_end', obs_command[1:])]:
            if not np.array_equal(_array(file, 'transitions/' + key, (transitions,), 'iu'), expected):
                raise ValueError(f'Invalid transition indices: {key}')
        dt = np.diff(tick)
        # Early v1 files did not store dt; deriving it from recorded timestamps loses no timing.
        if 'transitions/dt' in file and not np.allclose(
                _array(file, 'transitions/dt', (transitions,)), dt, rtol=1e-6, atol=1e-9):
            raise ValueError('Transition dt disagrees with observation timestamps')
        reward = _array(file, 'transitions/reward', (transitions,))
        terminated = _array(file, 'transitions/terminated', (transitions,), 'biu')
        truncated = _array(file, 'transitions/truncated', (transitions,), 'biu')
        expected_reward = np.zeros(transitions, np.float32)
        expected_reward[-1] = float(metadata['outcome'] == 'success')
        timeout = metadata.get('label', {}).get('reason') == 'timeout' and metadata['outcome'] != 'success'
        if (not np.array_equal(reward, expected_reward) or not np.isin(terminated, [0, 1]).all()
                or not np.isin(truncated, [0, 1]).all() or np.any(terminated[:-1]) or np.any(truncated[:-1])
                or bool(terminated[-1]) != (not timeout) or bool(truncated[-1]) != timeout):
            raise ValueError('Rewards/terminal flags disagree with the episode label')
        return dict(tick=tick, starts=starts, ends=ends, reward=reward, dt=dt,
                    terminated=terminated.astype(bool), truncated=truncated.astype(bool), plans=plan_count)

    def __len__(self):
        return len(self.indices)

    def _image(self, encoded):
        try:
            bgr = cv2.imdecode(np.asarray(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
        except cv2.error as exc:
            raise ValueError('Corrupt RGB encoding') from exc
        if bgr is None or bgr.shape != (224, 224, 3):
            raise ValueError('Corrupt RGB or image size differs from recorded 224x224 policy input')
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        if self.image_size != 224:
            rgb = cv2.resize(rgb, (self.image_size, self.image_size), interpolation=cv2.INTER_AREA)
        return torch.from_numpy(rgb.transpose(2, 0, 1).copy()).float() / 255.

    def _observation(self, file, indices):
        low, high = int(indices.min()), int(indices.max()) + 1
        pose = file['observations/pose'][low:high][indices - low]
        width = file['observations/gripper_width'][low:high][indices - low]
        obs = dict(robot_eef_pose=torch.from_numpy(pose7_to_pose9(pose)),
                   gripper_width=torch.from_numpy(width.astype(np.float32)[:, None]))
        for camera in ('wrist', 'external'):
            images = {i: self._image(file['observations/rgb/' + camera][int(i), 0])
                      for i in np.unique(indices)}
            obs['camera_' + camera] = torch.stack([images[i] for i in indices])
        return obs

    def __getitem__(self, index):
        episode_id, transition = self.indices[index]
        episode = self.episodes[episode_id]
        tick, next_tick = episode['tick'][transition:transition + 2]
        history = np.arange(transition - self.obs_steps + 1, transition + 1)
        next_history = history + 1
        obs_indices, next_indices = np.maximum(history, 0), np.maximum(next_history, 0)
        start, end = int(episode['starts'][transition]), int(episode['ends'][transition])
        begin = max(0, start - 1)  # Include the command already held at the interval's start.
        with h5py.File(episode['path'], 'r') as file:
            obs, next_obs = self._observation(file, obs_indices), self._observation(file, next_indices)
            commands = {}
            for key, dtype in [('tick', np.float64), ('requested_action', np.float32),
                               ('gripper_target', np.float32), ('gripper_mode', np.int64),
                               ('gripper_busy', bool), ('plan_id', np.int64), ('action_index', np.int64)]:
                tail = (10,) if key == 'requested_action' else ()
                value = file['commands/' + key][begin:end] if end > begin else np.empty((0, *tail))
                commands[key] = torch.from_numpy(value.astype(dtype))
            pose = file['commands/pose'][begin:end] if end > begin else np.empty((0, 7))
            action = torch.from_numpy(np.concatenate(
                [pose7_to_pose9(pose), commands['gripper_target'].numpy()[:, None]], axis=-1).astype(np.float32))
        commands['index'] = torch.arange(begin, end)
        commands['is_new'] = commands['index'] >= start
        effective = np.maximum(commands['tick'].numpy(), tick)
        commands['duration'] = torch.from_numpy(np.diff(np.r_[effective, next_tick]))
        uncovered = float((effective[0] if len(effective) else next_tick) - tick)
        return dict(obs=obs, next_obs=next_obs, action=action, commands=commands,
                    action_mask=torch.ones(len(action), dtype=torch.bool),
                    reward=torch.tensor(episode['reward'][transition], dtype=torch.float32),
                    terminated=torch.tensor(bool(episode['terminated'][transition]), dtype=torch.bool),
                    truncated=torch.tensor(bool(episode['truncated'][transition]), dtype=torch.bool),
                    dt=torch.tensor(episode['dt'][transition], dtype=torch.float64),
                    uncovered_dt=torch.tensor(uncovered, dtype=torch.float64),
                    obs_ticks=torch.from_numpy(episode['tick'][obs_indices].copy()),
                    next_obs_ticks=torch.from_numpy(episode['tick'][next_indices].copy()),
                    obs_mask=torch.from_numpy(history >= 0), next_obs_mask=torch.from_numpy(next_history >= 0),
                    episode_id=episode_id, transition_index=transition, command_start=start, command_end=end)

    def get_plan(self, episode_id, plan_id):
        """读取计划本身；plan_id=-1 是保持指令，没有对应的预测或噪声。"""
        if not 0 <= episode_id < len(self.episodes):
            raise IndexError('Episode index out of range')
        episode = self.episodes[episode_id]
        if not 0 <= plan_id < episode['plans']:
            raise IndexError('Plan index out of range; hold commands use plan_id=-1')
        with h5py.File(episode['path'], 'r') as file:
            pose = file['plans/actual_pose'][plan_id]
            width = file['plans/actual_gripper'][plan_id]
            obs = dict(robot_eef_pose=torch.from_numpy(pose7_to_pose9(pose)),
                       gripper_width=torch.from_numpy(width.astype(np.float32)[:, None]))
            for camera in ('wrist', 'external'):
                obs['camera_' + camera] = torch.stack(
                    [self._image(image) for image in file['plans/rgb/' + camera][plan_id]])
            return dict(obs=obs, predicted_action=torch.from_numpy(file['plans/actions'][plan_id].astype(np.float32)),
                        init_noise=torch.from_numpy(file['plans/init_noise'][plan_id].astype(np.float32)),
                        history_ticks=torch.from_numpy(file['plans/history_ticks'][plan_id]),
                        tick=float(file['plans/tick'][plan_id]), latency=float(file['plans/latency'][plan_id]),
                        episode_id=episode_id, plan_id=plan_id)


def collate_rl_batch(samples):
    """变长指令按本批最大长度补零；只在 action_mask=True 的位置使用指令。"""
    if not samples:
        raise ValueError('Cannot collate an empty batch')
    batch = default_collate([{k: v for k, v in sample.items()
                              if k not in {'action', 'action_mask', 'commands'}} for sample in samples])
    batch['action'] = pad_sequence([s['action'] for s in samples], batch_first=True)
    batch['action_mask'] = pad_sequence([s['action_mask'] for s in samples], batch_first=True)
    batch['commands'] = {
        key: pad_sequence([s['commands'][key] for s in samples], batch_first=True,
                          padding_value=-1 if key in {'index', 'plan_id', 'action_index'} else 0)
        for key in samples[0]['commands']}
    return batch
