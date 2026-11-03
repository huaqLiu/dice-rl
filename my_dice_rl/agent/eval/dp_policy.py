"""Load an EMA checkpoint and turn synchronized history into DP tensors."""
import hashlib
from pathlib import Path
import time

import cv2
import numpy as np
import torch

from my_dice_rl.util.checkpoint import load_policy
from my_dice_rl.util.recording import policy_image
from my_dice_rl.util.rotation import pose7_to_pose9
from my_dice_rl.util.cameras import camera_names


class CheckpointPolicy:
    def __init__(self, path, device, collector, real=True):
        self.path = str(Path(path).expanduser().resolve())
        self.model, state = load_policy(self.path, device, use_ema=True)
        task = state['config']['task']
        if real and task.get('allow_demo', True):
            raise ValueError('真实执行只接受 allow_demo=false 的正式数据 checkpoint')
        self._configure(task, state['model_config'], device, collector, real)
        self.identity = dict(path=self.path, dataset_id=state['dataset_id'],
                             global_step=state['global_step'], weights='ema',
                             task=task, model_config={**state['model_config'], 'action_dim': self.action_dim,
                                                      'state_dim': self.model.state_normalizer.feature_dim})
        with Path(self.path).open('rb') as stream:
            self.identity['sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest() if hasattr(hashlib, 'file_digest') else self._hash_stream(stream)
        self._warmup()

    @staticmethod
    def _hash_stream(stream):
        value = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
        return value.hexdigest()

    def _configure(self, task, model_config, device, collector, real):
        if real and task.get('allow_demo', True):
            raise ValueError('真实执行只接受 allow_demo=false 的正式数据 checkpoint')
        for key in ('base_frame', 'ee_frame', 'name'):
            expected = collector['task'] if key == 'name' else collector[key]
            if task[key] != expected:
                raise ValueError(f'Checkpoint {key} 与执行配置不一致')
        if not bool(self.model.state_normalizer.fitted) or not bool(self.model.action_normalizer.fitted):
            raise ValueError('Checkpoint 缺少已拟合归一化')
        self.camera_names = camera_names(task)
        self.camera_keys = tuple('camera_' + key for key in self.camera_names)
        if self.camera_keys != tuple(self.model.obs_encoder.camera_keys if hasattr(self.model, 'obs_encoder')
                                     else self.model.teacher.obs_encoder.camera_keys):
            raise ValueError('Checkpoint task cameras disagree with model')
        if self.camera_names != camera_names(collector):
            raise ValueError('策略相机数量/顺序与采集配置不一致')
        self.dt = 1 / float(task['record_hz'])
        self.obs_steps = self.model.obs_steps
        self.horizon = self.model.horizon_steps
        self.act_steps = self.model.act_steps
        self.action_dim = self.model.action_normalizer.feature_dim
        self.image_size = model_config['image_size']
        self.device = torch.device(device)

    def _warmup(self):
        # Warm CUDA kernels before the user can confirm reset/start a physical episode.
        center = self.model.state_normalizer.center
        obs = {key: torch.zeros(1, self.obs_steps, 3, self.image_size, self.image_size,
                                device=self.device) for key in self.camera_keys}
        obs['robot_eef_pose'] = center[:9].expand(1, self.obs_steps, 9)
        if center.numel() == 10:
            obs['gripper_width'] = center[9:].expand(1, self.obs_steps, 1)
        with torch.inference_mode():
            self.model.predict_action(obs)
        if self.device.type == 'cuda':
            torch.cuda.synchronize(self.device)

    def predict(self, history):
        obs = {}
        for source, key in zip(self.camera_names, self.camera_keys):
            images = []
            for frame in history:
                image = policy_image(frame[source].value)
                if self.image_size != 224:
                    image = cv2.resize(image, (self.image_size, self.image_size), interpolation=cv2.INTER_AREA)
                images.append(image)
            rgb = np.stack(images).transpose(0, 3, 1, 2).copy()
            obs[key] = torch.from_numpy(rgb).unsqueeze(0).to(self.device).float() / 255.
        pose = pose7_to_pose9(np.stack([frame['eef'].value for frame in history]))
        obs['robot_eef_pose'] = torch.from_numpy(pose).unsqueeze(0).to(self.device)
        if self.model.state_normalizer.feature_dim == 10:
            width = np.array([[frame['gripper'].value] for frame in history], dtype=np.float32)
            obs['gripper_width'] = torch.from_numpy(width).unsqueeze(0).to(self.device)
        noise = torch.randn(1, self.horizon, self.action_dim, device=self.device)
        with torch.inference_mode():
            prediction = self.model.predict_action(obs, init_noise=noise)['action_pred'][0].cpu().numpy()
        return prediction, noise[0].cpu().numpy()


class DemoPolicy:
    obs_steps, dt, horizon, act_steps = 2, .1, 20, 4
    action_dim = 9
    path = 'SIMULATED_POLICY'
    identity = dict(path=path, weights='synthetic', simulated=True, model_config=dict(action_dim=9))

    def predict(self, history):
        pose = pose7_to_pose9(np.asarray(history[-1]['eef'].value))
        action = pose.astype(np.float32)
        actions = np.repeat(action[None], self.horizon, axis=0)
        actions[:, 2] -= np.linspace(0, .004, self.horizon)
        return actions, np.zeros_like(actions)
