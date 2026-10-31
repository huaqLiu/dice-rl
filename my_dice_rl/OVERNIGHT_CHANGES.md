# 2026-10-30 夜间系统优化

任务目标：在现有额度与资源条件内，提高采集、DP 训练和部署衔接的可靠性；GPU 空闲时完成首版 DP 训练，明早 10:00 查收。

## 已完成结果

两组真实数据训练均完成 100 轮，各 29,292 次有效更新，训练与自动评估/对照报告在 2026-10-31 01:29（北京时间）完成。推荐 ImageNet 初始化第 9 轮的最佳 EMA：验证损失 0.060319，前 4 步平均位置误差 17.78 mm（随机初始化 25.34 mm），4090 平均推理 49.65 ms。真机装配成功率尚待现场验证。完整权重位置及附件见 [OVERNIGHT_REPORT.md](OVERNIGHT_REPORT.md)。

## 数据与训练计划

- 独立数据快照：`datasets/processed/gear_insertion_overnight_20261030.zarr`。
- 26 条人工标记成功的真实示范，2925 帧；9 条失败/无效记录排除。全部成功记录通过完整性、时间戳、有限值和图像解码校验。
- 数据 ID：`5a3e2c17b5265b8351ef3938a9a6fe62ad9a17a26b6d39c71474220fdee3f4d1`。
- 沿用原 DP：双独立 ResNet18、条件 1D U-Net，224×224 双 RGB、2 帧观测、20 步动作预测、每次执行 4 步、10 维动作。
- 沿用 100 epochs、batch 8、AdamW 1e-4、AMP、EMA、20% 按完整轨迹划分验证集、seed 42；基线使用随机初始化视觉权重。
- 夜间训练快照使用 2 个 DataLoader worker、主进程 2 个 Torch CPU 线程；worker 中 Torch/OpenCV 各 1 线程，避免每个 worker 创建大型线程池。
- 基线训练启动后，额外排队一组仅改变视觉初始化的对照（`train_dp_pretrained.yaml`）。使用 Torchvision 0.20 的 ResNet18 ImageNet1K V1 权重，缓存下载已校验；不改变模型结构、数据、划分、学习率或轮数。两组 GPU 训练串行运行。
- 每 500 次成功更新及每轮结束保存 checkpoint；首版权重不等于真机成功率已经验证。

训练快照：`log_dir/overnight_20261030/train_dp.yaml`。旧数据与既有模型未覆盖，原始成功/失败标签未修改。

## 已完成的优化

| 部分 | 改动 |
|---|---|
| 采集 | 已完成异步编码/写盘、有界队列与耗时诊断，见 COLLECTION_STABILITY_HANDOFF.md。 |
| 资源互斥 | DP 训练、DP 执行、RL learner 与 RL 执行共用 `log_dir/rl/device.lock`。 |
| GPU 空闲等待 | 无计算进程、桌面图形利用率不超过 60%、显存占用不超过 6144 MiB，连续观察 60 秒后启动；每 15 秒检查。已实测计算任务退出后仍有约 30–40% 的 Code/Chrome 图形占用；利用率阈值和显存阈值允许桌面图形占用，4090 仍至少留出约 18 GiB。 |
| 竞争任务进入 | 只向自己的训练子进程发送 SIGUSR1，完成当前更新后保存断点、退出；GPU 再次空闲后恢复，不终止其他任务。项目锁暂忙返回可重试状态。 |
| 断点与状态 | checkpoint 增加训练完成标记；提供进度 JSON。中断在更新边界处理，不把尚未完成的优化器更新冒充为完成。 |
| 推理线程 | 真机执行界面与离线耗时检查统一使用 Torch 2 线程、OpenCV 1 线程，为控制循环留出 CPU 容量。 |
| CPU 检查 | CPU RNG 快照不再为了记录状态而初始化 CUDA，减少 CPU 检查意外占用 GPU。 |
| RL 轨迹启动 | 初始元数据的原子写入及 fsync 移至写盘线程，消除忙磁盘导致的首次控制循环卡顿；慢磁盘测试验证构造调用不等待落盘。 |
| 观测同步 | 图像、末端和机械臂状态仍保持 80 ms 同步限制；约 10 Hz 的夹爪状态单独使用 150 ms 年龄检查，同时保持因果性和总观测时效限制。 |
| 离线预检查 | 比较示范位移、姿态和目标距离与部署限制，只报告不自动改硬件控制参数。 |
| 训练后评估 | 自动在 CPU 上评估最多 15 个验证集窗口，覆盖不同轨迹开头/中段/结尾；报告位置、姿态、夹爪误差和预测数据，不视为闭环成功率。 |
| 查收报告 | 后台自动更新 report.md/status.json，保存 GPU 观察历史与训练日志。 |
| 正常退出 | ROS context 已关闭后的 spin 唤醒异常安静退出；context 仍有效时的运行错误继续抛出，保留真正故障。 |

## 实测发现及待确认项

旧的全通道 80 ms 检查会让 2925 帧中的 362 帧超限；排除夹爪慢速更新造成的时间差后，高速图像/机械臂通道仅有 12 帧超限。这 12 帧仍应被部署同步保护拦截。夹爪年龄最大约 144.2 ms，95 分位约 92.1 ms。

25/26 条成功示范超出当前部署的 100 mm 起点范围，最大目标距离约 **117.5 mm**。
这不是训练数据损坏，但意味着正式试机前需现场确认允许范围；没有自动提高范围、速度、力/力矩或位姿跟踪阈值。末端质量、质心等先前待确认项也仍待现场核对。

验证集中 74/581 帧的 Y 目标超出训练集动作范围，最大约 11.19 mm；当前动作采样被限制在训练集归一化范围内，首版策略在这些帧存在表达范围限制。保持验证集独立，不将验证数据用于归一化或调整原始标签。空间分布见 `dataset_coverage.png`，统计见 `split_coverage.json`。

