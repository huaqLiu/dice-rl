"""从当前文件生成待确认参数表，不改配置、不访问设备。"""
import argparse
import json
from pathlib import Path

import yaml

from my_dice_rl.util.config import PROJECT_ROOT

UNITS = dict(gamma='每个策略时间步的折扣', target_tau='Polyak 更新比例',
    confirmed='用户统一确认开关；目前 false', teacher_checkpoint='必填：正式 DP checkpoint 路径',
    success_definition='必填：人工成功标注标准', notes='确认记录；载荷不自动配置，复位需按键请求',
    chunk_steps='每个 RL 动作块的策略步数', hidden_dims='Actor/Critic 各隐藏层宽度',
    ensemble_size='Q 网络数量', target_subset='TD 目标使用的随机 Q 子集大小',
    multi_z='每个状态的初始噪声候选数', bc_weight='向基础 DP 收缩的损失权重',
    filter_warmup='从全局训练步零开始计数的过滤热身步数',
    critic_warmup='开始 Actor 更新之前的 Critic 更新步数', q_margin='Q 优势过滤阈值，奖励尺度',
    self_imitation_weight='高 Q 候选自模仿损失权重',
    actor_lr='Actor AdamW 学习率', critic_lr='Critic AdamW 学习率',
    weight_decay='AdamW 权重衰减', grad_clip_norm='梯度全局范数上限',
    actor_update_freq='每多少次 Critic 更新进行一次 Actor 更新',
    batch_size='每次 RL 更新的样本数', encoding_batch_size='冻结图像编码的批大小',
    writer_queue_capacity='帧，示范采集待编码/写盘队列上限',
    max_gripper_age_s='s，执行时夹爪慢速状态通道的最大观测年龄',
    total_updates='Critic 更新总步数', updates_per_round='每轮新增轨迹后的更新步数',
    min_online_chunks='启动更新所需的最少在线动作块', min_new_episodes='每轮最少新增完整轨迹数',
    chunk_stride='相邻训练块起点间隔，策略步', checkpoint_every='定期保存间隔，更新步数',
    replay_capacity='在线 FIFO 最大动作块数，CPU 存储', expert_capacity='专家 reservoir 最大动作块数',
    expert_ratio='每批专家数据比例', seed='随机种子', torch_threads='learner CPU 线程数',
    success='成功末端奖励', failure='普通失败末端奖励', timeout='超时末端奖励',
    step_penalty='每个策略时间步的附加奖励/惩罚，按真实时长缩放',
    residual_limit='归一化动作单位；顺序 XYZ、rotation6d、夹爪',
    max_linear_speed='m/s', max_angular_speed='rad/s', max_force='N', max_torque='Nm',
    max_position_error='m，XYZ 合成距离', max_orientation_error='rad',
    max_goal_position_error='m', max_goal_orientation_error='rad',
    max_translation_from_reset='m', max_rotation_from_reset='rad', reset_drift_m='m', reset_drift_rad='rad',
    gripper_force='N（设定值）', gripper_speed='m/s', gripper_width_change='m', grasp_threshold='m',
    gripper_open_width='m', gripper_closed_width='m', control_hz='Hz', control_rate='Hz', record_hz='Hz',
    stiffness='前三项 N/m，后三项 Nm/rad', damping='前三项 N·s/m，后三项 Nm·s/rad',
    delta_tau_max='每次控制更新的 Nm 增量', success_insertion_depth_mm='mm，人工判断参考，不自动检测',
    success_position_tolerance_mm='mm，可选；人工判定可不设',
    success_hold_seconds='s，可选；0 表示不要求额外保持', payload_mass_kg='kg',
    payload_center_of_mass_m='m，法兰/载荷定义须现场核对', reset_position_m='m，panda_link0',
    reset_orientation_xyzw='归一化 XYZW', min_free_gb='GB',
    reset_base_frame='复位参考的基坐标系', reset_ee_frame='复位参考的 TCP 坐标系',
    reset_linear_speed='m/s，直线复位速度上限', reset_angular_speed='rad/s，复位角速度上限',
    reset_linear_acceleration='m/s²', reset_angular_acceleration='rad/s²',
    reset_max_distance='m，允许发起复位的最大距离', reset_max_angle='rad，允许发起复位的最大角度',
    reset_position_tolerance='m，复位实际到达容差', reset_orientation_tolerance='rad，复位实际到达容差',
    reset_tracking_position_error='m，复位专用跟踪保护', reset_tracking_orientation_error='rad，复位专用跟踪保护',
    reset_timeout='s，复位总超时')


