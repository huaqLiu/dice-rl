# 三相机 DP / DICE-RL 使用说明

代码位于原来的 `my_dice_rl`。新入口以 `_three.sh` / `_three.yaml` 命名；双相机配置、既有示范和已训练策略仍可使用。三相机需要重新采集三路同步示范并开始新的 DP/RL 训练，不能直接恢复双相机训练 checkpoint，也不会复制前方图像冒充侧面图像。

## 相机与网络

固定视角顺序为 `wrist → external → side`：

| 视角 | 设备/序列号 | ROS 图像话题 | 原始 RGB |
|---|---|---|---|
| wrist | Intel RealSense D435i | `/wrist/d435i/color/image_raw` | 640×480，30 Hz |
| external | Femto Bolt，`CL83842014T` | `/external/color/image_raw` | 1280×720，30 Hz |
| side | Femto Bolt，`CL83842013K` | `/side/color/image_raw` | 1280×720，30 Hz |

两台奥比中光按 `cfg/cameras_three.yaml` 中的序列号固定绑定。首次请核对预览画面对应前方/侧面，如实际位置相反，交换该文件中的两个序列号后重新采集，不能在同一数据集中随意交换。D435i 可在同文件的 `wrist.serial_number` 中指定序列号，留空使用唯一的 RealSense。

三个视角都沿用原来的缩放填满、居中裁剪为 224×224 RGB 的方式。同步采用因果接收时间配对，约束三个图像中最早和最晚时间的差，不是仅分别对腕部做比较。缺失/过期侧面图像会停止录制/执行，不能静默降为双相机。

| 设置 | 三相机版本 |
|---|---|
| 视觉编码 | 三个独立 CLIP ViT-B/32，OpenAI 预训练；各输出 768 维 |
| 融合 | 与双相机一样拼接视觉特征和机器人状态 |
| 单帧输入特征 | 3×768+9＝2313；两帧历史条件共 4626 维 |
| 机器人状态 | XYZ + rotation6d，共 9 维 |
| 策略动作 | XYZ + rotation6d，共 9 维；保持原有夹持，不输出夹爪动作 |
| DP 去噪网络 | ConditionalUnet1D，通道 `[256,512,1024]`，时间嵌入 256 |
| 窗口/推理 | 2 帧观测，20 步预测，执行前 4 步，50 训练扩散步、16 DDIM 推理步 |
| DP 训练 | batch 8、600 epoch 上限、30 次验证无改善早停，EMA；视觉/其他学习率 1e-5 / 1e-4 |
| RL | 冻结 DP 先验和视觉编码器、残差 Actor、5 个 Critic |
| Actor / Critic | 隐藏层均为 `[1024,1024,1024]`，BC 权重 100；与双相机相同 |
| RL 训练 batch | 32，与当前双相机配置相同；这不是论文中的 256 |

视觉分支数量和首层输入宽度随第三个视角增加，其余网络类型、层宽、动作定义和算法保持一致。原始文件仍记录实际夹爪开度，供检查使用。

## 1. 采集示范

驱动和相应控制器按原流程启动。以下脚本不会启动机器人驱动，也不会代替现场启用控制器。关闭其他占用这三台相机的采集/执行/预览驱动后，选择一种采集方式。

拖拽示教（`panda_drag_controller`）：

```bash
cd /home/lhq/dice-rl/my_dice_rl
bash script/collect_drag_three.sh
```

界面中 3 登记现有夹持，1 启用拖拽，4 开始录制，5 保存成功，6 保存失败，7 丢弃，8 随机复位，9 登记复位原点；2 / 0 / Esc 暂停。复位原点、位置±2 cm 和姿态总偏角≤10°等设置沿用双相机拖拽配置。

SpaceMouse 示教（原来的阻抗遥操作控制流程）：

```bash
cd /home/lhq/dice-rl/my_dice_rl
bash script/collect_three.sh
```

左键启用/暂停、右键切换夹爪；数字快捷键沿用原界面。