报告：`synchronization_audit.json`、`deployment_preflight.json`，位于 `log_dir/overnight_20261030/`。

## 验证

- 最新完整回归 **120 项通过，44.20 秒**：`full_tests_final.log / full_tests_final.xml`；此前 109 项完整回归也通过。
- 真实 CPU CLI 子进程通过 SIGUSR1 完成安全暂停，保存有效断点；小模型恢复测试验证下一次更新及 EMA 与不中断运行逐位一致。
- 监督器测试覆盖连续空闲检测、只暂停自己的子进程、恢复断点、项目资源锁暂忙重试和截止后不再启动。
- 夹爪慢速通道测试覆盖正常 100 ms 间隔、超过 150 ms 拒绝、以及高速相机/机械臂同步检查继续生效。
- 两组正式最佳 EMA 均完成 15 个验证窗口评估；推荐模型通过实际执行预测器的 10 次空闲 GPU 耗时检查。早期模拟小模型冒烟结果单独保存，不混作正式训练结果。
- 21 条训练轨迹、5 条验证轨迹；64 个样本的数据读取基准中，预热后 batch 读取平均约 40.3 ms，见 `data_loading_benchmark.json`。这是当前系统负载下的 CPU 观测值，不是训练耗时保证。
- 修改后的执行界面通过无 ROS 模拟冒烟检查：开始、停止、成功标注和保存 20 帧，见 `execution_gui_smoke.log`；使用独立测试资源锁，不绕过正式执行的 GPU 互斥。
- ROS 退出测试验证正常 context 关闭后的清理，以及 context 仍有效时的错误继续暴露。
- 状态机用例改为等待首个有效计划及异步写盘至少两帧后再标注，与实际界面启用条件一致；避免固定睡眠或提前标注造成偶发失败。没有放宽真机超时参数来通过测试。

测试命令将 `CUDA_VISIBLE_DEVICES` 置空，避免占用正在被其他任务使用的 GPU；ROS 测试使用隔离域 187。没有发送真机运动指令。

## 文件与作用

新增：

- `util/gpu_idle.py`：只读 NVIDIA 状态和连续空闲判断，无 Torch/CUDA 初始化。
- `util/overnight_report.py`：自动生成查收报告，无 GPU 计算。
- `script/train_dp_when_idle.py`：等待、启动、暂停、恢复、截止控制及训练后 CPU 评估。
- `script/evaluate_dp.py`：多验证窗口 EMA 策略评估，输出 JSON/NPZ。
- `script/preflight_deployment.py`：示范与执行范围的离线对照。
- `script/profile_dp_runtime.py`：使用真实执行预测器、保存的观测和共享资源锁测量推理耗时，不连接 ROS。
- `script/compare_dp_runs.py`：等待对照任务，按同一数据快照的验证损失生成推荐权重报告，不自动部署。
- `tests/test_idle_training.py`、`tests/test_offline_reports.py`、`tests/test_training_cli_signals.py`：新功能测试。
- `tests/test_ros_shutdown.py`：ROS 正常退出与真正运行故障的区分测试。
- `tests/test_rl_recording_async.py`：慢磁盘下的 RL 元数据异步写入验证。
- `tests/test_compare_runs.py`：只比较完成且数据一致的对照训练。
- 本说明和 `OVERNIGHT_REPORT.md`：静态改动说明与实时结果入口。

修改：

- `agent/pretrain/train_dp_agent.py`：线程限制、定期断点、暂停、训练完成标记、进度状态。
- `script/train_dp.py`：资源锁、信号处理和结果 JSON。
- `util/rl_recording.py`：初始元数据转到后台写盘，保留失败处理与完整结束语义。
- `util/checkpoint.py`、`util/config.py`：CPU RNG 行为和资源锁路径处理。
- `env/real/policy_observation.py`、`agent/eval/dp_executor.py`：夹爪独立时效检查，包含历史和停止阶段观测。
- `cfg/pretrain/train_dp.yaml`、`cfg/execute_dp.yaml`、`cfg/execute_rl.yaml`：线程、断点、资源锁及夹爪时效配置。
- `script/rl_collect_gui.py`、`util/rl_config.py`：新增配置校验及 RL 导出限制绑定。
- `script/report_rl_settings.py`、`RL_PENDING_VALUES.md`：同步参数表。
- `tests/test_dp_execution.py`：新增慢速夹爪时效与高速通道保护测试；与 `tests/test_dp_execution_ros.py` 一并使用独立测试资源锁，避免干扰正式训练。
- `env/real/teleop.py`、`env/real/spacemouse.py`、`env/real/ros_backend.py`、`script/collect_gui.py`：正常关闭时的 ROS spin 清理，保留运行中错误。

## 使用与检查

实时进度见 [OVERNIGHT_REPORT.md](OVERNIGHT_REPORT.md)。成功完成训练后，用实际 `best.pt` 路径执行已有的离线检查与真机执行入口；先完成现场范围和负载核对，再人工确认开始。

对照结果见 [MODEL_SELECTION.md](MODEL_SELECTION.md)，它按验证去噪损失推荐模型，同时保留前 4 步的位置误差供判断；验证集已用于选择模型，不代表独立真机测试。全 20 步和名义执行前 4 步误差分别输出。
参考：[Torchvision ResNet18 权重说明](https://docs.pytorch.org/vision/0.20/models/generated/torchvision.models.resnet18.html)。

若等待到截止时间仍未出现连续空闲，报告会明确注明训练未完成；若训练中断，保留的 `last.pt` 可继续恢复。等待任务和原有驱动/控制器进程互相独立。
