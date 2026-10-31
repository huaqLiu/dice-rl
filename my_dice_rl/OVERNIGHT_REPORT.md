# 夜间任务查收报告

**两组正式 DP 均已完成，各 100 轮；训练、离线评估及自动对照报告于北京时间 2026-10-31 01:29 完成。**

本报告终验汇总更新时间：2026-10-31T10:39:38+08:00。原定查收时间为 2026-10-31 10:00。

使用 **26 条真实成功示范、2925 帧**，按完整轨迹划分为 21 条训练、5 条验证。9 条失败/无效记录排除，原始数据及标签保留。
两组采用相同网络、数据、划分、随机种子和优化器，区别为视觉骨干随机初始化或 ImageNet 初始化。

## 训练结果

| 视觉初始化 | 完成轮数 | 最优轮次 | 最低验证去噪损失 | 前 4 步平均位置误差 |
|---|---:|---:|---:|---:|
| 随机 | 100 | 7 | 0.067147 | 25.34 mm |
| ImageNet | 100 | 9 | 0.060319 | 17.78 mm |

推荐 **ImageNet 初始化组的 best.pt（第 9 轮 EMA）**，位置模仿误差比随机初始化降低约 30%。
两组后期均出现过拟合，因此使用验证最优权重；`last.pt` 保留第 100 轮的优化器、调度器和随机数状态，供恢复训练。
最优权重来自早期轮次，文件内 `checkpoint_training_complete=false` 表示保存时尚未走完 100 轮；整个运行完成情况由 `last.pt` 及监督器结果确认。

推荐文件：

```text
/home/lhq/dice-rl/my_dice_rl/log_dir/pretrain/gear_dp_pretrained_overnight_20261030/20261031_004638_936849/checkpoint/best.pt
```

15 个验证窗口覆盖 5 条轨迹的开头、中段和结尾。前 4 步统计排除末尾补齐，共 45 个有效目标：
平均位置误差 **17.78 mm**，95 分位 **34.49 mm**；平均姿态误差 **6.11°**。
这些是留出窗口的模仿误差，验证集已用于模型选择，**不能据此认定已具备可靠的齿轮插入能力**。
夹爪误差为 0，源于这些示范始终保持夹持，不能证明学会了新的开闭动作。

4090 使用实际执行预测器对保存观测测试 10 次：平均 **49.65 ms**，95 分位 **51.31 ms**，最大 **51.81 ms**，均小于当前 300 ms 推理时限。
动作数值有限，夹爪目标保持闭合。加载和预热单独耗时约 4.62 秒。此测试没有连接 ROS，不是完整真机控制压力测试。

![训练曲线与验证误差分布](log_dir/overnight_20261030/training_comparison.png)

## 完成的系统优化

- 采集异步写盘、有界队列和耗时诊断，见 [采集稳定性说明](COLLECTION_STABILITY_HANDOFF.md)。
- 训练/执行共用 GPU 资源锁；空闲监督器支持等待、保存断点、让出 GPU 和恢复，未终止其他任务。
- 训练线程限制、定期断点、进度及完成状态；CPU 检查避免意外初始化 CUDA。
- 图像、末端及机器人状态保持 80 ms 同步检查；夹爪慢速状态单独检查 150 ms 年龄。
- 执行端线程限制与实际耗时测试一致；RL 初始元数据落盘移入写盘线程，避免首次控制循环卡顿。
- ROS 正常退出清理、离线范围预检查、多窗口评估、对照模型推荐及可复核报告。

**120 项回归测试通过（44.20 秒）**，测试代码与当前代码哈希一致；模拟执行界面完成开始、停止、成功标注和 20 帧保存。
四个正式 checkpoint 已重新在 CPU 加载，EMA 与优化器状态数值有限，两个最终断点均确认第 100 轮及完成标记。

完整文件清单及作用见 [OVERNIGHT_CHANGES.md](OVERNIGHT_CHANGES.md)。

## 下一步现场核对

1. 当前示范 25/26 条超过部署的 100 mm 起点范围，最大约 117.5 mm。先现场核对空间范围；本次未提高范围、速度、受力或跟踪保护阈值。
2. 末端负载质量、质心及复位相关确认仍待现场核对。无人值守期间没有向真机发送策略运动命令。
3. 验证集中 74/581 帧的 Y 目标超出训练集动作范围，最大约 11.19 mm。继续补采到 50 条有效示范时，应补足起点和插入过程覆盖，再重新训练和验证。
4. 先查看离线结果、核对相机及工装状态，再进行有人看护的策略试机。现场确认完成后，已有执行入口为：

```bash
cd /home/lhq/dice-rl/my_dice_rl
bash script/execute_dp.sh --checkpoint /home/lhq/dice-rl/my_dice_rl/log_dir/pretrain/gear_dp_pretrained_overnight_20261030/20261031_004638_936849/checkpoint/best.pt
```

上述命令未自动执行，窗口仍需人工复位确认和开始。控制器启动流程见 [README_execution.md](README_execution.md)。

## 证据与附件

- [两组自动对照与推荐](MODEL_SELECTION.md)、[基线训练状态](log_dir/overnight_20261030/supervisor_v3/report.md)、[ImageNet 训练状态](log_dir/overnight_20261030/pretrained_supervisor/report.md)。
- [验证误差与保存预测](log_dir/overnight_20261030/pretrained_supervisor/evaluation.json)、[GPU 推理耗时](log_dir/overnight_20261030/runtime_profile.json)。
- [完整测试日志](log_dir/overnight_20261030/full_tests_final.log)、[最终验证摘要](log_dir/overnight_20261030/verification.json)、[权重 SHA-256 与断点信息](log_dir/overnight_20261030/artifact_manifest.json)。
- [数据快照摘要](datasets/processed/gear_insertion_overnight_20261030.zarr/manifest.json)、[数据空间分布](log_dir/overnight_20261030/dataset_coverage.png)、[划分覆盖统计](log_dir/overnight_20261030/split_coverage.json)。
- [成功示范末帧拼图 1](log_dir/overnight_20261030/terminal_views_1.jpg)、[末帧拼图 2](log_dir/overnight_20261030/terminal_views_2.jpg)，供人工复核。
- [训练曲线 PDF](log_dir/overnight_20261030/training_comparison.pdf)、[软硬件版本](log_dir/overnight_20261030/environment.json)、[代码哈希清单](log_dir/overnight_20261030/source_manifest.json)。

额外检查了基线 8/16/50 步采样：前 4 步平均位置误差分别约 26.01/25.34/33.51 mm，保留原 16 步。结果位于 `log_dir/overnight_20261030/sampler_comparison/`，未覆盖原权重或执行配置。