拖拽数据位于 `datasets/demonstrations_drag_three/gear_insertion/`；SpaceMouse 数据位于 `datasets/demonstrations_three/gear_insertion/`。每条 `trajectory.h5` 包含 `rgb/side`、可选原始 `rgb_raw/side`、侧面相机时间戳，回放界面也显示三路。

如需只启动三台相机，不启动任何控制程序：

```bash
cd /home/lhq/dice-rl
source /opt/ros/humble/setup.bash
source my_dice_rl/ros_ws/install/setup.bash
ros2 launch "$PWD/my_dice_rl/script/cameras.launch.py" \
  config:="$PWD/my_dice_rl/cfg/cameras_three.yaml"
```

相机已单独运行时，采集/执行脚本使用 `--no-cameras`，避免重复打开设备。可通过 `--camera-config /绝对路径/cameras.yaml` 选择另一份设备绑定。

## 2. 创建训练数据包

下面命令在项目父目录执行。`--input`、`--output` 的相对路径由程序按 `my_dice_rl` 解析。只导出成功示范；失败、损坏、缺失第三路、混合两/三相机的输入不会被当作完整三相机训练数据。

```bash
cd /home/lhq/dice-rl
my_dice_rl/.venv-dp/bin/python -m my_dice_rl.script.process_gear_dataset \
  --input datasets/demonstrations_drag_three/gear_insertion \
  --output datasets/processed/gear_insertion_drag_three_v1.zarr \
  --action-dim 9
```

SpaceMouse 数据则将 `--input` 改为对应目录。每次扩充后选择新的输出文件名，并同步覆盖训练配置的 `task.dataset_path`。不要覆盖已被训练使用的数据包。

## 3. 训练与恢复 DP

```bash
cd /home/lhq/dice-rl/my_dice_rl
bash script/train_dp_three.sh
```

输出：`log_dir/pretrain/gear_dp_clip_three/<时间戳>/`。部署优先选择验证最优 EMA 的 `checkpoint/best.pt`；中断后恢复使用 `checkpoint/last.pt`：

```bash
bash script/train_dp_three.sh \
  --resume /完整三相机DP运行目录/checkpoint/last.pt
```

新示范、新数据包、新视角需要新训练任务，不要恢复双相机 checkpoint。三路 CLIP 的显存和磁盘开销会增加，原 batch 8 已在 4090 上完成少量合成训练更新验证；验证不代表装配成功率。

## 4. 真机测试 DP，并为 RL 采集数据

普通人工复位/标注：

```bash
cd /home/lhq/dice-rl/my_dice_rl
bash script/execute_dp_three.sh \
  --checkpoint /完整三相机DP运行目录/checkpoint/best.pt
```

3 就绪、4 执行、2 停止、5 成功/6 失败；8 复位，1 启用 SpaceMouse。开始前确认驱动、阻抗控制器和两路原有状态通道正常。

如果希望使用原来的自动退轴/复位/成功高度判定和后续 RL 策略切换，使用 RL 执行配置加载同一份 DP：

```bash
bash script/execute_rl_three.sh \
  --checkpoint /完整三相机DP运行目录/checkpoint/best.pt
```

勾选首次复位确认后按 9。沿基坐标 +Z 退轴、当前严格低于 60 mm 的成功规则、每批 10 条、最多 20 条、竖直姿态约束和现有控制保护均沿用当前双相机 RL 配置。确认这些条件仍适用于新场景后再启用自动循环。2 / 0 / Esc 停止。

新在线轨迹保存到 `datasets/rl_rollouts_three/real/gear_insertion/`，与旧双相机轨迹隔离。同一 RL 运行只接受同一个冻结 DP checkpoint 哈希对应的轨迹；不要把不同 DP 的轨迹混放用于同一次训练。

## 5. 启动 RL 监听训练

收集至少 10 条完整、已标注且 `eligible_for_rl=true` 的三相机 DP 执行轨迹后，在另一个终端运行：

