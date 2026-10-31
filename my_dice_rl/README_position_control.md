# Panda 遥操作位置控制

> 当前默认已切回阻抗控制，见 [README_impedance_control.md](README_impedance_control.md)。
> 本文保留位置控制实现与启动说明；使用前需同步切换下面三份配置。

切换到位置控制时，将 `cfg/teleop.yaml` 的 `controller_name` 设为
`panda_cartesian_position_controller`，`controller_command_interface` 设为 `position`，
`target_topic` 设为 `/cartesian_position_target`；同步修改 `cfg/collect.yaml` 的
`target_topic`，以及 `cfg/execute_dp.yaml` 的控制器名称和 command interface。
重启应用后，接口检查将按 position 模式执行。

控制链是：SpaceMouse 六轴 → 末端绝对位姿 PoseStamped → 运动学逆解 →
七个关节 position 接口 → libfranka `JointPositions` → Panda。
外部控制器不计算刚度、阻尼力矩或重力补偿力矩。

Franka FCI 的位置运动生成器仍通过机器人内部伺服跟踪目标。
本机 libfranka 0.9.1 的该接口需要指定 `ControllerMode::kJointImpedance`；
这是机器人内部的位置跟踪方式，不是原先运行在电脑上的笛卡尔阻抗力矩控制器。
此修改不能取消 Panda 固件内部的伺服控制。
位置目标不会主动按外力退让；保留上层外力与跟踪误差暂停保护。

## 编译与重启

本次开发已在当前设备编译。以后修改 C++ 后重新编译：

```bash
cd /home/lhq/dice-rl/my_dice_rl/ros_ws
source /opt/ros/humble/setup.bash
colcon build --packages-select franka_hardware franka_description franka_bringup panda_cartesian_position_controller --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release
```

先暂停遥操作，退出采集窗口和旧的机器人驱动 launch 进程，再启动新版驱动。
硬件接口在驱动启动时加载，仅重新 source 或重新加载控制器不够。

终端 1：

```bash
source /opt/ros/humble/setup.bash
source /home/lhq/dice-rl/my_dice_rl/ros_ws/install/setup.bash
ros2 launch franka_bringup franka.launch.py robot_ip:=你的机器人IP load_gripper:=true
```

终端 2：

```bash
source /opt/ros/humble/setup.bash
source /home/lhq/dice-rl/my_dice_rl/ros_ws/install/setup.bash
ros2 run controller_manager spawner panda_cartesian_position_controller
ros2 control list_controllers
ros2 control list_hardware_interfaces
```

应看到 `panda_cartesian_position_controller` 为 `active`，
`panda_joint1/position` 至 `panda_joint7/position` 为 `[claimed]`。
旧阻抗控制器配置保留，但上述启动流程不会加载它；同一时刻只运行一个运动控制器。
驱动拒绝同时使用 effort 与 position 模式。

终端 3：

```bash
cd /home/lhq/dice-rl/my_dice_rl
bash script/collect.sh
```

左键启用/暂停，右键切换夹爪。机械臂初始暂停；放开鼠标后再启用。
暂停遥操作保持当前位置；若还要停用底层控制器，在退出采集窗口后执行：

```bash
ros2 control set_controller_state panda_cartesian_position_controller inactive
```

## 代码与参数

| 位置 | 修改内容 |
|---|---|
| `ros_ws/src/panda_cartesian_position_controller/` | KDL 正运动学、阻尼最小二乘逆解、关节位置积分与限速 |
| `ros_ws/src/franka_ros2-0.1.0/franka_hardware/` | position 命令接口、FCI JointPositions 和模式互斥 |
| `ros_ws/src/franka_ros2-0.1.0/franka_description/robots/panda_arm.ros2_control.xacro` | 声明 position 接口；设置虚拟硬件初始关节位姿 |
| `ros_ws/src/franka_ros2-0.1.0/franka_bringup/config/controllers.yaml` | 新控制器类型、TCP、逆解和运动限制 |
| `env/real/teleop.py`、`cfg/teleop.yaml` | 发布位置目标，启用前及运行中检查 position 控制器 |
| `cfg/collect.yaml`、`cfg/execute_dp.yaml` | 采集与 DP 执行统一使用位置目标与位置控制器 |
| `script/check_position_setup.py` | 检查选中的 ROS 工作空间是否包含新驱动及接口声明 |

目标话题是 `/cartesian_position_target`，基坐标 `panda_link0`，TCP `panda_hand_tcp`。
上层默认 50 Hz，ros2_control 默认 1000 Hz。平移限速 0.025 m/s，旋转限速
0.15 rad/s；关节限速 0.3 rad/s，加速度上限 0.5 rad/s²，运动中的 jerk 限值
5 rad/s³。libfranka 原生滤波与速度、加速度、jerk 限制保持开启，也用于平滑保持/暂停时的指令变化。
目标超过 0.25 s 未更新就保持最后的关节目标；重新激活后旧目标不生效。
关节软限位、非有限数值和过大的跟踪误差也会阻止继续运动。

采集仍保存两路 RGB、实际末端位姿、夹爪实际开度与绝对目标，10 维 DP 动作定义不变。
由于执行控制器改变，已有阻抗控制示范的闭环响应可能不同，需检查后再混合训练。

## 已完成的验证

驱动及控制器编译、硬件接口 Mock 测试、逆解积分测试和 ROS 遥操作/采集回归测试。
在隔离 ROS 域使用 `fake_components/GenericSystem` 验证控制器加载、七个 position
接口占用、毫米级末端运动和目标超时保持。日志在 `log_dir/position_fake_check.log`。
没有连接或驱动真实 Panda，真机跟踪效果仍需现场验证。
