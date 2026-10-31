# RL 待确认参数表

代码已生成。已确定的项目记录如下，其余数值尚未统一确认，`cfg/rl_review.yaml` 的 `confirmed` 保持 `false`。
本表自动读取当前文件；重新生成命令：`python -m my_dice_rl.script.report_rl_settings`。

先提供正式 DP checkpoint 路径、其对应的专家 Zarr 路径，以及有效 RL 轨迹目录。
填写成功判据、载荷和复位区域后再确认训练/执行数值。填写载荷表不会自动修改 Desk 的载荷设置。

已确定沿用：Franka Panda/Hand、两路 RGB、10 维 XYZ+rotation6d+夹爪动作、人工复位和人工标注。
已确定成功标准：齿轮插入即可，约 15 mm，人工判定；成功奖励 1、失败奖励 0，不要求额外保持时间，容差不设。
策略 RGB 维持原项目的 224×224；基础编码器及归一化继承所选 DP，RL 不重新拟合。
模拟检查中的小网络和宽松时间预算只写在其独立输出目录，不覆盖下列正式配置。

## 确认记录（含可选项）

`cfg/rl_review.yaml`

| 字段 | 当前值 | 单位/说明 |
|---|---|---|
| `confirmed` | `false` | 用户统一确认开关；目前 false |
| `success_definition` | `"齿轮插入即可，约 15 mm，由人工判定；成功奖励 1，失败奖励 0。"` | 必填：人工成功标注标准 |
| `success_insertion_depth_mm` | `15.0` | mm，人工判断参考，不自动检测 |
| `success_position_tolerance_mm` | `不设（人工判定）` | mm，可选；人工判定可不设 |
| `success_hold_seconds` | `0.0` | s，可选；0 表示不要求额外保持 |
| `payload_mass_kg` | `待填写` | kg |
| `payload_center_of_mass_m` | `待填写` | m，法兰/载荷定义须现场核对 |
| `reset_base_frame` | `"panda_link0"` | 复位参考的基坐标系 |
| `reset_ee_frame` | `"panda_hand_tcp"` | 复位参考的 TCP 坐标系 |
| `reset_position_m` | `[0.5093991413180097, -0.016371339676856965, 0.16452121952955118]` | m，panda_link0 |
| `reset_orientation_xyzw` | `[0.9994101573379833, -0.020505423768044845, 0.027547471797854522, -4.246536057445636e-05]` | 归一化 XYZW |
| `notes` | `"成功判据、奖励和初始 TCP 位姿已确定，其余数值尚待确认。用户计划按 Franka Hand 与 D435i 估算质量，质量数值尚未确定，支架、转接件及夹持齿轮尚未核对；载荷质心待定。此表不修改机器人载荷配置；采集界面可按 8 显式请求直线复位。"` | 确认记录；载荷不自动配置，复位需按键请求 |

## RL 训练与奖励草案

`cfg/finetune/train_dice.yaml`

| 字段 | 当前值 | 单位/说明 |
|---|---|---|
| `teacher_checkpoint` | `待填写` | 必填：正式 DP checkpoint 路径 |
| `data.allow_demo` | `false` | 数量/比例/开关，见配置与 README_RL.md |
| `model.chunk_steps` | `4` | 每个 RL 动作块的策略步数 |
| `model.hidden_dims` | `[256, 256, 256]` | Actor/Critic 各隐藏层宽度 |
| `model.ensemble_size` | `5` | Q 网络数量 |
| `model.target_subset` | `2` | TD 目标使用的随机 Q 子集大小 |
| `model.residual_limit` | `[0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05]` | 归一化动作单位；顺序 XYZ、rotation6d、夹爪 |
| `model.multi_z` | `2` | 每个状态的初始噪声候选数 |
| `model.bc_weight` | `100.0` | 向基础 DP 收缩的损失权重 |
| `model.filter_warmup` | `500` | 从全局训练步零开始计数的过滤热身步数 |
| `model.q_margin` | `0.0` | Q 优势过滤阈值，奖励尺度 |
| `model.self_imitation_weight` | `0.0` | 高 Q 候选自模仿损失权重 |
| `model.normalize_q` | `true` | 数量/比例/开关，见配置与 README_RL.md |
| `model.retain_expert_bc` | `true` | 数量/比例/开关，见配置与 README_RL.md |
| `reward.success` | `1.0` | 成功末端奖励 |
| `reward.failure` | `0.0` | 普通失败末端奖励 |
| `reward.timeout` | `0.0` | 超时末端奖励 |
| `reward.step_penalty` | `0.0` | 每个策略时间步的附加奖励/惩罚，按真实时长缩放 |
| `training.seed` | `42` | 随机种子 |
| `training.torch_threads` | `2` | learner CPU 线程数 |
| `training.batch_size` | `32` | 每次 RL 更新的样本数 |
| `training.encoding_batch_size` | `8` | 冻结图像编码的批大小 |
| `training.actor_lr` | `0.0001` | Actor AdamW 学习率 |
| `training.critic_lr` | `0.0003` | Critic AdamW 学习率 |
| `training.weight_decay` | `1e-06` | AdamW 权重衰减 |
| `training.actor_update_freq` | `2` | 每多少次 Critic 更新进行一次 Actor 更新 |
| `training.critic_warmup` | `1000` | 开始 Actor 更新之前的 Critic 更新步数 |
| `training.grad_clip_norm` | `1.0` | 梯度全局范数上限 |
| `training.target_tau` | `0.005` | Polyak 更新比例 |
| `training.gamma` | `0.99` | 每个策略时间步的折扣 |
| `training.total_updates` | `10000` | Critic 更新总步数 |
| `training.updates_per_round` | `100` | 每轮新增轨迹后的更新步数 |
| `training.min_online_chunks` | `20` | 启动更新所需的最少在线动作块 |
| `training.min_new_episodes` | `1` | 每轮最少新增完整轨迹数 |
| `training.poll_seconds` | `2.0` | s |
| `training.chunk_stride` | `4` | 相邻训练块起点间隔，策略步 |
| `training.checkpoint_every` | `500` | 定期保存间隔，更新步数 |
| `training.replay_capacity` | `10000` | 在线 FIFO 最大动作块数，CPU 存储 |
| `training.expert_capacity` | `10000` | 专家 reservoir 最大动作块数 |
| `training.expert_ratio` | `0.5` | 每批专家数据比例 |
| `training.allow_expert_only` | `false` | 数量/比例/开关，见配置与 README_RL.md |

