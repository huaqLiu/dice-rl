# 观测同步与消息延迟诊断

## 已确认的问题

2026-11-01 12:21:37 和 12:22:08 两条 DP 轨迹因“图像与机器人状态时间差超限”中止。
生效的接收时间差限制为 80 ms，DP 推理平均约 62 ms、最大约 83 ms。
这两条轨迹最后保存的有效观测最大时间差分别约 33.0 ms 和 62.7 ms；
被拒绝的那一帧原先没有入盘，所以不能仅凭旧 HDF5 认定是哪一路超限。

程序按主机单调时钟的回调接收时间配对，不是硬件曝光同步。
实时控制、100 ms 前的历史观测以及 10 Hz 录制查询都可能触发检查。
任意一次查询超限即停止；本次未扩大阈值、重用未来帧或跳过保护。

## 新增定位能力

- `env/real/policy_observation.py`：`ObservationSyncError` 保留查询时刻、
  各路候选数量、所选消息的接收/源时间戳和年龄；超差提示显示最早/最晚通道和毫秒差。
- `agent/eval/dp_executor.py`：区分 current/history/record 查询，将原始诊断传递给录制器。
- `util/rl_recording.py`：将超限瞬间的信息写入 `metadata.json` 的 `stop_diagnostics`，
  不改变原始成功/失败奖励与保护中止的数据资格。
- `script/diagnose_observation_sync.py`：独立只读探测工具，统计到达间隔、源时间戳间隔、
  源时间戳到回调处理的延迟、回调耗时和三种查询的失败次数。可带双相机 Qt 预览。
  不创建目标发布者，不调用遥操作、夹爪、复位或策略控制服务，也不加载 GPU 模型。

## 只读工具的运行方式

先保持 Franka 驱动、状态发布和现有执行窗口的相机运行，遥操作/策略暂停。
工具只订阅现有话题，**不要另开一套相机驱动**。执行窗口已开时，无需再启动 `cameras.launch.py`。

```bash
cd /home/lhq/dice-rl
source /opt/ros/humble/setup.bash
source my_dice_rl/ros_ws/install/setup.bash
my_dice_rl/.venv-dp/bin/python -m my_dice_rl.script.diagnose_observation_sync \
  --preview --seconds 60 \
  --output "/tmp/dice_observation_sync_$(date +%s).json"
```

输出文件不覆盖已有文件。所有五路观测都收到后，报告中的
`observation_channels_complete` 才为 true；缺少通道时保留报告并返回非零状态。
报告中的 `source_age_at_callback_ms` 与 `accepted_skew_ms` 是不同指标：
前者是源时间戳到回调处理的延迟估计（依赖源时间戳与主机时钟定义），
后者才是当前 80 ms 保护所检查的各路接收时间差。

## 测试与探测范围

新增了超限查询、未来帧排除、结构化错误保存的回归测试；完整测试集 140 项通过，
见 `log_dir/execution/sync_diagnostics_tests.xml` 与同名 `.log`。
ROS 测试在隔离 DDS 域 187 中使用假设备，未驱动真机。

单相机链路和带预览测试分别见 `sync_probe_YkdqZwA1/timing.json`、
`sync_preview_XSIrDcG9/timing.json`（均位于 `log_dir/execution/`）。
当时机械臂驱动停止，只能用于相机间隔分析，不能当作完整同步验证。
前者腕部最大接收间隔约 35 ms，外部约 68 ms；带生产裁剪和预览时两路最大约 71 ms。
外部相机存在约 67 ms 的源时间戳间隔，但这些测量未达到 80 ms。

`sync_full_GXEvkpoN` 与 `sync_attached_uz5cNpHk` 涉及诊断相机与执行窗口相机重叠、
或冲突后设备尚未恢复，不能用于归因此前的中止。额外相机进程已清理。
完整结果以设备恢复后、只订阅已有话题的 `sync_clean_dhWVSXFk/timing.json` 为准。

