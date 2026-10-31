"""Offline DP: complete-episode split, normalization, AMP, EMA and resumable updates."""
from datetime import datetime
import json
import math
import os
from pathlib import Path
import random
import shutil
from zoneinfo import ZoneInfo

import numpy as np
import torch
from torch.utils.data import DataLoader
from tqdm import tqdm
import yaml

from my_dice_rl.agent.dataset.gear_dataset import make_datasets
from my_dice_rl.model.diffusion.diffusion_policy import DiffusionPolicy
from my_dice_rl.util.checkpoint import (CHECKPOINT_SCHEMA, atomic_save, load_checkpoint,
                                        move_to_device, restore_rng, rng_state)
from my_dice_rl.util.config import model_config
from my_dice_rl.util.ema import EMA


def initialize_data_worker(worker_id):
    # Avoid a full OpenCV/Torch thread pool in every image-loading worker.
    import cv2
    torch.set_num_threads(1)
    cv2.setNumThreads(1)


class TrainDPAgent:
    def __init__(self, config, resume=None):
        self.config = config
        cfg = config['training']
        self._validate_training_config(cfg)
        torch.set_num_threads(cfg.get('torch_threads', 2))
        self.device = torch.device(cfg['device'])
        if self.device.type not in ('cpu', 'cuda'):
            raise ValueError('This training entry supports cpu or cuda devices')
        if self.device.type == 'cuda' and not torch.cuda.is_available():
            raise RuntimeError('CUDA unavailable; select training.device=cpu explicitly')
        random.seed(cfg['seed'])
        np.random.seed(cfg['seed'])
        torch.manual_seed(cfg['seed'])
        self.model_args = model_config(config)
        self.train_dataset, self.val_dataset = make_datasets(config['task'], self.model_args['image_size'])
        state = load_checkpoint(resume) if resume else None
        self.signature = dict(model=self.model_args,
            task={k: v for k, v in config['task'].items() if k != 'dataset_path'},
            training={k: cfg[k] for k in ('seed', 'batch_size', 'max_train_steps', 'learning_rate',
                'weight_decay', 'warmup_steps', 'min_lr_ratio', 'grad_clip_norm', 'use_amp', 'ema_decay')})
        if state:
            if state['signature'] != self.signature:
                raise ValueError('Resume model/task/optimizer settings differ from checkpoint')
            if state['dataset_id'] != self.train_dataset.metadata['dataset_id']:
                raise ValueError('Resume dataset differs from checkpoint')
        kwargs = {**self.model_args, 'pretrained_vision': False} if state else self.model_args
        self.model = DiffusionPolicy(**kwargs)
        if state:
            self.model.load_state_dict(state['model'])
        else:
            self.model.fit_normalizer(*self.train_dataset.normalizer_data())
        self.model.to(self.device)
        self.ema = EMA(self.model, cfg['ema_decay'])
        self.optimizer = torch.optim.AdamW(self.model.parameters(), lr=cfg['learning_rate'],
                                           weight_decay=cfg['weight_decay'])
        self.steps_per_epoch = math.ceil(len(self.train_dataset) / cfg['batch_size'])
        if cfg['max_train_steps'] is not None:
            self.steps_per_epoch = min(self.steps_per_epoch, cfg['max_train_steps'])
        self.schedule_total_steps = (state['schedule_total_steps'] if state else
                                     self.steps_per_epoch * cfg['epochs'])

        def learning_rate_factor(step):
            if step < cfg['warmup_steps']:
                return (step + 1) / max(1, cfg['warmup_steps'])
            progress = (step-cfg['warmup_steps']) / max(1, self.schedule_total_steps-cfg['warmup_steps'])
            cosine = .5 * (1 + math.cos(math.pi * min(max(progress, 0), 1)))
            return cfg['min_lr_ratio'] + (1-cfg['min_lr_ratio']) * cosine

        self.scheduler = torch.optim.lr_scheduler.LambdaLR(self.optimizer, learning_rate_factor)
        self.use_amp = bool(cfg['use_amp'] and self.device.type == 'cuda')
        self.scaler = torch.amp.GradScaler('cuda', enabled=self.use_amp)
        self.epoch, self.batch_in_epoch, self.global_step = 0, 0, 0
        self.best_loss = float('inf')
        self.training_complete = False
        if state:
            self.ema.load_state_dict(state['ema'])
            self.optimizer.load_state_dict(state['optimizer'])
            self.scheduler.load_state_dict(state['scheduler'])
            self.scaler.load_state_dict(state['scaler'])
            self.epoch, self.batch_in_epoch = state['epoch'], state['batch_in_epoch']
            self.global_step, self.best_loss = state['global_step'], state['best_loss']
            self.training_complete = bool(state.get('training_complete', False) and self.epoch >= cfg['epochs'])
            resume_path = Path(resume).resolve()
            self.run_dir = resume_path.parent.parent
            restore_rng(state['rng'])
        else:
            stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S_%f')
            self.run_dir = Path(cfg['output_root']) / stamp
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / 'config.yaml').write_text(yaml.safe_dump(config, sort_keys=False), encoding='utf-8')
        (self.run_dir / 'split.json').write_text(json.dumps(dict(
            dataset_id=self.train_dataset.metadata['dataset_id'],
            train_episodes=self.train_dataset.episode_ids, val_episodes=self.val_dataset.episode_ids), indent=2))
        print(f'Run: {self.run_dir}\nTrain: {len(self.train_dataset.episode_ids)} episodes / '
              f'{len(self.train_dataset)} windows; validation: {len(self.val_dataset.episode_ids)} episodes', flush=True)
        if not len(self.val_dataset):
            print('No validation split: best checkpoint uses training loss, not task success.', flush=True)

    @staticmethod
    def _validate_training_config(cfg):
        for key in ('epochs', 'batch_size'):
            if not isinstance(cfg[key], int) or cfg[key] <= 0:
                raise ValueError(f'{key} must be a positive integer')
        for key in ('max_train_steps', 'max_val_steps', 'max_total_steps'):
            if cfg[key] is not None and (not isinstance(cfg[key], int) or cfg[key] <= 0):
                raise ValueError(f'{key} must be null or a positive integer')
        if cfg['num_workers'] < 0 or cfg['warmup_steps'] < 0:
            raise ValueError('num_workers/warmup_steps must be nonnegative')
        if cfg['learning_rate'] <= 0 or cfg['weight_decay'] < 0 or cfg['grad_clip_norm'] <= 0:
            raise ValueError('Invalid optimizer parameters')
        if not 0 <= cfg['min_lr_ratio'] <= 1:
            raise ValueError('min_lr_ratio must be in [0,1]')
        for key, default in [('torch_threads', 2), ('checkpoint_every', 0)]:
            value = cfg.get(key, default)
            if not isinstance(value, int) or isinstance(value, bool) or value < (1 if key == 'torch_threads' else 0):
                raise ValueError(key + ' has an invalid integer value')

    def loader(self, dataset, epoch=0, shuffle=False):
        cfg = self.config['training']
        generator = torch.Generator().manual_seed(cfg['seed'] + epoch)
        # The epoch's permutation can be regenerated when resuming inside an epoch.
        return DataLoader(dataset, batch_size=cfg['batch_size'], shuffle=shuffle,
            num_workers=cfg['num_workers'], pin_memory=self.device.type == 'cuda',
            generator=generator, drop_last=False, worker_init_fn=initialize_data_worker)

    @torch.no_grad()
    def validate(self):
        if not len(self.val_dataset):
            return None
        self.ema.model.eval()
        total, count = 0., 0
        devices = [self.device.index or 0] if self.device.type == 'cuda' else []
        # Validation noise is fixed and does not perturb training RNG state.
        with torch.random.fork_rng(devices=devices):
            torch.manual_seed(self.config['training']['seed'] + 10000)
            for index, batch in enumerate(self.loader(self.val_dataset)):
                limit = self.config['training']['max_val_steps']
                if limit is not None and index >= limit:
                    break
                batch = move_to_device(batch, self.device)
                with torch.autocast(self.device.type, enabled=self.use_amp):
                    loss = self.ema.model.compute_loss(batch)
                if not torch.isfinite(loss):
                    raise RuntimeError('Non-finite validation loss')
                weight = int(batch['valid_mask'].sum())
                total += loss.item() * weight
                count += weight
        return total / count

    def save(self, is_best=False):
        state = dict(schema=CHECKPOINT_SCHEMA, config=self.config, model_config=self.model_args,
            signature=self.signature, dataset_id=self.train_dataset.metadata['dataset_id'],
            model=self.model.state_dict(), ema=self.ema.state_dict(),
            optimizer=self.optimizer.state_dict(), scheduler=self.scheduler.state_dict(),
            scaler=self.scaler.state_dict(), epoch=self.epoch, batch_in_epoch=self.batch_in_epoch,
            global_step=self.global_step, best_loss=self.best_loss,
            training_complete=self.training_complete,
            schedule_total_steps=self.schedule_total_steps, rng=rng_state())
        last = self.run_dir / 'checkpoint/last.pt'
        atomic_save(last, state)
        progress = dict(updated_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
            global_step=self.global_step, epoch=self.epoch, batch_in_epoch=self.batch_in_epoch,
            training_complete=self.training_complete,
            best_loss=self.best_loss if math.isfinite(self.best_loss) else None)
        temporary_progress = self.run_dir/'progress.json.tmp'
        temporary_progress.write_text(json.dumps(progress,allow_nan=False))
        temporary_progress.replace(self.run_dir/'progress.json')
        if is_best:
            best = last.with_name('best.pt')
            temporary = best.with_suffix('.tmp')
            temporary.unlink(missing_ok=True)
            try:
                os.link(last, temporary)
            except OSError:
                shutil.copyfile(last, temporary)
            os.replace(temporary, best)

    def run(self, stop_event=None):
        cfg = self.config['training']
        if self.epoch >= cfg['epochs'] and not self.training_complete:
            # A pause can arrive after the final update but before validation.
            val_loss = self.validate()
            is_best = val_loss is not None and val_loss < self.best_loss
            if is_best:
                self.best_loss = val_loss
            self.training_complete = True
            self.save(is_best)
            return self.run_dir
        if cfg['max_total_steps'] is not None and self.global_step >= cfg['max_total_steps']:
            return self.run_dir
        try:
            while self.epoch < cfg['epochs']:
                epoch, resume_batch = self.epoch, self.batch_in_epoch
                total, count, skipped = 0., 0, 0
                self.model.train()
                progress = tqdm(self.loader(self.train_dataset, epoch, shuffle=True),
                                total=self.steps_per_epoch, desc=f'Epoch {epoch+1}/{cfg["epochs"]}')
                for index, batch in enumerate(progress):
                    if stop_event is not None and stop_event.is_set():
                        break
                    if index >= self.steps_per_epoch:
                        break
                    if index < resume_batch:
                        continue
                    batch = move_to_device(batch, self.device)
                    self.optimizer.zero_grad(set_to_none=True)
                    with torch.autocast(self.device.type, enabled=self.use_amp):
                        loss = self.model.compute_loss(batch)
                    if not torch.isfinite(loss):
                        raise RuntimeError('Non-finite training loss; stopped without exporting a new checkpoint')
                    self.scaler.scale(loss).backward()
                    self.scaler.unscale_(self.optimizer)
                    grad_norm = torch.nn.utils.clip_grad_norm_(self.model.parameters(), cfg['grad_clip_norm'])
                    if not self.use_amp and not torch.isfinite(grad_norm):
                        raise RuntimeError('Non-finite gradients')
                    previous_scale = self.scaler.get_scale()
                    self.scaler.step(self.optimizer)
                    self.scaler.update()
                    updated = self.scaler.get_scale() >= previous_scale
                    if updated:
                        self.scheduler.step()
                        self.ema.update(self.model)
                        self.global_step += 1
                    else:
                        skipped += 1
                    self.batch_in_epoch = index + 1
                    if self.batch_in_epoch == self.steps_per_epoch:
                        self.epoch, self.batch_in_epoch = epoch + 1, 0
                    weight = int(batch['valid_mask'].sum())
                    total += loss.item() * weight
                    count += weight
                    progress.set_postfix(loss=f'{loss.item():.4f}', step=self.global_step)
                    interval = cfg.get('checkpoint_every', 0)
                    if updated and interval and self.global_step % interval == 0 and self.batch_in_epoch:
                        self.save()
                    if cfg['max_total_steps'] is not None and self.global_step >= cfg['max_total_steps']:
                        break
                if stop_event is not None and stop_event.is_set():
                    self.save()
                    print(json.dumps(dict(event='paused', epoch=self.epoch,
                        batch_in_epoch=self.batch_in_epoch, global_step=self.global_step)), flush=True)
                    return self.run_dir
                if not count:
                    raise RuntimeError('No training batches available at the resume position')
                train_loss = total / count
                val_loss = self.validate()
                selection = val_loss if val_loss is not None else train_loss
                is_best = selection < self.best_loss
                self.best_loss = min(self.best_loss, selection)
                self.training_complete = self.epoch >= cfg['epochs'] and self.batch_in_epoch == 0
                metrics = dict(epoch=epoch+1, next_epoch=self.epoch, batch_in_epoch=self.batch_in_epoch,
                    global_step=self.global_step, train_loss=train_loss, val_loss=val_loss,
                    learning_rate=self.optimizer.param_groups[0]['lr'], skipped_amp_updates=skipped,
                    selection_metric='val_loss' if val_loss is not None else 'train_loss')
                with (self.run_dir / 'metrics.jsonl').open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(metrics, allow_nan=False) + '\n')
                self.save(is_best)
                print(json.dumps(metrics), flush=True)
                if cfg['max_total_steps'] is not None and self.global_step >= cfg['max_total_steps']:
                    break
        except KeyboardInterrupt:
            # Resume from the last completed update, not a partially accumulated gradient.
            print('Interrupted; retaining the most recent atomic checkpoint.', flush=True)
        return self.run_dir
