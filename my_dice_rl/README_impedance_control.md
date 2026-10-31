# Panda 笛卡尔阻抗控制

当前默认控制链：SpaceMouse → `/cartesian_impedance_target` 末端目标 →
`panda_cartesian_impedance_controller` → 七个关节 effort 接口。
遥操作、采集和 DP 执行统一使用这一控制器；夹爪通过独立的 Move/Grasp 动作控制。
原位置控制器和驱动的 position 支持保留，切换说明见 README_position_control.md。

## 启动

先暂停遥操作并关闭采集窗口。在旧驱动终端按 Ctrl+C 退出后，重新 source 并启动。
完整重启会清除旧运动控制器的加载状态，不需要再执行位置控制器的 spawner。

终端 1：

```bash
source /opt/ros/humble/setup.bash
source /home/lhq/dice-rl/my_dice_rl/ros_ws/install/setup.bash
ros2 launch franka_bringup franka.launch.py robot_ip:=172.16.0.2 load_gripper:=true
```

终端 2：

```bash
source /opt/ros/humble/setup.bash
source /home/lhq/dice-rl/my_dice_rl/ros_ws/install/setup.bash
ros2 run controller_manager spawner panda_cartesian_impedance_controller
ros2 control list_controllers
ros2 control list_hardware_interfaces
```

应看到阻抗控制器 active，七个 `panda_jointN/effort` 为 claimed。
驱动不允许 effort 和 position 运动模式同时运行。

终端 3：

```bash
cd /home/lhq/dice-rl/my_dice_rl
bash script/collect.sh
```

左键启用/暂停，右键切换夹爪；数字键 3 只登记已有夹持。采集界面数字键 8 请求
返回保存的初始 TCP 位姿，2 / 0 / Esc 停止复位，具体路径条件见 README_collection.md。
退出采集后停用控制器：

```bash
ros2 control set_controller_state panda_cartesian_impedance_controller inactive
```

## 参数位置

`ros_ws/src/franka_ros2-0.1.0/franka_bringup/config/controllers.yaml`
的 `panda_cartesian_impedance_controller.ros__parameters` 中当前调试参数：

```yaml
stiffness: [150.0, 150.0, 300.0, 30.0, 30.0, 30.0]
damping:   [15.0, 15.0, 22.0,  5.0,  5.0,  5.0]
delta_tau_max: 1.0
```

顺序为 x、y、z、绕 x/y/z 旋转；`delta_tau_max` 是每次控制更新允许的力矩变化量。
修改后重新启动驱动并加载控制器；当前 symlink 安装下 YAML 修改无需重新编译。
向上操作的实测暂停日志为位置误差 25.2 mm、姿态误差 0.3 度，
位置偏差 XYZ 为 [-0.6, -7.2, 24.2] mm。为排查竖直响应，Z 刚度从
150 调为 300 N/m，Z 阻尼从 15 调为 22 N·s/m；其余轴及 25 mm 跟踪保护不变。
这是待真机验证的调试起点，不代表已解决向上跟踪不足或异响。
回退时只需将上述两项第三个数恢复为 150 和 15，再重启驱动。

`cfg/teleop.yaml` 控制 SpaceMouse 速度、死区、外力阈值及夹爪力。
本次保留用户当前设置：平移 0.04 m/s、旋转 0.3 rad/s、死区 0.03、夹爪力 5 N。
修改该文件后重启采集程序即可。若调整速度等参数，应同步检查本机实际值。

`response_exponent` 当前为 1.0，即死区外线性映射。原先的 1.5 会压低小幅输入，
造成轻推时较慢、推大后明显加速。该调整只改变输入曲线，不提高最大速度。
遥操作通过积分生成位姿目标；若实际末端跟不上，放大的跟踪阈值会允许目标继续积累，
松开鼠标后仍可能继续追赶。输入曲线调整不能代替目标积累限制或控制器跟踪诊断。

模式字段 `controller_command_interface: effort` 用于核对控制器实际占用的七个接口。
它不是一个在线切换机器人硬件模式的开关；真正的硬件模式由激活的控制器决定。
数据格式、两路 RGB、实际末端位姿及 10 维策略动作定义保持原样。

## 状态发布器的 update 错误

旧版 `franka_robot_state_broadcaster` 将 realtime publisher 暂时忙、无法取得发布锁的
情况也返回为 ERROR，导致 controller_manager 重复报告 update 错误。
源码已修正为跳过本轮发布并返回 OK；发布器未配置或真正读取状态失败仍返回 ERROR。
这项修复不更改机械臂控制律，也不能据此认定机械异响已经解决。

若仍在运行旧驱动，先暂停遥操作、关闭采集窗口，再在驱动终端按 Ctrl+C。
然后编译并重新按本文的启动步骤运行：

```bash
cd /home/lhq/dice-rl/my_dice_rl/ros_ws
source /opt/ros/humble/setup.bash
source install/setup.bash
colcon build --packages-select franka_robot_state_broadcaster --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release
```

开发时已在独立目录编译并运行 9 项功能测试及格式检查，没有覆盖真机进程正在使用的库。
