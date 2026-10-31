# Panda 齿轮装配：SpaceMouse 双相机示范采集

本模块只负责遥操作和采集。任务从 Franka Hand 已夹持齿轮开始，使用 Panda
笛卡尔阻抗控制器，腕部 D435i 和机械臂前方的 Orbbec Femto Bolt 提供 RGB。

## 先认识代码

沿用原项目的 `cfg / env / util / script` 分工，后续再逐步加入 `model / agent`。

| 文件 | 职责 |
|---|---|
| `cfg/teleop.yaml` | 控制话题、坐标系、鼠标轴映射、速度和保护阈值 |
| `cfg/collect.yaml` | 双相机话题、保存目录、图像尺寸、采样频率 |
| `env/real/spacemouse.py` | 读取 Linux joystick，发布六轴和两个按键 |
| `env/real/teleop.py` | 将六轴输入转为末端目标，处理夹爪动作 |
| `util/reset_motion.py` | 校验初始 TCP 参考，生成限速、限加速度的直线复位轨迹 |
| `script/gui_keys.py` | 数字快捷键，保留备注输入框的正常数字输入 |
| `env/real/ros_backend.py` | 接收图像、TF、关节、外力及控制目标 |
| `util/recording.py` | 软件时间对齐、图像处理、轨迹保存 |
| `script/collect_gui.py` | PyQt 窗口、录制控制、双路回放 |
| `script/cameras.launch.py` | 两台相机的 ROS 驱动配置 |
| `script/collect.sh` | 启动本模块的进程，退出时清理这些进程 |

遥操作数学、采集和界面基于本机 `/home/lhq/rl_franka/ros_ws/src/` 中已有
`franka_spacemouse_teleop` 与 `franka_data_collector` 改编；原文件未修改。
新增项目隔离命名、初始夹持登记、224×224 裁剪、原图保留和跟踪误差保护。

## 1. 先预览界面

不需要 ROS、相机或机械臂，也不会发出任何硬件指令：

```bash
cd /home/lhq/dice-rl/my_dice_rl
bash script/collect.sh --demo
```

模拟数据保存到 `datasets/demonstrations/_demo/`，与真实演示分开。

## 2. 使用 Panda 控制器

启动脚本优先读取当前项目的已编译工作空间：

```text
/home/lhq/dice-rl/my_dice_rl/ros_ws/install/setup.bash
```

当前默认使用笛卡尔阻抗控制器，通过七个 effort 接口输出关节力矩。
控制器已在本机编译安装。以后需要重新编译时：

```bash
cd /home/lhq/dice-rl/my_dice_rl/ros_ws
source /opt/ros/humble/setup.bash
colcon build --packages-select panda_cartesian_impedance_controller --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release
```

采集脚本按配置检查控制器包与 effort 接口。切换与参数说明见
[README_impedance_control.md](README_impedance_control.md)。
若使用 `DICE_ROBOT_SETUP` 指定其他工作空间，该工作空间也必须包含阻抗控制器。

在机器人控制终端先按你已验证的流程启动 Panda、夹爪、机器人状态发布和
`panda_cartesian_impedance_controller`。启动示例：

```bash
source /opt/ros/humble/setup.bash
source /home/lhq/dice-rl/my_dice_rl/ros_ws/install/setup.bash
ros2 launch franka_bringup franka.launch.py robot_ip:=你的机器人IP load_gripper:=true
```

另一个终端检查和激活所需控制器：

```bash
source /opt/ros/humble/setup.bash
source /home/lhq/dice-rl/my_dice_rl/ros_ws/install/setup.bash
ros2 control list_controllers
# 仅当该笛卡尔控制器尚未加载时执行：
ros2 run controller_manager spawner panda_cartesian_impedance_controller
```

新增控制器包后，需要退出旧的驱动进程，并在重新 source 后重启驱动。
只在 spawner 终端 source 无法改变已经运行的 controller_manager 的插件搜索环境。

控制器应为 `active`，并占用 `panda_joint1/effort` 至 `panda_joint7/effort`。TCP 应为 `panda_hand_tcp`，基坐标系为 `panda_link0`。
配置必须与实际控制器一致；不能直接用 `panda_hand` 代替 TCP。
同一机械臂不要同时启用另一个关节运动控制器或目标发布程序。
本节点在检测到其他末端目标发布者时会拒绝启用或暂停。

本采集脚本不启动机器人驱动、不切换控制器，也不自动恢复机器人错误。
数字键 8 可显式请求返回已保存的初始 TCP 位姿；启动脚本本身不会触发运动。
全程使用系统 `/usr/bin/python3`，保持 ROS 的 Python 版本一致。

## 3. 启动真实采集

关闭占用相机的官方查看器及已有遥操作程序。连接两台相机和 SpaceMouse 后运行：

```bash
cd /home/lhq/dice-rl/my_dice_rl
bash script/collect.sh
```

脚本启动相机驱动、SpaceMouse 输入、遥操作节点和窗口；机器人初始保持暂停。
窗口显示设备接收时效、机器人位置、夹爪开度和外力估计。若相机或状态未就绪，
录制按钮不会开放。启动日志位于 `log_dir/collection/run_*/`。

