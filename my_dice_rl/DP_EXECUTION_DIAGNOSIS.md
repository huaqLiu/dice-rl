# DP 真机执行中止：诊断与修复

最新四次实际试运行（16:23–16:24）均因“动作计划过旧”而中止。
第一段动作已经返回并开始执行，下一段没有在旧计划 0.7 秒的有效期内接上，所以机械臂稍微运动后保存为 aborted。
实际记录的计划接收延迟约 260–440 ms；把推理等待上限设置为 1 秒，并不能让旧计划继续有效。

## 已复现的完整链路问题

此前单独预测、推理线程测试及纯模拟控制/录制测试耗时约 60–115 ms，未覆盖真实视频消息与窗口同时运行。
本次只读接入真实 D435i、Femto Bolt、机器人状态和 TF，运行实际窗口、50 Hz 控制调度及 HDF5 录制。
所有动作交给 DemoBackend，不创建真实机械臂目标发布者。

在线程后端中复现了同样的“动作计划过旧”：6 次推理为 215–325 ms。
这说明同一 Python 进程中的实时消息、界面和推理存在显著调度竞争；仅看 GPU 瞬时利用率或单独预测耗时不足以判断。

## 本次改动

- 新增 `agent/eval/process_policy.py`：用 spawn 独立进程加载 DP/RL 策略并执行 GPU 推理；避免 CUDA fork 及与 ROS/Qt 共用 Python GIL。
- `agent/eval/dp_executor.py`：通过原来的异步接口调用新进程，继续保留停止、迟到结果丢弃、资源锁和动作保护；加载替换与退出时关闭推理进程。
- `script/rl_collect_gui.py`：支持后端选择，并在界面显示传入、计算、返回和接收调度的分项耗时。
- `cfg/execute_dp.yaml`、`cfg/execute_rl.yaml`：默认 `inference_backend: process`；`thread` 保留用于对照。
- `tests/test_process_policy.py`：验证独立进程、观测传输、持久模型、异常传播、停止后迟到结果丢弃及资源锁。
- 已接受计划保存 `plans/inference_*_ms`；metadata 中记录 `policy.inference_backend` 和推理子进程 PID，方便确认新版本生效。

## 验证结果与边界

真实传感器、实际界面和录制 + 虚拟机械臂反馈的测试中，连续接收 8 次计划：
平均端到端延迟 **82.99 ms**，最大 **100.05 ms**。
测试采用原 300 ms 推理上限、700 ms 计划有效期，3 秒后主动停止，没有触发计划过期。
虚拟反馈跟随虚拟命令；真实机械臂只被读取状态，未进行闭环运动或装配测试。

**127 项完整回归通过（49.84 秒）**，ROS 测试使用隔离 DDS 域 187，CUDA 测试环境置空。
当前用户配置的 `max_inference_latency_s: 1.0` 保留；本次没有扩大运动范围、速度、受力或跟踪阈值。

## 如何生效

先按 2 / Esc 停止，然后关闭旧执行程序，再启动：

```bash
cd /home/lhq/dice-rl/my_dice_rl
bash script/execute_dp.sh --checkpoint /home/lhq/dice-rl/my_dice_rl/log_dir/pretrain/gear_dp_retrain_20261031_115755/20261031_130741_689656/checkpoint/best.pt
```

权重保持相同，不需要重新训练。模型加载会启动推理子进程；界面加载完毕后才可确认就绪、手动开始。
新界面会显示“推理进程：计算 … ms · 传入 … ms · 返回 … ms · 接收调度 … ms”。
首次继续做短时试运行，确认实际延迟和停止功能。当前离线位置模仿误差仍约 15 mm，推理时延修复不代表已验证装配成功率。

## 证据

- [真实传感器、线程后端复现](log_dir/execution/read_only_policy_probe_thread.json)
- [真实传感器、进程后端验证](log_dir/execution/read_only_policy_probe.json)
- [计划接收延迟统计](log_dir/execution/process_inference_live_verification.json)
- [完整测试日志](log_dir/execution/process_inference_full_tests.log)
- [验证摘要与源码哈希](log_dir/execution/process_inference_verification.json)

## 前一阶段的采样优化

此前已将 DDIM 调度器索引保留在 CPU、网络时间步一次性传入 GPU，减少 CUDA 标量回读。
4 组交替离线对照约 198 ms → 80 ms，相同噪声下动作最大绝对差为 0；这一优化仍保留。
但当时没有复现完整 ROS/窗口链路，不能用该结果断言真机时延已经解决。
[对照数据](log_dir/execution/timesteps_ab_test_20261031_1615.json)。