def flatten(value, prefix='', include_strings=False):
    for key, item in value.items():
        name = prefix + key
        if isinstance(item, dict):
            yield from flatten(item, name + '.', include_strings=include_strings)
        elif item is None or isinstance(item, (int, float, bool, list)) or (include_strings and isinstance(item, str)):
            if not isinstance(item, list) or all(isinstance(x, (int, float, bool)) for x in item):
                yield name, item


def render_settings(output):
    sections = [
        ('确认记录（含可选项）', 'cfg/rl_review.yaml', None),
        ('RL 训练与奖励草案', 'cfg/finetune/train_dice.yaml', None),
        ('RL 真机执行草案', 'cfg/execute_rl.yaml', None),
        ('DP 窗口与数据约定（正式值以所选 DP checkpoint 为准）', 'cfg/task/gear_insertion.yaml', None),
        ('当前人工遥操作设置（参考，不自动复制到 RL）', 'cfg/teleop.yaml', 'dice_spacemouse_teleop.ros__parameters'),
        ('采集设置', 'cfg/collect.yaml', None),
        ('当前阻抗参数（参考，未修改）', 'ros_ws/src/franka_ros2-0.1.0/franka_bringup/config/controllers.yaml',
         'panda_cartesian_impedance_controller.ros__parameters')]
    lines = ['# RL 待确认参数表', '',
        '代码已生成。已确定的项目记录如下，其余数值尚未统一确认，`cfg/rl_review.yaml` 的 `confirmed` 保持 `false`。',
        '本表自动读取当前文件；重新生成命令：`python -m my_dice_rl.script.report_rl_settings`。', '',
        '先提供正式 DP checkpoint 路径、其对应的专家 Zarr 路径，以及有效 RL 轨迹目录。',
        '填写成功判据、载荷和复位区域后再确认训练/执行数值。填写载荷表不会自动修改 Desk 的载荷设置。', '',
        '已确定沿用：Franka Panda/Hand、两路 RGB、10 维 XYZ+rotation6d+夹爪动作、人工复位和人工标注。',
        '已确定成功标准：齿轮插入即可，约 15 mm，人工判定；成功奖励 1、失败奖励 0，不要求额外保持时间，容差不设。',
        '策略 RGB 维持原项目的 224×224；基础编码器及归一化继承所选 DP，RL 不重新拟合。',
        '模拟检查中的小网络和宽松时间预算只写在其独立输出目录，不覆盖下列正式配置。', '']
    records = []
    for title, name, selector in sections:
        data = yaml.safe_load((PROJECT_ROOT / name).read_text())
        if selector:
            for key in selector.split('.'):
                data = data[key]
        lines.extend(['## ' + title, '', '`' + name + '`', '', '| 字段 | 当前值 | 单位/说明 |', '|---|---|---|'])
        for key, value in flatten(data, include_strings=(name == 'cfg/rl_review.yaml')):
            leaf = key.rsplit('.', 1)[-1]
            unit = UNITS.get(leaf, 's' if leaf.endswith(('_seconds', '_s', '_timeout')) else '数量/比例/开关，见配置与 README_RL.md')
            shown = '待填写' if value is None else json.dumps(value, ensure_ascii=False)
            if key == 'success_position_tolerance_mm' and value is None:
                shown = '不设（人工判定）'
            lines.append(f'| `{key}` | `{shown}` | {unit} |')
            records.append(dict(file=name, key=key, value=value, description=unit))
        lines.append('')
    lines.extend(['## 确认顺序', '',
        '1. 已确定：插入约 15 mm、人工标注成功/失败（1/0）；容差可留空，不要求额外保持时间。',
        '2. 核对载荷质量/质心、复位位置与姿态；这些不是自动标定结果。',
        '3. 确认 RL 执行的速度、接触力/力矩、位姿范围和夹爪参数。',
        '4. 确认残差幅度、奖励、折扣、学习率、训练步数和专家混合比例。',
        '5. 同步修改 YAML，最后把 `confirmed` 设为 `true`。正式导出会检查必填项。', '',
        '当前人工遥操作、RL 执行和机器人硬件保护是不同层的配置，应分别核对。',
        'RL 部署包绑定导出时的执行限制与确认表；后续修改后须重新导出，不能在运动途中换权重。', ''])
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('\n'.join(lines), encoding='utf-8')
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(PROJECT_ROOT / 'RL_PENDING_VALUES.md'))
    parser.add_argument('--json')
    args = parser.parse_args()
    records = render_settings(args.output)
    if args.json:
        Path(args.json).write_text(json.dumps(records, ensure_ascii=False, indent=2))
    print(f'{args.output}: {len(records)} configuration entries')


if __name__ == '__main__':
    main()
