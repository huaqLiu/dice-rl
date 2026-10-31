"""把实际执行目标按观测时钟组织成固定长度块，终止处使用 mask，不跨轨迹。"""
import h5py
import cv2
import numpy as np
import torch
from torch.utils.data import Dataset

from my_dice_rl.util.rotation import pose7_to_pose9


def returns(rewards, durations, gamma, policy_dt):
    elapsed = np.r_[0., np.cumsum(durations[:-1])] / policy_dt
    return float(np.sum(np.power(gamma, elapsed) * rewards)), float(gamma ** (durations.sum() / policy_dt))


def labels(outcome, terminated, truncated, durations, config, last):
    value = config['step_penalty'] * durations / config['policy_dt']
    if last:
        value[-1] += (config['success'] if outcome == 'success' else
                      config['timeout'] if truncated else config['failure'])
    return value


def pack(obs, next_obs, action, durations, reward, discount, terminated, truncated,
         chunk_steps, policy_dt, horizon, noise=None, noise_valid=False):
    count = len(action)
    index = np.minimum(np.arange(chunk_steps), count - 1)
    duration = np.zeros(chunk_steps, np.float32)
    duration[:count] = durations / policy_dt
    return dict(obs=obs, next_obs=next_obs, action=torch.as_tensor(action[index], dtype=torch.float32),
        mask=torch.arange(chunk_steps) < count, duration=torch.from_numpy(duration),
        reward=torch.tensor([reward], dtype=torch.float32), discount=torch.tensor([discount], dtype=torch.float32),
        terminated=torch.tensor([bool(terminated)]), truncated=torch.tensor([bool(truncated)]),
        dt=torch.tensor([float(np.sum(durations))], dtype=torch.float32),
        noise=torch.zeros(horizon, 10) if noise is None else torch.as_tensor(noise, dtype=torch.float32),
        noise_valid=torch.tensor([noise_valid]))


class OnlineChunks(Dataset):
    """使用每个区间中点已生效的实际目标，保留实际时长做 SMDP 折扣。

    这是对 50 Hz 控制目标的 10 Hz 重采样，不能还原区间内全部连续动力学；
    原始指令及预测仍保存在 rollout.h5，RLDataset 可用于审计。
    """
    def __init__(self, dataset, chunk_steps, horizon, policy_dt, gamma, reward, stride=1):
        self.dataset, self.chunk_steps, self.horizon = dataset, chunk_steps, horizon
        self.policy_dt, self.gamma, self.reward = policy_dt, gamma, {**reward, 'policy_dt': policy_dt}
        self.indices = []
        self.command_indices = []
        for episode_id, episode in enumerate(dataset.episodes):
            declared = episode['metadata']['config'].get('policy_dt', policy_dt)
            if not np.isclose(declared, policy_dt):
                raise ValueError('RL rollout policy_dt differs from the teacher')
            with h5py.File(episode['path'], 'r') as file:
                if episode['plans'] and file['plans/init_noise'].shape[1:] != (horizon, 10):
                    raise ValueError('Recorded noise horizon differs from the teacher')
                ticks = file['commands/tick'][:] if 'commands/tick' in file else np.empty(0)
            middle = (episode['tick'][:-1] + episode['tick'][1:]) / 2.
            commands = np.searchsorted(ticks, middle, side='right') - 1
            self.command_indices.append(commands)
            for start in range(0, len(episode['reward']), stride):
                end = min(start + chunk_steps, len(episode['reward']))
                if np.all(commands[start:end] >= 0):
                    self.indices.append((episode_id, start, end))

    def __len__(self):
        return len(self.indices)

    def __getitem__(self, index):
        episode_id, start, end = self.indices[index]
        episode = self.dataset.episodes[episode_id]
        history = np.maximum(np.arange(start - self.dataset.obs_steps + 1, start + 1), 0)
        next_history = np.maximum(np.arange(end - self.dataset.obs_steps + 1, end + 1), 0)
        indices = self.command_indices[episode_id][start:end]
        noise, noise_valid = None, False
        with h5py.File(episode['path'], 'r') as file:
            obs = self.dataset._observation(file, history)
            next_obs = self.dataset._observation(file, next_history)
            # h5py 不支持重复 fancy 索引，先读连续范围，再恢复保持指令。
            low, high = int(indices.min()), int(indices.max()) + 1
            poses = file['commands/pose'][low:high][indices - low]
            widths = file['commands/gripper_target'][low:high][indices - low]
            action = np.c_[pose7_to_pose9(poses), widths]
            plan_ids = file['commands/plan_id'][low:high][indices - low]
            if np.all(plan_ids >= 0) and np.all(plan_ids == plan_ids[0]):
                noise = file['plans/init_noise'][int(plan_ids[0])]
                noise_valid = True
        durations = episode['dt'][start:end]
        last = end == len(episode['reward'])
        terminated, truncated = episode['terminated'][end - 1], episode['truncated'][end - 1]
        reward = labels(episode['metadata']['outcome'], terminated, truncated, durations, self.reward, last)
        total, discount = returns(reward, durations, self.gamma, self.policy_dt)
        result = pack(obs, next_obs, action, durations, total, discount, terminated, truncated,
                      self.chunk_steps, self.policy_dt, self.horizon, noise, noise_valid)
        result['reference'] = f'{episode["path"]}#transition={start}:{end}'
        return result


class ExpertChunks(Dataset):
    """仅使用 DP 训练分区的成功示范；最后一帧作为后继观测，不能伪造额外下一帧。"""
    def __init__(self, dataset, chunk_steps, horizon, policy_dt, gamma, reward, stride=1):
        self.dataset, self.chunk_steps, self.horizon = dataset, chunk_steps, horizon
        self.policy_dt, self.gamma, self.reward = policy_dt, gamma, {**reward, 'policy_dt': policy_dt}
        self.indices = []
        starts = np.r_[0, dataset.episode_ends[:-1]]
        for episode in dataset.episode_ids:
            low, high = int(starts[episode]), int(dataset.episode_ends[episode])
            self.indices.extend((low, anchor, min(anchor + chunk_steps, high - 1), high)
                                for anchor in range(low, high - 1, stride))

    def __len__(self):
        return len(self.indices)

    def _obs(self, indices):
        obs = {key: torch.from_numpy(self.dataset.read(key, indices).astype(np.float32))
               for key in ('robot_eef_pose', 'gripper_width')}
        for key in ('camera_wrist', 'camera_external'):
            rgb = self.dataset.read(key, indices)
            if self.dataset.image_size != 224:
                rgb = np.stack([cv2.resize(x, (self.dataset.image_size,) * 2, interpolation=cv2.INTER_AREA) for x in rgb])
            obs[key] = torch.from_numpy(rgb.transpose(0, 3, 1, 2).copy()).float() / 255.
        return obs

    def __getitem__(self, index):
        low, start, end, high = self.indices[index]
        obs_indices = np.maximum(np.arange(start - self.dataset.sampler.obs_steps + 1, start + 1), low)
        next_indices = np.maximum(np.arange(end - self.dataset.sampler.obs_steps + 1, end + 1), low)
        action = self.dataset.read('action', np.arange(start, end))
        durations = np.full(end - start, self.policy_dt, np.float64)
        terminal = end == high - 1
        reward = labels('success', terminal, False, durations, self.reward, terminal)
        total, discount = returns(reward, durations, self.gamma, self.policy_dt)
        result = pack(self._obs(obs_indices), self._obs(next_indices), action, durations,
                      total, discount, terminal, False, self.chunk_steps, self.policy_dt, self.horizon)
        result['reference'] = f'{self.dataset.path}#expert={start}:{end}'
        return result
