"""Launch offline DP training with YAML and explicit dotted overrides."""
import argparse
import json
import os
from pathlib import Path
import signal
import threading

from my_dice_rl.agent.pretrain.train_dp_agent import TrainDPAgent
from my_dice_rl.util.config import DEFAULT_TRAIN_CONFIG, load_train_config
from my_dice_rl.util.rl_coordination import DeviceLease


def write_result(path, result):
    if not path:
        return
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', help='Defaults to training YAML, or saved run YAML when resuming')
    parser.add_argument('--resume', help='This project\'s checkpoint/last.pt')
    parser.add_argument('--set', nargs='*', default=[], metavar='KEY=VALUE')
    parser.add_argument('--result', help='Optional atomic JSON completion/pause result')
    args = parser.parse_args()
    path = (args.config or (Path(args.resume).resolve().parent.parent / 'config.yaml'
                           if args.resume else DEFAULT_TRAIN_CONFIG))
    config = load_train_config(path, args.set)
    stopped = threading.Event()
    signals = (signal.SIGINT, signal.SIGTERM, signal.SIGUSR1)
    handlers = {number: signal.signal(number, lambda *_: stopped.set()) for number in signals}
    write_result(args.result, dict(state='initializing', pid=os.getpid()))
    lease = (DeviceLease(config['training']['device_lock'])
        if str(config['training']['device']).startswith('cuda') and config['training'].get('device_lock') else None)
    try:
        if lease and not lease.acquire():
            write_result(args.result, dict(state='resource_busy', training_complete=False))
            print('GPU 正由本项目的训练或执行进程占用，请等待释放', flush=True)
            raise SystemExit(75)
        agent = TrainDPAgent(config, args.resume)
        write_result(args.result, dict(state='running',training_complete=False,run_dir=str(agent.run_dir),
            best_checkpoint=str(agent.run_dir/'checkpoint/best.pt'),last_checkpoint=str(agent.run_dir/'checkpoint/last.pt')))
        output = agent.run(stop_event=stopped)
        result = dict(state='completed' if agent.training_complete else 'paused',
            run_dir=str(output), training_complete=agent.training_complete,
            global_step=agent.global_step, epoch=agent.epoch, batch_in_epoch=agent.batch_in_epoch,
            best_checkpoint=str(output/'checkpoint/best.pt'), last_checkpoint=str(output/'checkpoint/last.pt'))
        write_result(args.result, result)
        print('Training output:', output)
        print(json.dumps(result), flush=True)
    finally:
        if lease:
            lease.release()
        for number, handler in handlers.items():
            signal.signal(number, handler)


if __name__ == '__main__':
    main()
