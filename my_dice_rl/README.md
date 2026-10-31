# my_dice_rl：Panda 齿轮装配

硬件：Franka Panda、Franka Hand、SpaceNavigator、腕部 D435i、前方 Femto Bolt。
策略观测为双路 RGB、实际末端位姿与夹爪总开度；动作是目标 XYZ + rotation6d +
夹爪目标总开度，共10维。

遥操作默认使用笛卡尔阻抗控制器及关节 effort 接口，启动见 [阻抗控制说明](README_impedance_control.md)。

按下面顺序阅读和使用：

1. [采集与遥操作](README_collection.md)：启动双相机界面，采集并标记示范。
2. [模型构成](model/README.md)：理解观测编码器、条件 U-Net、归一化和扩散。
3. [数据转换与 DP 训练](README_training.md)：导出数据、检查、训练、续训和离线推理。
4. [DP 真机执行与 RL 轨迹采集](README_execution.md)：人工复位、策略执行、停止与奖励标注。
5. [DICE-RL 训练与部署](README_RL.md)：动作块、混合回放、残差更新、恢复、部署与待确认参数。

```text
cfg/        采集、遥操作、任务与训练配置
env/        真实设备接入
agent/      数据集与预训练循环
model/      DP 模型
util/       录制、旋转、EMA、配置与 checkpoint
script/     采集、数据转换、训练及离线检查入口
tests/      使用隔离 ROS 域与模拟数据的自动检查
ros_ws/     本项目的 ROS 2 工作空间
datasets/   示范及处理后的数据（运行时生成）
log_dir/    训练日志、checkpoint 和验证产物（运行时生成）
```

采集使用系统 Python；离线训练使用项目内 `.venv-dp`。
模拟数据与正式示范分开，训练和检查入口不操作机械臂。