## RL 真机执行草案

`cfg/execute_rl.yaml`

| 字段 | 当前值 | 单位/说明 |
|---|---|---|
| `checkpoint` | `待填写` | 数量/比例/开关，见配置与 README_RL.md |
| `control_hz` | `50.0` | Hz |
| `max_age_s` | `0.2` | s |
| `max_sensor_skew_s` | `0.08` | s |
| `max_gripper_age_s` | `0.15` | s，执行时夹爪慢速状态通道的最大观测年龄 |
| `control_timeout_s` | `0.15` | s |
| `max_inference_latency_s` | `0.3` | s |
| `max_plan_age_s` | `0.7` | s |
| `max_episode_s` | `30.0` | s |
| `min_free_gb` | `5.0` | GB |
| `handover_timeout_s` | `2.0` | s |
| `max_linear_speed` | `0.025` | m/s |
| `max_angular_speed` | `0.15` | rad/s |
| `max_position_error` | `0.025` | m，XYZ 合成距离 |
| `max_orientation_error` | `0.25` | rad |
| `max_goal_position_error` | `0.04` | m |
| `max_goal_orientation_error` | `0.4` | rad |
| `max_translation_from_reset` | `0.1` | m |
| `max_rotation_from_reset` | `0.6` | rad |
| `reset_drift_m` | `0.005` | m |
| `reset_drift_rad` | `0.05` | rad |
| `max_force` | `20.0` | N |
| `max_torque` | `10.0` | Nm |
| `gripper_speed` | `0.02` | m/s |
| `gripper_force` | `10.0` | N（设定值） |
| `grasp_threshold` | `0.01` | m |
| `gripper_width_change` | `0.005` | m |
| `gripper_min_interval_s` | `0.3` | s |
| `gripper_timeout_s` | `8.0` | s |
| `cancel_timeout_s` | `2.0` | s |

## DP 窗口与数据约定（正式值以所选 DP checkpoint 为准）

`cfg/task/gear_insertion.yaml`

| 字段 | 当前值 | 单位/说明 |
|---|---|---|
| `record_hz` | `10.0` | Hz |
| `hold_target_width` | `0.0` | 数量/比例/开关，见配置与 README_RL.md |
| `obs_steps` | `2` | 数量/比例/开关，见配置与 README_RL.md |
| `horizon_steps` | `20` | 数量/比例/开关，见配置与 README_RL.md |
| `act_steps` | `4` | 数量/比例/开关，见配置与 README_RL.md |
| `sample_stride` | `1` | 数量/比例/开关，见配置与 README_RL.md |
| `val_ratio` | `0.2` | 数量/比例/开关，见配置与 README_RL.md |
| `split_seed` | `42` | 数量/比例/开关，见配置与 README_RL.md |
| `allow_demo` | `false` | 数量/比例/开关，见配置与 README_RL.md |

## 当前人工遥操作设置（参考，不自动复制到 RL）

`cfg/teleop.yaml`