如果相机驱动已按配置中的话题命名运行：

```bash
bash script/collect.sh --no-cameras
```

如果本模块的输入与遥操作进程也已经单独运行，可以再加 `--no-teleop`。
`--no-teleop` 不会自动使用其他项目的遥操作遥测：消息字段和服务必须匹配。

## 4. 按键与每条示范

| 操作 | 功能 |
|---|---|
| SpaceMouse 六轴 | 在机器人基坐标系中控制平移和旋转 |
| SpaceMouse 左键 | 启用 / 暂停机械臂遥操作 |
| SpaceMouse 右键 | 切换夹爪张开 / 闭合抓握；机械臂暂停时也有效 |
| 1 | 界面启用遥操作 |
| 2 / 0 / Esc | 暂停遥操作，或停止正在进行的复位 |
| 3 | 登记当前已夹持状态，不发送夹爪指令 |
| 4 | 开始一条连续演示 |
| 5 / 6 / 7 | 结束并标记成功 / 失败 / 丢弃 |
| 8 | 回到已保存的初始 TCP 位姿，完成后保持暂停 |
| 双击历史条目 | 回放保存的双路图像和夹爪状态 |

按键按住只触发一次；启动或重连时已按下的按键需要先释放。两键同时按下不执行动作。
鼠标回到中位后才能启用机械臂。初始轴映射见 `cfg/teleop.yaml`，首次使用需要
确认六个方向是否符合操作习惯，按需调整 `axis_indices / axis_signs`。
相机在机械臂前方只描述安装位置，不等于已经标定出相机坐标系。

每条采集的推荐操作：

1. 确认齿轮已夹稳，机械臂和轴处在起始状态。
2. 按 3 登记当前夹持。该操作只记录已有夹持模式，不能验证夹持力或是否打滑。
3. 检查两个画面；“查看策略画面”勾选时显示实际保存的 224×224 中心裁剪。
4. 放开 SpaceMouse，按左键或 1 启用遥操作，按 4 开始记录。
5. 对准并插入；完成后 5 标记成功，失败则 6。
6. 先结束录制，再复位机械臂、齿轮和工装；回放检查画面和动作。

2026-10-30 已将现场读取的实际 TCP 位姿记录到 `cfg/rl_review.yaml` 的
`reset_position_m / reset_orientation_xyzw`，作为人工复位参考，坐标系为
`panda_link0 → panda_hand_tcp`。采集界面按 8 会读取此参考并请求复位。
DP/RL 执行界面仍以每轮人工确认时的实际位姿作为本轮运动范围的参考。

复位前先结束录制并等待保存完成，再人工将齿轮退出轴，确认直线返回路径畅通，释放
SpaceMouse 后按 8。复位使用当前阻抗控制器，XYZ 沿直线插值，姿态沿最短旋转插值；
不做避障、不自动拔出齿轮，也不改变夹爪。不是关节姿态复位。
默认复位速度 0.02 m/s、角速度 0.10 rad/s，允许起点距参考最多 0.15 m、0.60 rad。
复位专用跟踪保护为 25 mm / 0.25 rad，实际到达容差为 3 mm / 0.03 rad，超时 30 s。
这些参数在 `cfg/teleop.yaml` 的 `reset_*` 中，与人工遥操作速度和跟踪阈值分开。
复位完成后暂停，按 1 才能继续遥操作。
复位过程中按 2 / 0 / Esc、移动 SpaceMouse 或按其任一按键，都会停止复位；
状态/输入超时、控制器失效、外力/力矩超限或跟踪误差超限也会停止。
录制与复位通过 ROS 服务互锁，复位帧不会作为示范录入。
快捷键在采集窗口获得键盘焦点时生效，不连发；在任务名/备注输入框中，数字用于正常输入，Esc 仍可暂停。

**右键在初始夹持状态下通常会张开夹爪。** 装配过程中需要保持齿轮时不要按右键。
5/6 只结束数据记录；需要暂停运动请按左键或 2。录制期间暂停遥操作会终止该条，
不会把暂停前后的不连续动作拼接在一起。

当前人工遥操作速度、外力/力矩与位姿跟踪阈值以 `cfg/teleop.yaml` 的实际值为准。
这些是第一版工程默认值，需要根据工装和齿轮配合验证，不能当作设备急停功能。
输入超时、状态过旧或超限会暂停遥操作；恢复后需要重新启用。
位姿跟踪超限时，日志和窗口会显示实际位置误差/阈值、姿态误差/阈值及 XYZ 位置偏差。
暂停后目标话题停止连续更新，采集窗口优先显示新鲜遥操作状态中的暂停原因，
避免只提示 target 数据超时。放开鼠标并排除原因后再重新启用。
若底层通信、进程或电源中断，不能保证上层程序还能发送保持指令，仍依赖底层控制器与设备保护。

## 5. 相机分辨率与数据

原 `DICE-RL-Robot/diffusion_policy/config/task/gear_insertion_no_force.yaml`
使用 `[3,224,224]` 策略图像；`utils/computer_vision.py:get_image_transform`
执行等比例缩放后中心裁剪。本项目采用相同处理方式。

