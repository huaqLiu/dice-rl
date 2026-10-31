"""RL learner CLI。--watch 按新增完整轨迹分轮训练，不启动机器人或执行器。"""
import argparse
from pathlib import Path

from my_dice_rl.agent.finetune.train_dice_agent import TrainDiceAgent
from my_dice_rl.util.rl_config import DEFAULT_RL_CONFIG, load_rl_config
from my_dice_rl.util.config import project_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config')
    parser.add_argument('--resume')
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--set', nargs='*', default=[], metavar='KEY=VALUE')
    args = parser.parse_args()
    if args.resume:
        args.resume = str(project_path(args.resume))
    path = project_path(args.config) if args.config else (Path(args.resume).parent.parent / 'config.yaml' if args.resume else DEFAULT_RL_CONFIG)
    config = load_rl_config(path, args.set)
    print('RL training output:', TrainDiceAgent(config, args.resume).run(args.watch))


if __name__ == '__main__':
    main()
