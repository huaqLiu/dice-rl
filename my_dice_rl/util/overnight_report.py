"""Continuously updated overnight status without CUDA imports."""
import json
from pathlib import Path
import yaml


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def render_report(output, state):
    names = dict(waiting_for_idle_gpu='等待 GPU 连续空闲', starting='正在启动训练',
        training='DP 训练中', waiting_to_resume='等待空闲后恢复断点',
        saving_and_yielding='正在保存断点并让出 GPU', waiting_for_project_device_lock='等待项目资源锁',
        evaluating_on_cpu='训练完成，CPU 离线评估中', completed='训练和离线评估完成',
        training_completed='训练完成', training_completed_evaluation_pending='训练完成，评估待检查',
        deadline_reached='已到截止时间', stopped='等待任务已停止', training_failed='训练失败，需检查日志')
    child, gpu = state.get('child_result', {}), state.get('gpu', {})
    try:
        config = yaml.safe_load(Path(state['config']).read_text())
        manifest = read_json(Path(config['task']['dataset_path'])/'manifest.json')
    except (OSError, KeyError, TypeError, yaml.YAMLError):
        config, manifest = {}, {}
    progress = read_json(Path(child['run_dir'])/'progress.json') if child.get('run_dir') else {}
    lines = ['# 夜间任务状态（自动更新）', '',
        '**当前状态：'+names.get(state.get('state'),state.get('state','未知'))+'**', '',
        '- 更新时间：'+str(state.get('updated_at','未知')),
        '- 截止时间：'+str(state.get('deadline','未知')),
        '- 训练启动次数：'+str(state.get('attempts',0)),
        '- 数据快照：'+str(manifest.get('episodes','待读取'))+' 条有效成功示范，'+str(manifest.get('frames','待读取'))+' 帧。',
        '- 当前计划：100 epochs，batch 8，原有 DP 网络结构；具体以训练快照 YAML 为准。',
        '- GPU 最近利用率：'+str(gpu.get('utilization','未知'))+'%，显存 '+str(gpu.get('used_mib','未知'))+' MiB。',
        '- GPU 最近计算进程：'+str([p['pid'] for p in gpu.get('processes',[])]), '',
        ('共享 GPU 模式，允许其他计算进程'+('（包含重启后的进程）' if state.get('allow_any_compute')
         else '：'+str(state.get('allowed_compute_pids',[])))+
         '；仍检查启动显存条件，不终止其他进程，不发送机械臂运动指令。'
         if state.get('gpu_mode')=='shared' else
         '只在 GPU 连续空闲后启动；不终止其他进程，不发送机械臂运动指令。'), '']
    if child.get('run_dir'):
        lines += ['## 训练产物', '', '- 运行目录：`'+child['run_dir']+'`',
            '- 最新 checkpoint：`'+child.get('last_checkpoint','待写出')+'`',
            '- 最佳 EMA checkpoint：`'+child.get('best_checkpoint','待写出')+'`',
            '- 最新已保存进度：`'+json.dumps(progress,ensure_ascii=False)+'`','']
    evaluation = state.get('evaluation', {})
    if evaluation:
        lines += ['## 离线评估', '', '- 完成窗口数：'+str(evaluation.get('completed_windows')),
            '- 位置误差 / mm：`'+json.dumps(evaluation.get('position_error_mm'),ensure_ascii=False)+'`',
            '- 旋转误差 / deg：`'+json.dumps(evaluation.get('rotation_error_deg'),ensure_ascii=False)+'`',
            '- 夹爪误差 / mm：`'+json.dumps(evaluation.get('gripper_error_mm'),ensure_ascii=False)+'`','',
            '离线模仿误差不代表真机装配成功率；CPU 推理耗时不代表 4090 部署耗时。','']
    if state.get('state') in ('deadline_reached','training_failed','stopped') and not child.get('training_complete'):
        lines += ['**尚未完成正式 DP 训练。** 请查看等待历史、失败日志或断点，不把模拟测试视为训练完成。','']
    lines += ['## 明早检查', '',
        '1. 检查本报告状态和 `status.json`；训练日志为 `attempt_*.log`。',
        '2. 若已完成训练，先查看 `evaluation.json`；没有进行无人值守真机执行。',
        '3. 默认部署范围为 100 mm，当前示范的最大目标距离约 117.5 mm，需要现场确认执行范围。',
        '4. 部署前确认负载/质心、现场空间及控制限制；不自动放宽保护。', '',
        '系统改动说明：项目目录中的 `OVERNIGHT_CHANGES.md`。', '']
    path = Path(output)
    temporary = path.with_suffix('.md.tmp')
    temporary.write_text('\n'.join(lines))
    temporary.replace(path)