| 字段 | 当前值 | 单位/说明 |
|---|---|---|
| `control_rate` | `50.0` | Hz |
| `max_linear_speed` | `0.08` | m/s |
| `max_angular_speed` | `0.6` | rad/s |
| `deadband` | `0.02` | 数量/比例/开关，见配置与 README_RL.md |
| `response_exponent` | `1.0` | 数量/比例/开关，见配置与 README_RL.md |
| `axis_indices` | `[1, 0, 2, 4, 3, 5]` | 数量/比例/开关，见配置与 README_RL.md |
| `axis_signs` | `[1.0, 1.0, -1.0, 1.0, 1.0, -1.0]` | 数量/比例/开关，见配置与 README_RL.md |
| `enable_button` | `1` | 数量/比例/开关，见配置与 README_RL.md |
| `gripper_button` | `0` | 数量/比例/开关，见配置与 README_RL.md |
| `joy_timeout` | `0.25` | s |
| `gripper_open_width` | `0.08` | m |
| `gripper_closed_width` | `0.0` | m |
| `gripper_speed` | `0.03` | m/s |
| `gripper_force` | `5.0` | N（设定值） |
| `gripper_state_timeout` | `0.5` | s |
| `enable_wrench_stop` | `true` | 数量/比例/开关，见配置与 README_RL.md |
| `robot_state_timeout` | `0.25` | s |
| `max_force` | `20.0` | N |
| `max_torque` | `10.0` | Nm |
| `max_position_error` | `1.0` | m，XYZ 合成距离 |
| `max_orientation_error` | `1.0` | rad |
| `enabled_on_startup` | `false` | 数量/比例/开关，见配置与 README_RL.md |
| `reset_linear_speed` | `0.1` | m/s，直线复位速度上限 |
| `reset_angular_speed` | `0.1` | rad/s，复位角速度上限 |
| `reset_linear_acceleration` | `0.2` | m/s² |
| `reset_angular_acceleration` | `0.3` | rad/s² |
| `reset_max_distance` | `0.4` | m，允许发起复位的最大距离 |
| `reset_max_angle` | `1.0` | rad，允许发起复位的最大角度 |
| `reset_position_tolerance` | `0.01` | m，复位实际到达容差 |
| `reset_orientation_tolerance` | `0.1` | rad，复位实际到达容差 |
| `reset_tracking_position_error` | `0.1` | m，复位专用跟踪保护 |
| `reset_tracking_orientation_error` | `0.8` | rad，复位专用跟踪保护 |
| `reset_timeout` | `30.0` | s，复位总超时 |

## 采集设置

`cfg/collect.yaml`

| 字段 | 当前值 | 单位/说明 |
|---|---|---|
| `record_hz` | `10.0` | Hz |
| `writer_queue_capacity` | `20` | 帧，示范采集待编码/写盘队列上限 |
| `save_width` | `224` | 数量/比例/开关，见配置与 README_RL.md |
| `save_height` | `224` | 数量/比例/开关，见配置与 README_RL.md |
| `save_raw_rgb` | `true` | 数量/比例/开关，见配置与 README_RL.md |
| `jpeg_quality` | `95` | 数量/比例/开关，见配置与 README_RL.md |
| `max_age_s` | `0.25` | s |
| `max_camera_skew_s` | `0.06` | s |
| `min_free_gb` | `5.0` | GB |
| `min_episode_frames` | `16` | 数量/比例/开关，见配置与 README_RL.md |

## 当前阻抗参数（参考，未修改）

`ros_ws/src/franka_ros2-0.1.0/franka_bringup/config/controllers.yaml`

| 字段 | 当前值 | 单位/说明 |
|---|---|---|
| `stiffness` | `[500.0, 500.0, 500.0, 60.0, 60.0, 60.0]` | 前三项 N/m，后三项 Nm/rad |
| `damping` | `[40.0, 40.0, 40.0, 10.0, 10.0, 10.0]` | 前三项 N·s/m，后三项 Nm·s/rad |
| `delta_tau_max` | `1.0` | 每次控制更新的 Nm 增量 |
| `use_current_pose_as_initial_target` | `true` | 数量/比例/开关，见配置与 README_RL.md |

## 确认顺序

1. 已确定：插入约 15 mm、人工标注成功/失败（1/0）；容差可留空，不要求额外保持时间。
2. 核对载荷质量/质心、复位位置与姿态；这些不是自动标定结果。
3. 确认 RL 执行的速度、接触力/力矩、位姿范围和夹爪参数。
4. 确认残差幅度、奖励、折扣、学习率、训练步数和专家混合比例。
5. 同步修改 YAML，最后把 `confirmed` 设为 `true`。正式导出会检查必填项。

当前人工遥操作、RL 执行和机器人硬件保护是不同层的配置，应分别核对。
RL 部署包绑定导出时的执行限制与确认表；后续修改后须重新导出，不能在运动途中换权重。
