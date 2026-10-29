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

## 2. 使用现有 Panda 控制器

本机已存在兼容的旧版 Panda 工作空间，启动脚本默认读取：

```text
/home/lhq/rl_franka/ros_ws/install/setup.bash
```

这里仅复用已安装的 ROS 消息类型、机器人驱动和笛卡尔阻抗控制器。
本模块的遥操作、采集配置、数据和代码全部位于 `my_dice_rl`。
若使用其他工作空间，在启动采集前设置：

```bash
export DICE_ROBOT_SETUP=/你的工作空间/install/setup.bash
```

在机器人控制终端先按你已验证的流程启动 Panda、夹爪、机器人状态发布和
`panda_cartesian_impedance_controller`。本机旧工作空间提供的入口示例：

```bash
source /opt/ros/humble/setup.bash
source /home/lhq/rl_franka/ros_ws/install/setup.bash
ros2 launch franka_bringup franka.launch.py robot_ip:=你的机器人IP load_gripper:=true
```

另一个终端检查和激活所需控制器：

```bash
source /opt/ros/humble/setup.bash
source /home/lhq/rl_franka/ros_ws/install/setup.bash
ros2 control list_controllers
# 仅当该笛卡尔控制器尚未加载时执行：
ros2 run controller_manager spawner panda_cartesian_impedance_controller
```

控制器应为 `active`，TCP 应为 `panda_hand_tcp`，基坐标系为 `panda_link0`。
配置必须与实际控制器一致；不能直接用 `panda_hand` 代替 TCP。
同一机械臂不要同时启用另一个关节运动控制器或目标发布程序。
本节点在检测到其他末端目标发布者时会拒绝启用或暂停。

本采集脚本不启动机器人驱动、不切换控制器、不自动复位，也不自动恢复机器人错误。
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
| F2 / F3 | 界面启用 / 暂停遥操作 |
| F4 | 登记当前已夹持状态，不发送夹爪指令 |
| F5 | 开始一条连续演示 |
| F6 / F7 / F8 | 结束并标记成功 / 失败 / 丢弃 |
| 双击历史条目 | 回放保存的双路图像和夹爪状态 |

按键按住只触发一次；启动或重连时已按下的按键需要先释放。两键同时按下不执行动作。
鼠标回到中位后才能启用机械臂。初始轴映射见 `cfg/teleop.yaml`，首次使用需要
确认六个方向是否符合操作习惯，按需调整 `axis_indices / axis_signs`。
相机在机械臂前方只描述安装位置，不等于已经标定出相机坐标系。

每条采集的推荐操作：

1. 确认齿轮已夹稳，机械臂和轴处在起始状态。
2. 按 F4 登记当前夹持。该操作只记录已有夹持模式，不能验证夹持力或是否打滑。
3. 检查两个画面；“查看策略画面”勾选时显示实际保存的 224×224 中心裁剪。
4. 放开 SpaceMouse，按左键或 F2 启用遥操作，按 F5 开始记录。
5. 对准并插入；完成后 F6 标记成功，失败则 F7。
6. 先结束录制，再复位机械臂、齿轮和工装；回放检查画面和动作。

**右键在初始夹持状态下通常会张开夹爪。** 装配过程中需要保持齿轮时不要按右键。
F6/F7 只结束数据记录；需要暂停运动请按左键或 F3。录制期间暂停遥操作会终止该条，
不会把暂停前后的不连续动作拼接在一起。

默认最大平移速度 0.025 m/s、角速度 0.15 rad/s；外力 20 N、力矩 3 Nm、
位置跟踪误差 25 mm、旋转跟踪误差 0.25 rad 为软件阈值，可在配置中调整。
这些是第一版工程默认值，需要根据工装和齿轮配合验证，不能当作设备急停功能。
输入超时、状态过旧或超限会暂停遥操作；恢复后需要重新启用。
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

中断文件保留为 `trajectory.partial.h5`；正常完成才改名为 `trajectory.h5`。
超时或写盘落后会标为 `invalid`，避免默默补齐缺失数据。
这些是本模块的采集文件，**尚不能直接套用原项目 Zarr 数据集配置训练**；
后续按你的学习进度单独实现数据转换与双相机 DP 配置。

## 6. 本次验证

已实际读取连接的 SpaceNavigator，识别六轴和两个按键；完成模拟录制、保存、
界面预览、回放读取检查，以及隔离 ROS 域的按键、夹爪动作和超时保护测试。
测试不会连接或驱动真实机械臂。开发时 USB 列表中没有 D435i 和 Femto Bolt，
因此双相机实拍和真实 Panda 遥操作整条示范尚待接设备验证。

本机测试命令：

```bash
cd /home/lhq/dice-rl
source /opt/ros/humble/setup.bash
source /home/lhq/rl_franka/ros_ws/install/setup.bash
ROS_DOMAIN_ID=187 /usr/bin/python3 -m pytest -q my_dice_rl/tests
```