| 项目 | 配置 |
|---|---|
| 腕部 D435i RGB 输入 | 640×480，30 fps |
| 前方 Femto Bolt RGB 输入 | 1280×720，30 fps，驱动原生配置 |
| 保存给后续策略的 RGB | 两路均为 224×224，JPEG |
| 原始 RGB | 保留输入尺寸，分别保存，便于检查裁剪与重处理 |
| 轨迹采样 | 10 Hz，可配置；与图像输入帧率分别定义 |

第一版只采集 RGB，不启用深度和 IMU。两台相机通过主机接收时间软件配对，
默认最大到达时间差 60 ms；这不是硬件曝光同步。源时间戳和接收时间均保留。

```text
datasets/demonstrations/
├── gear_insertion/日期_时间_微秒/
│   ├── session.json
│   └── episode_000001/
│       ├── metadata.json
│       ├── timing.jsonl          # 每帧采样、编码、写盘与排队耗时
│       └── trajectory.h5
└── _demo/                         # 只有模拟模式写入这里
```

HDF5 每行记录：实际末端位姿、绝对末端目标、关节位置/速度、外力估计、
实际和目标夹爪开度、夹爪模式、速度/力设定、双路 RGB 和时间戳。
位姿使用 `[x,y,z,qx,qy,qz,qw]`，米与弧度；不能把实际位姿当作目标动作。

夹爪模式 `0=hold_existing`、`1=move`、`2=grasp`。模式 0 的开度是登记时的实测值，
不代表控制器收到过该开度命令；后续策略执行时应保持已有夹持，不能转成一次张开指令。
`gripper_force` 是软件抓握力设定，`external_wrench` 是 Panda 的外力估计，二者都不是独立夹持力传感器读数。
元数据记录配置、图像处理方式、相机原始内参和成功/失败标签。
原始内参对应原图，不能直接用于裁剪后的 224×224 图像。

示范采集采用定时采样与后台写盘两个线程。采样线程按固定 10 Hz 检查时效和相机配对，
复制当前观测后进入有界队列；后台线程执行策略图和原图的 JPEG 编码、HDF5 写入及磁盘空间检查。
`cfg/collect.yaml` 的 `writer_queue_capacity: 20` 最多缓存 20 帧，约对应 2 秒采样量。
当前两路原图尺寸下，队列图像副本占用上限约 74 MB，另有正在处理的帧和原有接收缓存。

短暂写盘延迟不会直接阻塞定时采样；采样本身落后超过一个周期、队列满、状态/图像过旧、
相机无法配对或写盘出错仍会中止。不会跳过时间点、复制旧帧补齐或自动放宽有效性阈值。
结束录制会停止采样并等待队列写完；界面显示“已采 / 已写 / 待写”，保存完成前不能开始下一条或复位。
结束录制仍不等于暂停机械臂，暂停方式见上面的数字键表。

`timing.jsonl` 每行对应一帧，包含 `sampling_delay_ms`、`snapshot_copy_ms`、`queue_wait_ms`、
`encode_ms`、`hdf5_write_ms` 和 `worker_total_ms`；`metadata.json` 中的 `performance`
汇总平均值、最大值、队列观察峰值及已采/已写帧数。`encode_ms` 包含帧数据准备和 JPEG 编码。
`wall_timestamp` 为实际采样阶段的主机时间，不会被后台写盘延迟改变；固定采样时间和各源时间戳继续保留。

完整写完的轨迹保存为 `trajectory.h5`；传感器/采样/队列中止时，其中已保存的连续片段仍标为 `invalid`。
JPEG 编码或写盘线程异常时保留 `trajectory.partial.h5` 和无效标签，避免半行数据被误认为完整轨迹。
这些是本模块的采集文件。现已提供对应的数据转换与双相机10维动作 DP 训练流程，
见 [README_training.md](README_training.md)。应使用本项目配置读取转换后的 Zarr。

本次采集稳定性改动与复核结果见 [COLLECTION_STABILITY_HANDOFF.md](COLLECTION_STABILITY_HANDOFF.md)。

## 6. 本次验证

已实际读取连接的 SpaceNavigator，识别六轴和两个按键；完成模拟录制、保存、
界面预览、回放读取检查，以及隔离 ROS 域的按键、夹爪动作和超时保护测试。
测试不会连接或驱动真实机械臂。位置控制曾通过 GenericSystem 虚拟硬件的位姿跟踪和超时保持验证。
当前切回阻抗控制后，43 项 Python 回归测试通过，并验证了阻抗插件加载、
七个 effort 接口占用及有限力矩输出。真实 Panda 的装配示范仍需现场验证。

本机测试命令：

```bash
cd /home/lhq/dice-rl
source /opt/ros/humble/setup.bash
source /home/lhq/dice-rl/my_dice_rl/ros_ws/install/setup.bash
ROS_DOMAIN_ID=187 my_dice_rl/.venv-dp/bin/python -m pytest -q my_dice_rl/tests
```
