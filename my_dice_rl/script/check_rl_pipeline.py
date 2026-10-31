"""完整离线演练：合成示范 -> DP -> RL 轨迹 -> 更新/恢复 -> 导出 -> 模拟执行。

不会导入 ROS 或向机器人发命令；生成的所有权重都标记为 simulated，不能部署真机。
"""
import argparse
import copy
from datetime import datetime
import json
from pathlib import Path
import threading
import time
from zoneinfo import ZoneInfo

import numpy as np
import torch
import yaml

from my_dice_rl.agent.eval.dp_executor import DPExecutor
from my_dice_rl.agent.eval.dp_policy import CheckpointPolicy
from my_dice_rl.agent.eval.rl_policy import load_execution_policy
from my_dice_rl.agent.finetune.train_dice_agent import TrainDiceAgent
from my_dice_rl.agent.pretrain.train_dp_agent import TrainDPAgent
from my_dice_rl.env.real.policy_demo import DemoBackend, feed_demo
from my_dice_rl.env.real.policy_observation import ObservationBuffer
from my_dice_rl.script.process_gear_dataset import convert_dataset
from my_dice_rl.util.checkpoint import load_policy
from my_dice_rl.util.config import PROJECT_ROOT, load_train_config
from my_dice_rl.util.recording import EpisodeWriter, Sample, atomic_json
from my_dice_rl.util.rl_checkpoint import load_rl_checkpoint
from my_dice_rl.util.rl_config import load_rl_config


def synthetic_demonstrations(root):
    for episode in range(3):
        config = dict(task='gear_insertion', simulated=True, record_hz=10., min_episode_frames=2,
            save_width=224, save_height=224, save_raw_rgb=False, jpeg_quality=95, max_age_s=.2,
            max_camera_skew_s=.06, base_frame='panda_link0', ee_frame='panda_hand_tcp')
        writer = EpisodeWriter(root / 'session', config)
        for i in range(12):
            tick = 1000. + episode * 10. + i * .1
            pose = [.45 + .0003 * i + .0001 * episode, 0., .3 + .0002 * i, 1., 0., 0., 0.]
            values = dict(wrist=np.full((224, 224, 3), [40, 70, 110], np.uint8),
                external=np.full((224, 224, 3), [30, 95, 65], np.uint8), eef=pose,
                target=[pose[0] + .0001, *pose[1:]], gripper=.035,
                teleop=dict(gripper_target_width=.035, gripper_busy=False, gripper_speed=.02,
                            gripper_force=5., gripper_command_mode='hold_existing'),
                robot=dict(q=np.zeros(7), dq=np.zeros(7), wrench=np.zeros(6)))
            writer.append(tick, {key: Sample(tick - .01, 1700000000. + tick, value) for key, value in values.items()})
        writer.close('success')


def collect_simulated(policy, root, outcome='success'):
    # 只读取 YAML；不经过 GUI/ROS 启动器。
    config = yaml.safe_load((PROJECT_ROOT / 'cfg/execute_rl.yaml').read_text())
    collector = yaml.safe_load((PROJECT_ROOT / 'cfg/collect.yaml').read_text())
    config.pop('collector_config')
    config.update(data_root=str(root), min_free_gb=0., device_lock=str(root.parent / 'device.lock'),
                  max_inference_latency_s=2., control_timeout_s=.5, max_age_s=.4,
                  max_sensor_skew_s=.2, max_episode_s=5.)
    buffer = ObservationBuffer()
    backend = DemoBackend(buffer)
    engine = DPExecutor(config, collector, buffer, backend, simulated=True, policy=policy)
    stop = threading.Event()
    feeder = threading.Thread(target=feed_demo, args=(buffer, backend, stop), daemon=True)
    feeder.start()

    def pump(seconds, predicate=None):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            engine.tick()
            if predicate is not None and predicate():
                return
            time.sleep(.02)
        if predicate is not None and not predicate():
            raise RuntimeError(engine.status())

    try:
        pump(.3)
        engine.submit('ready', held_confirmed=True)
        pump(2., lambda: engine.state == 'READY')
        engine.submit('start')
        pump(.9)
        if engine.state != 'RUNNING':
            raise RuntimeError(engine.status())
        engine.submit('stop')
        pump(3., lambda: engine.state == 'LABEL')
        engine.submit('label', outcome=outcome)
        pump(3., lambda: engine.state == 'RESET')
        if not engine.writer.meta['eligible_for_rl'] or not backend.commands:
            raise RuntimeError('Synthetic rollout did not produce valid transitions/commands')
        return engine.last_path
    finally:
        engine.submit('close')
        pump(3., lambda: engine.status()['can_close'])
        stop.set()
        feeder.join(timeout=2.)
        engine.shutdown()


