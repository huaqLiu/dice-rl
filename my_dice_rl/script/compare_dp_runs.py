"""Compare completed DP runs on the same fixed dataset; never deploy a model."""
import argparse
from datetime import datetime
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

import yaml
from my_dice_rl.util.config import PROJECT_ROOT
from my_dice_rl.util.gpu_idle import IdleWindow,read_gpu


def read_json(path):
    try:return json.loads(Path(path).read_text())
    except (OSError,ValueError):return {}


def compare(status_paths,output,report_path):
    runs=[]
    terminal={'completed','training_completed','training_completed_evaluation_pending',
              'deadline_reached','training_failed','stopped'}
    all_terminal=True
    for status_path in status_paths:
        status_path=Path(status_path)
        state=read_json(status_path)
        all_terminal &= state.get('state') in terminal
        child=state.get('child_result',{})
        entry=dict(status_file=str(status_path),state=state.get('state','pending'),
                   training_complete=bool(child.get('training_complete')))
        if child.get('run_dir'):
            directory=Path(child['run_dir'])
            cfg=yaml.safe_load((directory/'config.yaml').read_text())
            manifest=read_json(Path(cfg['task']['dataset_path'])/'manifest.json')
            losses=[]
            metrics=directory/'metrics.jsonl'
            parse_errors=0
            for line in metrics.read_text().splitlines() if metrics.exists() else []:
                try:row=json.loads(line)
                except ValueError:
                    parse_errors+=1
                    continue
                if row.get('val_loss') is not None and math.isfinite(row['val_loss']):losses.append(row)
            best=min(losses,key=lambda r:r['val_loss']) if losses else {}
            checkpoint=Path(child['best_checkpoint'])
            entry.update(run_dir=str(directory),dataset_id=manifest.get('dataset_id'),
                pretrained_vision=bool(cfg['model']['pretrained_vision']),
                best_validation_loss=best.get('val_loss'),best_epoch=best.get('epoch'),
                checkpoint=str(checkpoint),checkpoint_exists=checkpoint.is_file(),
                metrics_parse_errors=parse_errors,
                evaluation=read_json(status_path.parent/'evaluation.json'))
        runs.append(entry)
    candidates=[r for r in runs if r['training_complete'] and r.get('checkpoint_exists')
                and r.get('best_validation_loss') is not None and not r.get('metrics_parse_errors')]
    comparable=len({r['dataset_id'] for r in candidates})<=1
    winner=min(candidates,key=lambda r:r['best_validation_loss']) if candidates and comparable else None
    result=dict(schema='dice_dp_comparison_v1',updated_at=datetime.now().astimezone().isoformat(),
        all_terminal=all_terminal,comparable=comparable,runs=runs,
        recommended_checkpoint=winner['checkpoint'] if winner else None,
        criterion='Minimum held-out denoising loss on the same frozen dataset; physical success is not measured.')
    path=Path(output);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2));temporary.replace(path)
    lines=['# DP 对照训练与推荐权重（自动更新）','',
        '更新时间：'+result['updated_at'],'',
        '两组使用相同真实数据快照、训练/验证轨迹划分和优化器；区别仅为视觉骨干随机初始化或 ImageNet 初始化。','',
        '| 视觉初始化 | 状态 | 最低验证去噪损失 | 最优轮次 | 前4步平均位置误差/mm |',
        '|---|---|---:|---:|---:|']
    for r in runs:
        error=r.get('evaluation',{}).get('execution_prefix_position_error_mm') or {}
        lines.append('| '+('ImageNet' if r.get('pretrained_vision') else '随机/待启动')+' | '+r['state']+
            ' | '+str(r.get('best_validation_loss','待定'))+' | '+str(r.get('best_epoch','待定'))+
            ' | '+str(error.get('mean','待评估'))+' |')
    if winner:
        lines+=['','当前推荐的已完成训练权重：','', '`'+winner['checkpoint']+'`','',
            '执行入口使用上面的 .pt 路径；本 JSON 是对照报告，不是可执行策略索引。']
    else:
        lines+=['','尚无可推荐的已完成训练权重，或数据集不一致而不能比较。']
    lines+=['','验证集用于模型选择，不是未见过的真机成功率测试。部署前仍需确认负载、空间范围并人工开始；不会自动驱动机械臂。','']
    report_path=Path(report_path);report_path.parent.mkdir(parents=True,exist_ok=True)
    tmp=report_path.with_suffix('.md.tmp');tmp.write_text('\n'.join(lines));tmp.replace(report_path)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--statuses',nargs='+',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--report',required=True)
    parser.add_argument('--deadline',required=True)
    parser.add_argument('--profile-output',help='Optional idle-GPU execution-predictor timing report')
    args=parser.parse_args()
    deadline=datetime.fromisoformat(args.deadline)
    if deadline.tzinfo is None:parser.error('deadline requires timezone')
    while True:
        result=compare(args.statuses,args.output,args.report)
        if result['all_terminal'] or time.time()>=deadline.timestamp():break
        time.sleep(min(15.,max(.1,deadline.timestamp()-time.time())))
    if args.profile_output and result.get('recommended_checkpoint'):
        idle=IdleWindow(seconds=60,max_utilization=60,max_used_mib=6144)
        while time.time()<deadline.timestamp():
            try:available=idle.update(read_gpu(),time.monotonic())
            except Exception:
                available=False
                idle.since=None
            if available:
                command=[sys.executable,'-m','my_dice_rl.script.profile_dp_runtime',
                    '--checkpoint',result['recommended_checkpoint'],'--output',args.profile_output]
                with Path(args.profile_output).with_suffix('.log').open('a') as log:
                    try:
                        done=subprocess.run(command,cwd=PROJECT_ROOT.parent,
                            env=dict(os.environ,OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2'),
                            stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                            timeout=max(.1,deadline.timestamp()-time.time()))
                    except subprocess.TimeoutExpired:
                        break
                if done.returncode==0:
                    result['runtime_profile']=read_json(args.profile_output)
                    break
                idle.since=None
            time.sleep(min(15.,max(.1,deadline.timestamp()-time.time())))
        output=Path(args.output)
        temporary=output.with_suffix('.tmp')
        temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2))
        temporary.replace(output)
        if result.get('runtime_profile'):
            report=Path(args.report)
            text=(report.read_text()+'\n## 推荐模型的 GPU 推理耗时\n\n`'+
                json.dumps(result['runtime_profile']['latency_ms'],ensure_ascii=False)+'` ms\n\n'+
                '是否全部低于当前推理时限：'+str(result['runtime_profile']['all_within_budget'])+'。未发送机械臂指令。\n')
            temporary=report.with_suffix('.md.tmp');temporary.write_text(text);temporary.replace(report)
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
