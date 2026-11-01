# 策略试运行：成功记录与人工复位

按 4 开始执行策略时已经自动录制，不需要另开采集窗口。

1. 人工确认齿轮插入完成后，立即按 **2 停止**。
2. 等待显示“等待人工标注”，按 **5 成功**，可填写约 15 mm 插入、成功依据等备注。
3. 等待保存结束并返回“待人工复位”。该成功轨迹最后一步奖励为 1，普通失败按 6，放弃按 7。
4. 按 **1 启用 SpaceMouse 遥操作**；先退出轴、恢复工装或夹持状态。
5. 释放 SpaceMouse 至中位，确认直线路径畅通后，按 **8 回到初始位姿**。
6. 复位完成后重新勾选就绪确认，按 **3** 确认、**4** 开始下一轮。

**2 / 0 / Esc** 同时用于停止策略、暂停遥操作或停止复位。SpaceMouse 原来的左键启用/暂停、右键开闭夹爪保留。

1 和 8 仅在待人工复位、无策略控制权锁且无待完成控制请求时开放；策略执行、等待标注、保存及 FAULT 期间不能用它们移动机械臂。
正在复位时，下一轮就绪确认被禁用。关闭窗口会请求暂停；迟到的启用/复位应答后会再次发送暂停，防止停止意图被旧请求覆盖。

8 复用采集界面的已有复位服务，不改变夹爪。参考文件由 `cfg/teleop.yaml` 的 `reset_reference_file` 指定，目前是 `cfg/rl_review.yaml`。
3 仅确认当前实际起始位姿，不覆盖该固定复位参考。复位不会自动避障。

## 本次人工反馈

2026-10-31 17:04:34 那条轨迹已记录 168 帧、41 次计划，但之后因“预测目标离实际末端过远”自动中止，原始状态是 aborted。
操作员反馈已经装配成功，已单独保存为 [人工成功观察](datasets/rl_rollouts/real/gear_insertion/20261031_170434_362929/operator_success_note.json)。
原始 metadata、HDF5 标签和 RL 资格保留。若要将这条用于 RL，需要另行确认成功时刻并审查保护前的有效片段，不能直接把整段保护中止数据改为普通成功。

正式轨迹目录：`datasets/rl_rollouts/real/gear_insertion/`，每条包含 metadata.json 和 rollout.h5。

## 生效与验证

停止并关闭旧执行窗口，使用原 `execute_dp.sh --checkpoint .../best.pt` 命令重启；不需要重新训练。

138 项回归通过（51.47 秒）；隔离 DDS 域 187 的假设备服务验证了遥操作启用、复位和暂停，未触发真实机械臂运动。
模拟界面也完成开始、停止、成功标注和保存。见 [完整测试日志](log_dir/execution/manual_controls_full_tests.log)、
[验证摘要](log_dir/execution/manual_controls_verification.json) 和 [界面预览](log_dir/execution/manual_controls_preview.png)。

主要修改：`script/rl_collect_gui.py` 增加 1/8 按钮与提示；`agent/eval/dp_executor.py` 增加控制状态互锁、暂停重放及完成检查；
`env/real/ros_backend.py` 返回服务 Future，`policy_demo.py` 提供模拟手动控制；增加 GUI/异步互锁和隔离 ROS 服务测试。
