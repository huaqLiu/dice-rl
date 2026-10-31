# 本次 RL 完成任务文件清单

新增 **25** 个代码/配置/文档文件，修改 **7** 个已有文件，共 **32** 个。

统计基于本次任务开始前的源码快照。此前已有的 `agent/dataset/rl_dataset.py` 没有改动，不计作本次新增。

| 类型 | 文件 | 大致作用 |
|---|---|---|
| 修改 | [README.md](README.md) | 总入口增加 RL 训练与部署说明链接。 |
| 新增 | [README_RL.md](README_RL.md) | 完整操作顺序、算法适配、数据语义、恢复、导出、部署与限制。 |
| 修改 | [README_execution.md](README_execution.md) | 把已有执行/标注流程与新的 RL 训练、部署入口关联。 |
| 新增 | [RL_IMPLEMENTATION_FILES.md](RL_IMPLEMENTATION_FILES.md) | 本次新增/修改文件及作用清单，区别于之前已有的文件。 |
| 新增 | [RL_PENDING_VALUES.md](RL_PENDING_VALUES.md) | 自动汇总当前数值、开关和确认记录，供统一确认。 |
| 新增 | [agent/dataset/rl_chunks.py](agent/dataset/rl_chunks.py) | 把专家与在线转移组织为不跨轨迹的动作块，保留时长、mask 和 SMDP 回报。 |
| 修改 | [agent/eval/dp_executor.py](agent/eval/dp_executor.py) | 在已有状态机中接入 DP/RL 加载及 GPU 锁；保持人工复位和停止保护。 |
| 修改 | [agent/eval/dp_policy.py](agent/eval/dp_policy.py) | 抽取部署配置/预热公共逻辑，并记录基础 DP 文件哈希。 |
| 新增 | [agent/eval/rl_policy.py](agent/eval/rl_policy.py) | 加载自包含 RL 策略或最新策略索引，检查来源、确认表、坐标和控制限制。 |
| 新增 | [agent/finetune/__init__.py](agent/finetune/__init__.py) | 使 RL 微调模块成为可导入的 Python 包。 |
| 新增 | [agent/finetune/train_dice_agent.py](agent/finetune/train_dice_agent.py) | learner 主循环：特征提取、混合回放、TD/Actor 更新、分轮监视、保存与恢复。 |
| 新增 | [cfg/execute_rl.yaml](cfg/execute_rl.yaml) | RL 执行草案：控制限制、设备锁和统一确认表路径。 |
| 新增 | [cfg/finetune/train_dice.yaml](cfg/finetune/train_dice.yaml) | RL 模型、奖励、优化器、采样、训练预算和输出路径草案。 |
| 新增 | [cfg/rl_review.yaml](cfg/rl_review.yaml) | 待填写的成功判据、载荷、复位定义和统一确认状态，目前 confirmed=false。 |
| 修改 | [model/diffusion/diffusion_policy.py](model/diffusion/diffusion_policy.py) | 增加从冻结特征采样的接口，复用 DP，原有训练/推理接口保持兼容。 |
| 新增 | [model/rl/__init__.py](model/rl/__init__.py) | 使 RL 模型模块成为可导入的 Python 包。 |
| 新增 | [model/rl/distill_rl.py](model/rl/distill_rl.py) | 有界零初始化残差 Actor、Critic 集成、多噪声收缩正则与目标网络。 |
| 新增 | [script/check_rl_pipeline.py](script/check_rl_pipeline.py) | 纯离线的 DP→RL→导出→模拟执行→继续采集/恢复演练。 |
| 修改 | [script/execute_dp.sh](script/execute_dp.sh) | 支持选定执行配置，并在设备进程启动前检查 RL 数值确认状态。 |
| 新增 | [script/execute_rl.sh](script/execute_rl.sh) | RL 真机/模拟执行入口，复用已存在的图形界面与设备启动流程。 |
| 新增 | [script/export_rl_policy.py](script/export_rl_policy.py) | 把训练 checkpoint 导出为不可覆盖的自包含部署包。 |
| 新增 | [script/report_rl_settings.py](script/report_rl_settings.py) | 从当前 YAML 自动生成待确认参数表，可输出 JSON。 |
| 修改 | [script/rl_collect_gui.py](script/rl_collect_gui.py) | 界面支持选择 DP、RL 部署包和 latest_policy.json，仍仅在复位阶段加载。 |
| 新增 | [script/train_rl.py](script/train_rl.py) | 离线/监视模式 RL 训练及恢复命令行入口。 |
| 新增 | [script/train_rl.sh](script/train_rl.sh) | 使用项目 .venv-dp 启动 RL learner。 |
| 新增 | [tests/test_rl_core.py](tests/test_rl_core.py) | 检查梯度、冻结、目标回报、轨迹边界、回放恢复和进程锁。 |
| 新增 | [tests/test_rl_executor.py](tests/test_rl_executor.py) | 验证训练锁阻止执行冲突、运动途中拒绝换权重、迟到推理结束后释放锁。 |
| 新增 | [tests/test_rl_training.py](tests/test_rl_training.py) | 小模型实际训练/恢复/导出/模拟执行，以及部署包和参数一致性检查。 |
| 新增 | [util/replay_buffer.py](util/replay_buffer.py) | CPU 专家 reservoir 与在线 FIFO 混合回放，支持状态保存和确定性采样恢复。 |
| 新增 | [util/rl_checkpoint.py](util/rl_checkpoint.py) | RL checkpoint 与部署格式、冻结 DP 快照、自包含策略和严格导出校验。 |
| 新增 | [util/rl_config.py](util/rl_config.py) | 解析/校验 RL 配置，统一路径、哈希、执行限制和必填确认项。 |
| 新增 | [util/rl_coordination.py](util/rl_coordination.py) | 用 Linux flock 协调同机训练与推理，进程退出自动释放锁。 |

