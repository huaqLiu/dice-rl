"""Wait for an idle GPU; pause only our own DP child if competing compute appears."""
import argparse
from datetime import datetime
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from my_dice_rl.util.config import PROJECT_ROOT
from my_dice_rl.util.gpu_idle import IdleWindow, read_gpu
from my_dice_rl.util.overnight_report import render_report


def atomic_status(path, state):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    temporary.replace(path)
    try:
        render_report(path.parent/'report.md',state)
    except Exception as exc:
        print('report_error: '+str(exc),flush=True)


def load_result(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def evaluate_after_training(state, output, deadline):
    result = state['child_result']
    checkpoint = Path(result['best_checkpoint'])
    if not checkpoint.exists():
        checkpoint = Path(result['last_checkpoint'])
    evaluation = output/'evaluation.json'
    state.update(state='evaluating_on_cpu', evaluation_file=str(evaluation))
    atomic_status(output/'status.json',state)
    command = [sys.executable,'-m','my_dice_rl.script.evaluate_dp','--checkpoint',str(checkpoint),
               '--output',str(evaluation),'--device','cpu','--max-windows','15']
    env = dict(os.environ,CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2',MKL_NUM_THREADS='2')
    with (output/'evaluation.log').open('a') as stream:
        child = subprocess.Popen(command,cwd=PROJECT_ROOT.parent,env=env,stdin=subprocess.DEVNULL,
                                 stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
        while child.poll() is None and time.time()<deadline.timestamp():
            state['updated_at'] = datetime.now().astimezone().isoformat()
            atomic_status(output/'status.json',state)
            time.sleep(.5)
        if child.poll() is None:
            child.terminate()  # Owned CPU-only evaluator, never an unrelated process.
            try:
                child.wait(timeout=30.)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    report = load_result(evaluation)
    state.update(state='completed' if child.returncode==0 and report.get('complete') else 'training_completed_evaluation_pending',
                 evaluation_exit_code=child.returncode,evaluation=report)
    atomic_status(output/'status.json',state)
    return state


def supervise(config, output, deadline, gpu='0', poll_seconds=15., idle_seconds=60., post_evaluate=False,
              max_used_mib=1024.,max_utilization=5.,allowed_compute_pids=(),share_gpu=False,
              initial_resume=None):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    window = IdleWindow(idle_seconds,max_utilization=max_utilization,max_used_mib=max_used_mib)
    allowed_compute_pids = frozenset(allowed_compute_pids)
    resume = str(Path(initial_resume).expanduser().resolve()) if initial_resume else None
    if resume and not Path(resume).is_file():
        raise FileNotFoundError(resume)
    previous_attempts = [int(p.name.split('_')[1]) for p in output.glob('attempt_*_result.json')
                         if p.name.split('_')[1].isdigit()] if resume else []
    child, log, attempts = None, None, max(previous_attempts,default=0)
    result_path = output/'not_started.json'
    stop_requested = False
    shutdown = False
    def stop_supervisor(*_):
        nonlocal shutdown
        shutdown = True
    previous = {s: signal.signal(s, stop_supervisor) for s in (signal.SIGINT, signal.SIGTERM)}
    state = dict(state='waiting', config=str(Path(config).resolve()), deadline=deadline.isoformat(),
                 supervisor_pid=os.getpid(), attempts=attempts, gpu_index=gpu,
                 gpu_mode='shared' if share_gpu or allowed_compute_pids else 'exclusive',
                 allow_any_compute=bool(share_gpu),
                 allowed_compute_pids=sorted(allowed_compute_pids),
                 idle_criteria=dict(seconds=idle_seconds,max_utilization=max_utilization,max_used_mib=max_used_mib,
                                    require_no_compute_processes=not (share_gpu or allowed_compute_pids),
                                    require_no_unlisted_compute_processes=not share_gpu))
    try:
        while True:
            now = time.time()
            expired = now >= deadline.timestamp() or shutdown
            try:
                sample = read_gpu(gpu)
                state['gpu'] = sample
                state.pop('probe_error', None)
            except Exception as exc:
                sample = None
                window.since = None
                state['probe_error'] = str(exc)
            state['updated_at'] = datetime.now().astimezone().isoformat()
            if child is not None:
                result = load_result(result_path)
                code = child.poll()
                state.update(child_pid=child.pid, child_result=result, exit_code=code)
                if code is not None:
                    log.close()
                    log = None
                    child = None
                    if code == 0 and result.get('training_complete'):
                        state['state'] = 'training_completed'
                        atomic_status(output/'status.json', state)
                        if post_evaluate:
                            return evaluate_after_training(state,output,deadline)
                        return state
                    checkpoint = Path(result.get('last_checkpoint', '/nonexistent'))
                    if code == 75:
                        window.since = None
                        state['state'] = 'waiting_for_project_device_lock'
                    elif code == 0 and checkpoint.is_file():
                        resume = str(checkpoint)
                        window.since = None
                        state.update(state='waiting_to_resume', resume=resume)
                    else:
                        state['state'] = 'training_failed'
                        atomic_status(output/'status.json', state)
                        return state
                else:
                    external = [] if sample is None or share_gpu else [p for p in sample['processes']
                        if p['pid'] != child.pid and p['pid'] not in allowed_compute_pids]
                    if (expired or external or sample is None) and not stop_requested:
                        # The result file is first created after signal handlers are installed.
                        if result.get('state') in ('initializing', 'running', 'paused', 'completed'):
                            child.send_signal(signal.SIGUSR1)
                            stop_requested = True
                            state.update(state='saving_and_yielding',
                                pause_reason='deadline/shutdown' if expired else 'external_compute_or_probe_error')
                    elif not stop_requested:
                        state['state'] = 'training'
            if child is None:
                if expired:
                    state.update(state='deadline_reached' if not shutdown else 'stopped', resume=resume)
                    atomic_status(output/'status.json', state)
                    return state
                # Keep the complete GPU sample in status/history; only the startup
                # eligibility check excludes processes explicitly allowed to share.
                eligible = None if sample is None else dict(sample, processes=[
                    p for p in sample['processes'] if not share_gpu and p['pid'] not in allowed_compute_pids])
                if eligible is not None and window.update(eligible, time.monotonic()):
                    attempts += 1
                    result_path = output/f'attempt_{attempts:03d}_result.json'
                    if result_path.exists():
                        raise FileExistsError('Supervisor output already contains this attempt; use a new directory')
                    command = [sys.executable, '-m', 'my_dice_rl.script.train_dp',
                               '--config', str(Path(config).resolve()), '--result', str(result_path)]
                    if resume:
                        command += ['--resume', resume]
                    env = dict(os.environ, CUDA_VISIBLE_DEVICES=sample['uuid'],
                        OMP_NUM_THREADS='2', MKL_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2',
                        NUMEXPR_NUM_THREADS='2', PYTHONUNBUFFERED='1')
                    log = (output/f'attempt_{attempts:03d}.log').open('x')
                    child = subprocess.Popen(command, cwd=PROJECT_ROOT.parent, env=env,
                        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                        start_new_session=True)
                    stop_requested = False
                    state.update(state='starting', attempts=attempts, child_pid=child.pid,
                                 result_file=str(result_path))
                    window.since = None
                elif sample is not None:
                    state.update(state='waiting_to_resume' if resume else 'waiting_for_idle_gpu',
                                 idle_observed_seconds=0. if window.since is None else time.monotonic()-window.since)
            atomic_status(output/'status.json', state)
            with (output/'gpu_observations.jsonl').open('a') as events:
                events.write(json.dumps(dict(time=state['updated_at'], state=state['state'],
                    gpu=sample, error=state.get('probe_error')), ensure_ascii=False)+'\n')
            time.sleep(min(poll_seconds, max(.1, deadline.timestamp()-time.time())) if not expired else .5)
    finally:
        # Never stop unrelated GPU tasks. Our child is signaled only by the loop above.
        if log is not None:
            log.close()
        for number, handler in previous.items():
            signal.signal(number, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--deadline', required=True, help='ISO timestamp with timezone')
    parser.add_argument('--gpu', default='0')
    parser.add_argument('--poll-seconds', type=float, default=15.)
    parser.add_argument('--idle-seconds', type=float, default=60.)
    parser.add_argument('--skip-evaluation', action='store_true')
    parser.add_argument('--max-used-mib',type=float,default=1024.,help='Allow graphics memory while requiring no compute apps')
    parser.add_argument('--max-utilization',type=float,default=5.,help='Maximum utilization during the idle observation window')
    parser.add_argument('--allow-compute-pid',type=int,action='append',default=[],
                        help='Explicitly allow sharing with this existing compute PID; repeat for multiple jobs')
    parser.add_argument('--share-gpu',action='store_true',
                        help='Allow other compute jobs, including restarted PIDs; startup memory limits still apply')
    parser.add_argument('--resume',help='Resume an existing DP last.pt instead of starting a new run')
    args = parser.parse_args()
    deadline = datetime.fromisoformat(args.deadline)
    if (deadline.tzinfo is None or not all(math.isfinite(v) for v in
            [args.poll_seconds,args.idle_seconds,args.max_used_mib,args.max_utilization])
            or args.poll_seconds <= 0 or args.idle_seconds < 0 or args.max_used_mib < 0
            or not 0<=args.max_utilization<=100 or any(pid<=0 for pid in args.allow_compute_pid)):
        parser.error('Timezone and positive polling interval are required')
    print(json.dumps(supervise(args.config, args.output, deadline, args.gpu,
                              args.poll_seconds, args.idle_seconds, not args.skip_evaluation,
                              args.max_used_mib,args.max_utilization,args.allow_compute_pid,args.share_gpu,
                              args.resume), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