```bash
cd /home/lhq/dice-rl/my_dice_rl
bash script/train_rl_three.sh --watch --set \
  teacher_checkpoint=/完整三相机DP运行目录/checkpoint/best.pt
```

专家数据默认是上述三相机 Zarr，如有更名，增加：

```text
expert_dataset=datasets/processed/你的三相机数据包.zarr
```

新 RL 首轮更新 2000 步以跨过原来的 1000 步 Critic 热身，让导出的首个正式策略已有 Actor 更新；以后每新增 10 条更新 1000 步。全局更新上限 100000。训练时执行器会交出同一 GPU 锁，停止后更新，避免控制期间争抢 GPU。

程序自动维护本次运行的目录别名：

```text
log_dir/finetune/gear_dice_three/current/
```

等训练已完成第一轮并生成正式部署包，再加载：

```bash
cd /home/lhq/dice-rl/my_dice_rl
bash script/execute_rl_three.sh \
  --checkpoint "$PWD/log_dir/finetune/gear_dice_three/current/latest_policy.json"
```

若 DP 执行界面仍在运行，不要重复启动，可在复位/停止阶段用“加载 DP / RL 策略”选择同一个 `latest_policy.json`。按 9 的自动循环会在每批后自动读取新版本；手动逐条执行需要手动重新加载索引。输入框会同步显示当前实际加载的权重路径。

恢复监听（不重新开始）：

```bash
bash script/train_rl_three.sh \
  --resume "$PWD/log_dir/finetune/gear_dice_three/current/checkpoint/last.pt" \
  --watch --set training.total_updates=100000
```

这里 `--resume` 自动沿用保存的训练配置及专家/DP 身份，不会使用模板中为空的 `teacher_checkpoint`。

## 6. 修改控制配置后重新导出

导出程序新增 `--execution-config`；它重新封装已有参数，保留部署一致性检查，不修改训练 checkpoint 或绕过保护。

```bash
cd /home/lhq/dice-rl
my_dice_rl/.venv-dp/bin/python -m my_dice_rl.script.export_rl_policy \
  --checkpoint log_dir/finetune/gear_dice_three/current/checkpoint/last.pt \
  --execution-config cfg/execute_rl_three.yaml \
  --output log_dir/finetune/gear_dice_three/current/policy/manual_reexport_v1.pt
```

选择一个不存在的新输出文件名，随后在执行界面加载新文件。三相机 DP、三相机 collector、三相机 RL 和实际数据的视角必须一致。

## 验证与限制

三相机完整离线演练（无 ROS 控制或机器人运动）：

```bash
cd /home/lhq/dice-rl
my_dice_rl/.venv-dp/bin/python -m my_dice_rl.script.check_rl_pipeline \
  --camera-count 3 --output /tmp/three_camera_smoke_new
```

使用完整 CLIP/UNet/1024 网络在空闲 GPU 上做少量合成更新，可增加 `--device cuda:0 --production-model`。输出全部标记 simulated，不能当作真机策略使用。

模型构成查看：

```bash
my_dice_rl/.venv-dp/bin/python -m my_dice_rl.model --camera-count 3
```

本次已完成三路真实相机只读出流检查，以及三相机示范/回放/同步、DP/RL 更新、恢复训练、部署包往返和伪 ROS 控制链路验证。没有向真实机械臂发送运动指令，没有训练出具备真实装配能力的三相机策略。三相机真机成功率需要新示范及现场实验确认。

本次检查时侧面 Femto Bolt 协商为 USB 2.0，但 RGB-only 1280×720 出流成功；长时采集请优先使用 USB 3.x 连接。`/home` 分区仅余约 5 GB，正式采集和保存多份 CLIP 训练 checkpoint 前需要腾出空间或选择容量充足的数据/输出目录；不要降低录制空间保护来掩盖不足。测试输出位于 `/tmp/dice_three_camera_validation_20261103`，项目 `log_dir/three_camera_validation` 是其链接，临时目录可能被系统清理。