## 另外生成的验证记录

| 文件 | 作用 |
|---|---|
| `log_dir/rl_completion/cpu_report.json` | CPU 完整小模型演练结果和产物路径。 |
| `log_dir/rl_completion/cuda_report.json` | 4090 串行演练结果，记录约 602 MiB 的小模型显存峰值。 |
| `log_dir/rl_completion/verification.json` | 回归与设备验证摘要，包括首次 CUDA 计时保护中止记录。 |
| `log_dir/rl_completion/files.json` | 本清单的机器可读版本及源文件 SHA256。 |

测试训练产生的小模型、模拟轨迹和 checkpoint 保存在各报告指向的独立临时目录；全部不能作为正式真机策略使用。
本次没有修改 ROS 驱动、阻抗控制器参数、原始采集数据或已经训练的正式模型。

## 后续：采集界面数字快捷键与返回初始 TCP 位姿

此项单独记录，不计入上面的原 RL 完成任务数量。

| 文件 | 大致作用 |
|---|---|
| `util/reset_motion.py`（新增） | 读取复位参考及坐标系，生成平滑且受速度/加速度限制的直线路径。 |
| `script/gui_keys.py`（新增） | 数字快捷键不连发，文本编辑时数字正常输入。 |
| `env/real/teleop.py` | 复位服务、录制互锁、暂停及保护停止，完成后保持暂停。 |
| `env/real/ros_backend.py` | 采集端调用复位和录制互锁服务。 |
| `script/collect_gui.py` | 采集数字键、复位按钮和录制互锁状态。 |
| `script/rl_collect_gui.py` | 原策略执行界面改用数字快捷键，保留 Esc 停止。 |
| `util/recording.py` | 拒绝录入复位状态。 |
| `cfg/collect.yaml`、`cfg/teleop.yaml`、`cfg/rl_review.yaml` | 服务地址、复位专用限制及已保存的初始 TCP 位姿。 |
| `tests/test_reset_motion.py`、`tests/test_collection_gui_keys.py`（新增） | 轨迹边界、真实 Qt 按键行为和界面互锁测试。 |
| `tests/test_teleop.py` | 复位、停止、状态过期及跟踪保护的隔离 ROS 测试。 |
| `README_collection.md`、`README_execution.md`、`README_impedance_control.md`、`README_RL.md` | 更新操作顺序、数字键和复位适用条件。 |
| `script/report_rl_settings.py`、`RL_PENDING_VALUES.md` | 同步当前复位参数及参考坐标系。 |

全套 91 项测试通过；未执行真机复位，直线路径需现场确认畅通。