def check_pipeline(output, device='cpu'):
    output = Path(output).expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('Choose a new empty smoke directory: ' + str(output))
    output.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(2)
    raw, dataset = output / '_demo/raw', output / '_demo/expert.zarr'
    synthetic_demonstrations(raw)
    convert_dataset(raw, dataset, allow_demo=True)
    config = load_train_config(overrides=['task.allow_demo=true', 'task.horizon_steps=8', 'task.act_steps=2',
        'training.device=' + device, 'training.num_workers=0', 'training.batch_size=2', 'training.epochs=1',
        'training.max_train_steps=1', 'training.max_val_steps=1', 'training.warmup_steps=0',
        'model.image_size=32', 'model.down_dims=[16,32]', 'model.time_embed_dim=16',
        'model.train_diffusion_steps=4', 'model.inference_steps=2'])
    config['task']['dataset_path'], config['training']['output_root'] = str(dataset), str(output / 'dp')
    teacher_checkpoint = TrainDPAgent(config).run() / 'checkpoint/best.pt'
    collector = yaml.safe_load((PROJECT_ROOT / 'cfg/collect.yaml').read_text())
    teacher = CheckpointPolicy(teacher_checkpoint, device, collector, real=False)
    rollout_root = output / 'rollouts'
    first = collect_simulated(teacher, rollout_root)
    second = collect_simulated(teacher, rollout_root, outcome='failed')
    rl = load_rl_config(overrides=['data.allow_demo=true', 'model.chunk_steps=2', 'model.hidden_dims=[32,32]',
        'model.ensemble_size=2', 'model.target_subset=2', 'model.multi_z=2', 'model.filter_warmup=0',
        'training.device=' + device, 'training.batch_size=2', 'training.encoding_batch_size=4',
        'training.critic_warmup=0', 'training.actor_update_freq=1', 'training.total_updates=3',
        'training.updates_per_round=2', 'training.min_online_chunks=1', 'training.chunk_stride=2',
        'training.checkpoint_every=2', 'training.replay_capacity=128', 'training.expert_capacity=128'],
        require_paths=False)
    rl.update(teacher_checkpoint=str(teacher_checkpoint), expert_dataset=str(dataset),
              rollout_root=str(rollout_root), device_lock=str(output / 'device.lock'))
    execution = yaml.safe_load((PROJECT_ROOT / 'cfg/execute_rl.yaml').read_text())
    execution.update(device_lock=str(output / 'device.lock'),
                     deployment_review=rl['review_config'], collector_config=str(PROJECT_ROOT / 'cfg/collect.yaml'))
    execution_path = output / 'execute_rl.yaml'
    execution_path.write_text(yaml.safe_dump(execution, sort_keys=False))
    rl['execution_config'] = str(execution_path)
    rl['training']['output_root'] = str(output / 'rl')
    learner = TrainDiceAgent(rl)
    run = learner.run()
    state = load_rl_checkpoint(run / 'checkpoint/last.pt')
    changed = float(learner.model.actor.network[-1].weight.detach().abs().sum())
    if state['global_step'] != 3 or changed == 0:
        raise RuntimeError('RL optimizer did not update')
    index_path = run / 'latest_policy.json'
    policy = load_execution_policy(index_path, device, collector, real=False)
    # 部署模型和 learner 对相同特征/噪声必须输出相同动作。
    batch = learner.replay.sample(2, device)
    noise = torch.randn(2, learner.model.horizon, 10, device=device)
    with torch.no_grad():
        trained, base = learner.model.action(batch['features'], noise)
        corrected = (base + policy.model.actor(batch['features'], noise).masked_fill(
            policy.model.action_normalizer.constant, 0.)).clamp(-1, 1)
    torch.testing.assert_close(trained, corrected)
    try:
        load_execution_policy(index_path, device, collector, real=True, execution={})
    except ValueError:
        rejected_real = True
    else:
        raise RuntimeError('Synthetic policy unexpectedly accepted real deployment')
    third = collect_simulated(policy, rollout_root)
    resumed_config = copy.deepcopy(rl)
    resumed_config['training']['total_updates'] = 4
    resumed = TrainDiceAgent(resumed_config, run / 'checkpoint/last.pt')
    resumed.run(watch=True)
    if resumed.global_step != 4 or len(resumed.data_manifest['online']) != 3:
        raise RuntimeError('Resume/online ingestion did not advance')
    reference, _ = load_policy(teacher_checkpoint, 'cpu')
    for key, value in learner.model.teacher.state_dict().items():
        torch.testing.assert_close(value.cpu(), reference.state_dict()[key], rtol=0, atol=0)
    report = dict(simulated=True, real_robot_commands_sent=False, device=device,
        teacher_checkpoint=str(teacher_checkpoint), rl_run=str(run), latest_policy_index=str(index_path),
        online_episodes=[str(first), str(second), str(third)], global_step=resumed.global_step,
        expert_chunks=resumed.replay.expert.size, online_chunks=resumed.replay.online.size,
        frozen_teacher_unchanged=True, export_matches_learner=True, synthetic_real_deployment_rejected=rejected_real)
    atomic_json(output / 'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output')
    parser.add_argument('--device', default='cpu')
    args = parser.parse_args()
    stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S_%f')
    output = args.output or PROJECT_ROOT / 'log_dir/rl_smoke' / stamp
    print(json.dumps(check_pipeline(output, args.device), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
