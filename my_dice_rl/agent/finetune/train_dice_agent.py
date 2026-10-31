"""单机 DICE-RL learner：冻结编码器、混合回放、周期保存、按新轨迹分轮更新。"""
import copy
from datetime import datetime
import json
from pathlib import Path
import random
import time
from zoneinfo import ZoneInfo

import numpy as np
import torch
from torch.utils.data import DataLoader
import yaml

from my_dice_rl.agent.dataset.gear_dataset import make_datasets
from my_dice_rl.agent.dataset.rl_dataset import RLDataset
from my_dice_rl.agent.dataset.rl_chunks import ExpertChunks, OnlineChunks
from my_dice_rl.model.rl.distill_rl import DistillRL
from my_dice_rl.util.checkpoint import atomic_save, move_to_device, rng_state, restore_rng
from my_dice_rl.util.recording import atomic_json
from my_dice_rl.util.replay_buffer import HybridReplayBuffer
from my_dice_rl.util.rl_checkpoint import (RL_CHECKPOINT_SCHEMA, snapshot_teacher, teacher_from_payload,
    load_rl_checkpoint, export_rl_policy)
from my_dice_rl.util.rl_config import digest, file_digest
from my_dice_rl.util.rl_coordination import DeviceLease


class TrainDiceAgent:
    def __init__(self, config, resume=None):
        self.config = copy.deepcopy(config)
        t, m = config['training'], config['model']
        self.device = torch.device(t['device'])
        if self.device.type not in ('cpu', 'cuda') or self.device.type == 'cuda' and not torch.cuda.is_available():
            raise ValueError('Requested CPU/CUDA training device is unavailable')
        torch.set_num_threads(t['torch_threads'])
        random.seed(t['seed']); np.random.seed(t['seed']); torch.manual_seed(t['seed'])
        self.lease = DeviceLease(config['device_lock'])
        state = load_rl_checkpoint(resume) if resume else None
        self._wait_device()
        with self.lease:
            if state:
                self.teacher_payload = state['teacher']
                teacher = teacher_from_payload(self.teacher_payload, self.device)
            else:
                teacher, self.teacher_payload = snapshot_teacher(config['teacher_checkpoint'], self.device)
            if file_digest(config['teacher_checkpoint']) != self.teacher_payload['sha256']:
                raise ValueError('Base DP checkpoint differs from frozen teacher')
            self.model = DistillRL(teacher, m).to(self.device).train()
        if m['chunk_steps'] > teacher.horizon_steps:
            raise ValueError('RL chunk_steps exceeds teacher horizon')
        task = copy.deepcopy(self.teacher_payload['task'])
        if task.get('allow_demo', True) and not config['data']['allow_demo']:
            raise ValueError('Synthetic teacher requires data.allow_demo=true')
        task['dataset_path'] = config['expert_dataset']
        self.expert_dataset, _ = make_datasets(task, self.teacher_payload['model_config']['image_size'])
        if self.expert_dataset.metadata['dataset_id'] != self.teacher_payload['dataset_id']:
            raise ValueError('Expert dataset is not the frozen DP training dataset')
        self.policy_dt = 1. / float(task['record_hz'])
        self.simulated = bool(config['data']['allow_demo'] or task.get('allow_demo', True)
                              or self.expert_dataset.metadata['simulated'])
        self.replay = HybridReplayBuffer(t['replay_capacity'], t['expert_capacity'], t['expert_ratio'], t['seed'])
        self.actor_optimizer = torch.optim.AdamW(self.model.actor.parameters(), lr=t['actor_lr'], weight_decay=t['weight_decay'])
        self.critic_optimizer = torch.optim.AdamW(self.model.critic.parameters(), lr=t['critic_lr'], weight_decay=t['weight_decay'])
        self.global_step = 0
        self.actor_updates = 0
        self.round_stop = 0
        self.data_manifest = dict(teacher_sha256=self.teacher_payload['sha256'], expert_id=self.teacher_payload['dataset_id'], online={})
        self.signature = digest(dict(model=m, reward=config['reward'], allow_demo=config['data']['allow_demo'],
            teacher=self.teacher_payload['sha256'], expert=self.teacher_payload['dataset_id'],
            training={key: t[key] for key in ('seed', 'batch_size', 'actor_lr', 'critic_lr', 'weight_decay',
                'actor_update_freq', 'critic_warmup', 'grad_clip_norm', 'target_tau', 'gamma', 'chunk_stride',
                'replay_capacity', 'expert_capacity', 'expert_ratio')}))
        self.ingesting, self.updating = False, False
        if state:
            if state['signature'] != self.signature:
                raise ValueError('Resume settings differ from model/optimizer/replay checkpoint')
            for name in ('actor', 'critic', 'target_actor', 'target_critic'):
                getattr(self.model, name).load_state_dict(state['model'][name], strict=True)
            self.actor_optimizer.load_state_dict(state['actor_optimizer'])
            self.critic_optimizer.load_state_dict(state['critic_optimizer'])
            self.replay.load_state_dict(state['replay'])
            self.data_manifest, self.global_step = state['data_manifest'], state['global_step']
            self.actor_updates = state.get('actor_updates', 0)
            self.round_stop = state.get('round_stop', self.global_step)
            for path, record in self.data_manifest['online'].items():
                if file_digest(path) != record['rollout_sha256'] or file_digest(Path(path).with_name('metadata.json')) != record['metadata_sha256']:
                    raise ValueError('Previously ingested rollout changed: ' + path)
            restore_rng(state['rng'])
            self.run_dir = Path(resume).resolve().parent.parent
        else:
            stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S_%f')
            self.run_dir = Path(t['output_root']) / stamp
            self.run_dir.mkdir(parents=True, exist_ok=False)
            expert = ExpertChunks(self.expert_dataset, m['chunk_steps'], teacher.horizon_steps,
                self.policy_dt, t['gamma'], config['reward'], t['chunk_stride'])
            self._ingest(expert, expert=True)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / 'config.yaml').write_text(yaml.safe_dump(self.config, sort_keys=False, allow_unicode=True))

    def _ingest(self, dataset, expert=False):
        self.ingesting = True
        try:
            loader = DataLoader(dataset, batch_size=self.config['training']['encoding_batch_size'], shuffle=False)
            for raw in loader:
                self._wait_device()
                with self.lease, torch.no_grad():
                    batch = move_to_device(raw, self.device)
                    values = {key: batch[key] for key in ('mask', 'duration', 'reward', 'discount',
                        'terminated', 'truncated', 'dt', 'noise', 'noise_valid')}
                    values['features'] = self.model.teacher.encode_obs(batch['obs']).float()
                    values['next_features'] = self.model.teacher.encode_obs(batch['next_obs']).float()
                    values['action'] = self.model.teacher.action_normalizer.normalize(batch['action']).float()
                    self.replay.add_batch(values, expert, raw['reference'])
        finally:
            self.ingesting = False

    def refresh_online(self):
        root = Path(self.config['rollout_root'])
        if not root.exists():
            return 0
        new = 0
        for metadata_path in sorted(root.rglob('metadata.json')):
            metadata = json.loads(metadata_path.read_text())
            path = metadata_path.with_name('rollout.h5').resolve()
            if str(path) in self.data_manifest['online']:
                record = self.data_manifest['online'][str(path)]
                stat = path.stat()
                if ([stat.st_size, stat.st_mtime_ns] != record['stat']
                        or file_digest(metadata_path) != record['metadata_sha256']):
                    raise ValueError('Already ingested rollout changed on disk: ' + str(path))
                continue
            if metadata.get('status') not in ('success', 'failed') or metadata.get('eligible_for_rl') is not True:
                continue
            data = RLDataset(path, obs_steps=self.model.teacher.obs_steps,
                image_size=self.teacher_payload['model_config']['image_size'],
                allow_demo=self.config['data']['allow_demo'], task=self.teacher_payload['task']['name'])
            identity = metadata.get('policy', {})
            teacher_sha = identity.get('teacher_sha256', identity.get('sha256'))
            if teacher_sha is not None and teacher_sha != self.teacher_payload['sha256']:
                raise ValueError('Online rollout comes from a different frozen DP prior')
            if not data.episodes[0]['simulated'] and teacher_sha is None:
                raise ValueError('Real rollout lacks frozen DP hash; record with the updated executor')
            coordinates = metadata['config'].get('collector', {})
            for key in ('base_frame', 'ee_frame'):
                if coordinates.get(key, self.teacher_payload['task'][key] if data.episodes[0]['simulated'] else None) != self.teacher_payload['task'][key]:
                    raise ValueError('Online rollout coordinate mismatch: ' + key)
            t, m = self.config['training'], self.config['model']
            chunks = OnlineChunks(data, m['chunk_steps'], self.model.horizon, self.policy_dt,
                                  t['gamma'], self.config['reward'], t['chunk_stride'])
            self._ingest(chunks)
            stat = path.stat()
            self.data_manifest['online'][str(path)] = dict(rollout_sha256=file_digest(path),
                metadata_sha256=file_digest(metadata_path), stat=[stat.st_size, stat.st_mtime_ns], chunks=len(chunks))
            new += 1
        return new

    def update(self):
        t = self.config['training']
        self.updating = True
        try:
            with self.lease:
                batch = self.replay.sample(t['batch_size'], self.device)
                self.critic_optimizer.zero_grad(set_to_none=True)
                loss, metrics = self.model.critic_loss(batch)
                self._backward(loss, self.model.critic.parameters(), self.critic_optimizer)
                if self.global_step >= t['critic_warmup'] and self.global_step % t['actor_update_freq'] == 0:
                    self.model.critic.requires_grad_(False)
                    try:
                        self.actor_optimizer.zero_grad(set_to_none=True)
                        actor_loss, actor_metrics = self.model.actor_loss(batch, self.global_step)
                        self._backward(actor_loss, self.model.actor.parameters(), self.actor_optimizer)
                        self.actor_updates += 1
                        metrics.update(actor_metrics)
                    finally:
                        self.model.critic.requires_grad_(True)
                self.model.update_targets(t['target_tau'])
                self.global_step += 1
                if self.device.type == 'cuda':
                    torch.cuda.synchronize(self.device)
        finally:
            self.updating = False
        metrics.update(global_step=self.global_step, online_chunks=self.replay.online.size,
                       expert_chunks=self.replay.expert.size, actor_updates=self.actor_updates)
        with (self.run_dir / 'metrics.jsonl').open('a') as stream:
            stream.write(json.dumps(metrics, allow_nan=False) + '\n')
        return metrics

    def _backward(self, loss, parameters, optimizer):
        parameters = list(parameters)
        if not torch.isfinite(loss):
            raise RuntimeError('Nonfinite RL loss; no checkpoint exported')
        loss.backward()
        norm = torch.nn.utils.clip_grad_norm_(parameters, self.config['training']['grad_clip_norm'])
        if not torch.isfinite(norm):
            raise RuntimeError('Nonfinite RL gradients')
        optimizer.step()
        if any(not torch.isfinite(value).all() for value in parameters):
            raise RuntimeError('Nonfinite RL parameters after optimizer update')

    def _wait_device(self):
        while not self.lease.acquire():
            time.sleep(.1)

    def save(self):
        self._wait_device()
        with self.lease:
            state = dict(schema=RL_CHECKPOINT_SCHEMA, config=self.config, signature=self.signature,
                teacher=self.teacher_payload, simulated=self.simulated, global_step=self.global_step,
                actor_updates=self.actor_updates,
                round_stop=self.round_stop,
                model={name: move_to_device(getattr(self.model, name).state_dict(), 'cpu')
                       for name in ('actor', 'critic', 'target_actor', 'target_critic')},
                actor_optimizer=move_to_device(self.actor_optimizer.state_dict(), 'cpu'),
                critic_optimizer=move_to_device(self.critic_optimizer.state_dict(), 'cpu'),
                replay=self.replay.state_dict(), data_manifest=self.data_manifest, rng=rng_state())
        path = self.run_dir / 'checkpoint/last.pt'
        atomic_save(path, state)
        policy_path = self.run_dir / f'policy/step_{self.global_step:08d}.pt'
        if not policy_path.exists():
            export_rl_policy(path, policy_path, allow_draft=True)
        atomic_json(self.run_dir / 'latest_policy.json', dict(schema='dice_gear_rl_index_v1',
            policy=str(policy_path.resolve()), sha256=file_digest(policy_path), global_step=self.global_step,
            simulated=self.simulated))
        return path

    def run(self, watch=False):
        t = self.config['training']
        try:
            new = self.refresh_online()
            while self.global_step < t['total_updates']:
                enough = self.replay.online.size >= t['min_online_chunks'] or t['allow_expert_only']
                pending_round = self.round_stop > self.global_step
                if not enough or watch and not pending_round and new < t['min_new_episodes']:
                    if not watch:
                        raise ValueError('有效在线动作块不足；先用 DP 执行界面采集并标注 RL 轨迹')
                    time.sleep(t['poll_seconds'])
                    new += self.refresh_online()
                    continue
                stop = min(self.round_stop if pending_round else self.global_step + t['updates_per_round'], t['total_updates'])
                self.round_stop = stop
                while self.global_step < stop:
                    if not self.lease.acquire():
                        time.sleep(min(t['poll_seconds'], .2))
                        continue
                    # update/save 内部重复 acquire 是幂等的；这里不跨更新持锁。
                    try:
                        metrics = self.update()
                    finally:
                        self.lease.release()
                    if self.global_step % t['checkpoint_every'] == 0:
                        self.save()
                    print(json.dumps(metrics), flush=True)
                self.save()
                if not watch:
                    new = max(1, new)  # 固定数据离线更新到 total_updates。
                else:
                    new = 0
        except KeyboardInterrupt:
            print('Learner interrupted; resume from the last atomic checkpoint.', flush=True)
        finally:
            self.lease.release()
        return self.run_dir
