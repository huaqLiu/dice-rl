# DP 真机执行与人工标注的 RL 轨迹采集

本模块加载本项目 DP 的 EMA checkpoint，同步读取双相机与机器人状态，
执行10维目标动作，并通过界面完成每轮开始、停止、成功/失败标注及复位确认。

观测时效分别处理：图像、末端与机械臂状态保持 `max_sensor_skew_s=0.08`；
约 10 Hz 更新的夹爪状态使用独立的 `max_gripper_age_s=0.15`，仍要求历史因果性和新鲜状态。
DP 训练和执行共享 `device_lock`。夜间检查与训练结果见 [OVERNIGHT_REPORT.md](OVERNIGHT_REPORT.md)。
策略参数更新由 RL learner 完成。界面新增 1 启用遥操作、8 显式请求回初始位姿；每轮不会自动复位。

## 1. 先预览完整操作流程

```bash
cd /home/lhq/dice-rl/my_dice_rl
bash script/execute_dp.sh --demo
```

模拟模式只使用进程内设备与示例策略，没有 ROS 发布者，不会操作真实机械臂。
可练习：勾选复位确认 → 3 → 4 → Esc → 5/6/7 → 再次复位确认。
选择 checkpoint 时也可用真实 DP 做模拟设备推理；不合适的预测仍会触发动作限制。

## 2. 真机启动

先按现有流程启动 Panda、Franka Hand、机器人状态发布和
`panda_cartesian_impedance_controller`。执行器会读取控制器状态，并检查
`base_link=panda_link0`、`ee_link=panda_hand_tcp`，不自动加载或切换控制器。

```bash
cd /home/lhq/dice-rl/my_dice_rl
bash script/execute_dp.sh --checkpoint /你的正式训练目录/checkpoint/best.pt
```

也可以不带 `--checkpoint`，进入窗口后选择加载。启动时不会执行策略。
请关闭旧采集窗口及旧遥操作程序，避免相机占用和重复目标发布者。
同一机械臂一次运行一个执行界面。

脚本默认启动两台相机、SpaceMouse 输入和支持控制权交接的新遥操作节点。
如果设备节点已按本项目配置启动：

```bash
bash script/execute_dp.sh --no-cameras --no-teleop
```

`--no-teleop` 表示不启动新的人工复位节点；已有遥操作节点必须支持
`/dice_spacemouse_teleop/policy_lock`，否则执行器会拒绝与其共享控制话题。
若完全没有遥操作节点，则执行器检查自己是唯一目标发布者后直接工作。

工作空间使用 `my_dice_rl/ros_ws/install/setup.bash`，启动前检查所配置的 effort 接口支持。
也可以设置 `DICE_ROBOT_SETUP` 指定包含阻抗控制器的工作空间。
驱动重启与控制器加载步骤见 [README_impedance_control.md](README_impedance_control.md)。
模型与窗口使用 `.venv-dp`，相机和遥操作使用系统 Python。

## 3. 每轮操作与控制权

1. **待人工复位**：可用 SpaceMouse 左键启用人工遥操作，右键开闭夹爪，
   或按界面 **1 启用 SpaceMouse 遥操作**，将齿轮、工装和机械臂恢复到本轮初始状态。
   需要返回已保存的位置时，先人工退出轴、确认直线路径畅通，再按 **8 回到初始位姿**。
   8 调用与采集界面相同的复位服务，不改变夹爪；参考位置来自 `cfg/teleop.yaml` 的
   `reset_reference_file`（当前为 `cfg/rl_review.yaml`）。**2 / 0 / Esc** 可暂停遥操作或停止复位。
2. 勾选“齿轮夹稳、工装就位、机械臂处于起始区域”，按 **3 复位就绪确认**。
3. 程序暂停并锁住 SpaceMouse 的机械臂与夹爪控制，核对实时观测和控制器。
   如果人工夹爪动作尚未完成，会拒绝交接。
4. 显示“已就绪”后按 **4 开始执行**。确认后若末端明显移动，需要取消就绪、重新确认。
5. 完成或需要中止时按 **Esc 停止**。程序丢弃未执行动作、取消进行中的夹爪动作，
   并在观测新鲜时短暂发布实测位姿作为保持目标。
6. 显示“等待人工标注”后选择 **5 成功 / 6 失败 / 7 放弃**，可填写备注。
   此时 SpaceMouse 仍被锁住，避免在标注前改变停止场景。
7. 保存完成后交回人工复位，机械臂保持暂停。必须再次勾选并按 3，才能开始下一轮。

开始执行（4）时已经自动录制，不需要另开采集窗口。插入完成后立即按 **2 停止 → 5 成功**，
最后一个 transition 的奖励为 1；普通失败按 6，放弃按 7。按 5 前必须先停止。
策略运行、停止交接、等待标注、保存和 FAULT 状态都不允许通过 1/8 开始人工运动。
复位请求尚未完成或正在复位时不能确认下一轮就绪；点击人工操作会清除旧的复位确认勾选。
暂停请求会立即发送，并在尚未应答的启用/复位请求完成后再次发送，防止迟到响应重新启动运动。

程序只在明确按 8 时请求返回初始位置。成功按人工确认插入约 15 mm 的标准判断。
“复位就绪”是操作员对真实场景的确认，不是算法自动识别齿轮是否夹稳。
本界面只提供软件停止；现场设备急停仍由机器人硬件负责。

## 4. 同步观测与动作转换

观测使用与训练相同的两路224×224中心裁剪、实际末端位姿和实际两指总开度。
历史帧间隔取自 checkpoint 中的采样频率，默认10 Hz；
四元数 XYZW 转为旋转矩阵前两行展开的 rotation6d，归一化取自 checkpoint。