## 完整链路复现（60 秒，真实设备保持暂停）

完整收到五路观测。实时查询 3000 次，其中 8 次超限；录制查询 600 次，其中 3 次超限；
历史查询 146 次，其中 1 次超限。这些次数可能来自同一次图像空档，不能当作独立故障次数。

| 通道/检查 | 测量结果 |
|---|---|
| 腕部图像最大接收间隔 | 164.5 ms；对应源时间戳间隔最大 166.8 ms |
| 外部图像最大接收间隔 | 103.4 ms；对应源时间戳间隔最大 100.0 ms |
| 机器人状态最大接收间隔 | 20.2 ms |
| 末端位姿读取最大间隔 | 32.9 ms |
| 探测控制循环最大间隔 | 32.8 ms |
| 外部图像触发实例 | 第 7.5 秒，external/eef 相差 92.8 ms |
| 腕部图像触发实例 | 第 24.2 秒起，wrist/eef 相差 96.8～163.4 ms |

因此已经在没有策略推理、没有真实运动的条件下复现了同类中止。
超限时图像消息出现空档，而机器人状态与定时循环继续更新；不能将这次现象归因于 GPU 推理。
源时间戳间隔跳跃只证明接收端缺少这些帧，尚不能单独区分相机发布端与 DDS 传输丢帧。
图像头订阅器的对照产物保存在 `log_dir/execution/sync_comparison/`。

## 已定位的传输丢帧与修复

让原生产订阅器、只读图像头 Best Effort 订阅器和只读图像头 RELIABLE 订阅器同时运行，
按共同源时间戳比较约 32.7 秒的重叠区间：

| 通道 | 原生产 Best Effort 收到 | RELIABLE 收到 | 生产端缺少但 RELIABLE 收到 |
|---|---:|---:|---:|
| wrist | 949 帧 | 982 帧 | 33 帧 |
| external | 949 帧 | 984 帧 | 35 帧 |

RELIABLE 接收的源时间戳间隔最大分别为 33.36 ms、34.00 ms；
Best Effort 生产端对应最大间隔为 366.9 ms、100.0 ms。
因此这些漏掉的帧已经由相机生成并发布，问题发生在传输/订阅链路，
而不是仅仅相机采集端缺帧。纯图像头 Best Effort 接收器同样丢帧，
也不能只归因于本进程的裁剪和 Qt 图像转换。
详见 `sync_comparison/comparison_summary.json`，原始源时间戳用于逐帧核对。

修复位于 `env/real/ros_backend.py`：两路 Image 订阅改为
RELIABLE + KEEP_LAST depth=1 + VOLATILE，适配当前两个支持 RELIABLE 的相机发布者。
仅保留最新待处理图像，已有源时间戳时效检查仍然生效。
机器人状态、夹爪、TF 的订阅不变，相机分辨率、80 ms 同步限制与运动保护数值不变。
此更改同时作用于 DP/RL 执行和示范采集；已经启动的旧窗口需要重启才能使用。

改后真实设备只读验证见 `sync_comparison/fixed_reliable.json`；
回归日志为 `sync_diagnostics_fixed_tests.log` 和同名 XML。

改后 90 秒验证完整收到五路观测：4503 次实时查询、219 次历史查询、901 次录制查询，
同步超限均为 **0 次**，所选快速通道接收时间差最大 **50.18 ms**。
腕部/外部图像接收间隔最大分别为 51.91/47.24 ms，源时间戳间隔最大 33.36/34.01 ms。
改后完整回归 **140 项通过（49.00 秒）**。
测试覆盖真实传感器与 Qt 预览，没有加载策略推理、HDF5 持续录制或发送真机动作，
因此仍需操作员重启旧执行窗口后做短时 DP 闭环试运行。
验证摘要和源码 SHA256 见 `log_dir/execution/sync_diagnostics_verification.json`。
