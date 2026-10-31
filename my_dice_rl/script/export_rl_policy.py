"""将 learner checkpoint 导出为嵌入基础 DP 的单文件部署包，不启动 ROS。"""
import argparse

from my_dice_rl.util.config import project_path
from my_dice_rl.util.rl_checkpoint import export_rl_policy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--allow-draft', action='store_true', help='只生成不能用于真机的模拟/未确认包')
    args = parser.parse_args()
    print(export_rl_policy(project_path(args.checkpoint), project_path(args.output), args.allow_draft))


if __name__ == '__main__':
    main()