各历史帧只读取该查询时刻以前的数据。相机帧配对后还检查图像与机器人状态的
接收时间差，默认80 ms，数据最大年龄200 ms。源时间戳和接收时间均保存。
这是软件时间对齐，不是两台相机的硬件曝光同步。

GPU 在独立的 spawn 进程推理，ROS、界面和录制保留在主进程，减少 Python GIL 竞争。
`cfg/execute_dp.yaml` 和 `cfg/execute_rl.yaml` 的 `inference_backend: process` 为默认设置；
`thread` 保留给离线性能对照。加载模型时启动推理子进程，不需要重新训练权重。
ROS 控制循环默认50 Hz，不等待推理完成才能响应停止。
预测动作具有观测时刻：根据推理延迟跳过已经过期的动作，默认每4个策略时间步
发起下一次推理，期间可继续使用尚未过期的上一计划；禁止超过预测长度或最大计划年龄。
推理超时、观测丢失、控制循环超时和异常数值都会停止本条。
界面显示传入排队、子进程计算、结果返回和控制接收调度耗时；这些分项也写入
`plans/inference_*_ms`。metadata 中记录推理后端和子进程 PID，便于确认新代码已生效。

动作转换：

- XYZ 是基坐标系中的绝对目标位置。
- rotation6d 经正交化转换为合法四元数；退化旋转直接中止。
- 末端目标按平移速度与角速度限制插值，再发布到 `/cartesian_impedance_target`。
- 目标总开度不超过10 mm时使用 `Grasp` 闭合语义，其余使用 `Move`。
  夹爪指令有变化阈值与最小间隔，进行中的动作不会被重复排队。
- 初始人工已夹持且预测保持闭合时，保留现有夹持，不重新打开或重复抓握。

初始工程参数位于 `cfg/execute_dp.yaml`，包括速度、外力、跟踪误差、
目标与当前末端的距离，以及相对本轮复位位置的范围。
这些默认值需要结合实际装配验证；程序没有自动测得工装碰撞区域。
不同控制器的 TCP 或坐标系未经核对不能直接执行。

如果夹爪取消超时未确认，会进入 FAULT：本条保存为 aborted，不交回遥操作锁，
禁止下一轮。请现场确认设备状态；可以关闭窗口，但关闭窗口不意味着硬件停止已确认。
确认设备已停止后，必要时可通过 ROS 服务释放遗留锁：

```bash
ros2 service call /dice_spacemouse_teleop/policy_lock std_srvs/srv/SetBool '{data: false}'
```

## 5. 奖励与轨迹文件

```text
datasets/rl_rollouts/
├── real/gear_insertion/日期_时间_微秒/
│   ├── metadata.json
│   └── rollout.h5
└── _demo/gear_insertion/...          # 模拟轨迹隔离保存
```

HDF5 包括：

| 分组 | 内容 |
|---|---|
| `observations/` | 同步图像、实际位姿、夹爪、关节、外力、源/接收时间和命令索引 |
| `plans/` | 每次预测的完整动作、初始噪声、历史观测与图像、推理延迟 |
| `commands/` | 每次实际发布的位姿、原始策略目标、夹爪已接受目标/模式、计划编号 |
| `transitions/` | 相邻观测索引、其间命令范围、实际时长、奖励、terminated/truncated |

相邻观测形成真实 `obs -> next_obs`，终止时会尽可能记录最后的新鲜观测。
奖励只在人工标注后写入：成功最后一个 transition 为1，其余为0；普通失败为0。
普通人工成功/失败是 terminated；超时且未成功是 truncated。
放弃、保护触发、数据或推理异常为 aborted/invalid，`eligible_for_rl=false`，
不能混入普通失败。没有至少两帧观测不能形成有效 transition。

如果装配已经成功，但未及时停止而在随后触发保护，原始记录仍保留为 aborted；
人工成功观察可以单独写入旁注，不能直接把整段保护中止数据改成普通成功。
若需用于 RL，应另行确认成功发生时刻、审查并截取保护前的有效片段。

限速、插值、推理延迟和夹爪等待会让实际执行与原始预测有差别，因此两者都保存。
后续 DICE-RL 回放适配需要使用实际命令范围与时间信息，不能把整个预测序列冒充
已经执行的动作。现已提供 RL 原始轨迹读取、动作块适配、混合回放和分轮更新，
见 [README_RL.md](README_RL.md)。`execute_rl.sh` 可在同一界面加载正式 DP 或已导出的 RL 策略。
文件先写为 `rollout.partial.h5`，完成标注后才改名；崩溃留下的 partial 不算完整轨迹。
模拟 DP 训练 checkpoint 会被真实执行模式拒绝。

## 6. 本次验证

- 已实读 D435i 640×480、Femto Bolt 1280×720 RGB，并检查224×224策略裁剪。
- 模拟界面已跑通开始、停止、标注和再次复位确认，预览见 `log_dir/execution/rl_preview.png`。
- 已在隔离 DDS 域使用真实 ROS 消息、模拟控制器/TF/夹爪验证控制权交接和闭环执行。
- 检查了迟到推理丢弃、外力中止、夹爪取消未确认、奖励和终止标记。
- 加载过已有 EMA checkpoint，在4090上完成新的观测封装与推理检查。
- 已完成两组真实示范 DP 训练，推荐权重与实测推理耗时见 [OVERNIGHT_REPORT.md](OVERNIGHT_REPORT.md)；尚未向真实 Panda 下发策略运动。

```bash
cd /home/lhq/dice-rl
source /opt/ros/humble/setup.bash
source /home/lhq/dice-rl/my_dice_rl/ros_ws/install/setup.bash
ROS_DOMAIN_ID=187 my_dice_rl/.venv-dp/bin/python -m pytest -q my_dice_rl/tests
```
