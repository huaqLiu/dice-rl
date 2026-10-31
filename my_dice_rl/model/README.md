# 第一阶段：双相机、10 维动作的 Diffusion Policy 模型

本说明聚焦 `model`；数据集和训练器现已补齐，见上级目录的 `README_training.md`。
建议按下面四个文件依次阅读：

| 文件 | 核心类 | 要理解的问题 |
|---|---|---|
| `vision/multi_image_encoder.py` | `MultiImageEncoder` | 两幅图像和机器人状态如何变成观测特征？ |
| `diffusion/conditional_unet1d.py` | `ConditionalUnet1D` | 带噪动作如何结合观测、扩散步预测噪声？ |
| `common/normalizer.py` | `FeatureNormalizer` | 米、旋转表示和夹爪开度如何缩放到训练范围？ |
| `diffusion/diffusion_policy.py` | `DiffusionPolicy` | 如何组织训练损失和完整的动作采样？ |

## 1. 数据约定

`B` 是 batch size，`To` 是观测历史长度，`H` 是预测动作长度。

| 输入字段 | 形状 | 内容 |
|---|---|---|
| `obs["camera_wrist"]` | `[B, To, 3, 224, 224]` | D435i RGB，浮点数，范围 `[0,1]` |
| `obs["camera_external"]` | `[B, To, 3, 224, 224]` | 前方 Femto Bolt RGB，同上 |
| `obs["robot_eef_pose"]` | `[B, To, 9]` | 实际末端 XYZ + rotation6d |
| `obs["gripper_width"]` | `[B, To, 1]` | 实际两指总开度，单位米，连续值 |
| `action` | `[B, H, 10]` | 目标 XYZ + rotation6d + 夹爪目标总开度 |
| `valid_mask`（可选） | `[B, H]` | 布尔值，标记哪些动作时间步参与训练损失 |

10 维动作的固定顺序：

```text
[x, y, z, r0, r1, r2, r3, r4, r5, gripper_target_width]
 └位置3维┘ └────────旋转6维─────────┘ └────夹爪1维────┘
```

观测中的开度是**实际开度**，动作中的开度是**目标开度**，均为两指间总距离，
不是单根手指的位置，也不是简单的 0/1 开关。

采集 HDF5 的位姿目前是 `[XYZ, qx,qy,qz,qw]` 七维。数据转换会将其旋转
转换为六维表示，统一采用旋转矩阵前两行展开的约定；模型不直接读取 HDF5。
推理得到的 rotation6d 也需要在执行模块中重新构造合法的旋转矩阵/四元数。
训练与执行应沿用同一 `panda_link0 -> panda_hand_tcp` 坐标定义。

## 2. 编码器

```text
腕部 RGB ── 独立 ResNet18 ── 512维 ──┐
前方 RGB ── 独立 ResNet18 ── 512维 ──┤
归一化实际末端位姿 ────────   9维 ──┼─ 每帧拼接：1034维
归一化实际夹爪开度 ────────   1维 ──┘
历史 To 帧按时间展开 ──────────────── 全局条件：To × 1034维
```

两路相机各有一套视觉参数。ResNet18 去掉分类头，BatchNorm 替换为 GroupNorm；
图像输入先使用 ImageNet mean/std 标准化。默认不下载预训练权重，
`pretrained_vision=True` 时才请求 torchvision 的 ImageNet 权重。

这是便于学习的 ResNet18 版本；原真机仓库 `train_dp_workspace.yaml` 默认使用
ViT/CLIP 编码器与更复杂的融合模块，因此本版本不是原默认视觉架构的逐项复现。
本版保留“观测编码器 + 条件一维 U-Net + 扩散调度器”的划分。

## 3. 条件一维 U-Net

网络沿**动作序列时间轴**做卷积，不在这里处理图像。

```text
带噪动作 [B,H,10] ─ 下采样残差块 ─ 中间块 ─ 上采样残差块 ─ 预测噪声 [B,H,10]
                         ↑                         ↑
              扩散步的时间嵌入 + 全局观测特征，通过 FiLM 调节各层
```

跳跃连接保留不同时间尺度的动作特征。默认通道 `[256,512,1024]`，
因此 `H` 必须是 4 的倍数；16 或 20 都可用。
U-Net 中的扩散步 `t` 表示噪声程度，不是机械臂运行时间。

## 4. DP 总模型的两个主要接口

`compute_loss(batch)` 用于训练：

1. 将目标动作归一化。
2. 随机采样扩散步和高斯噪声，为真实动作加噪。
3. U-Net 根据观测、扩散步、带噪动作预测噪声。
4. 计算预测噪声与所加噪声之间的 MSE。

`predict_action(obs)` 用于推理：

1. 编码观测历史。
2. 从随机动作噪声开始，使用 DDIM 逐步去噪。
3. 将结果反归一化为物理单位，夹爪总开度限定在 0–0.08 m。
4. 返回完整 `action_pred`，以及当前计划执行的前 `act_steps` 个 `action`。

默认 `To=2, H=20, act_steps=4`。这些是可调整的模型窗口，尚未确定最终控制频率。
我们的采集是 10 Hz，后续数据集应按此设置窗口：观测截止当前时刻 t，
标签从 t 的目标开始。执行后更新观测，再预测新序列；执行器是否需要更短窗口应实测确定。

归一化必须先通过 `fit_normalizer(training_states, training_actions)` 拟合，
其中 states 是实际位姿9维+实际开度1维。统计量只能来自训练集，保存在 `state_dict` 中。
示范中恒定的动作分量在反归一化时保持该常量；若希望学习开闭，示范必须包含对应变化。
后续 checkpoint 还需保存构造配置、坐标和旋转约定，而不只是网络权重。

数据转换保留 `hold_existing / move / grasp` 模式及原始登记开度。
其中 `hold_existing` 通过显式配置映射为闭合训练标签，详见 `README_training.md`，
没有把登记夹持的实测开度视作已接受的夹爪指令。
后续执行器也要明确如何将预测开度转换为 Franka Hand 的 move/grasp。

## 5. 模型检查

单独的训练环境使用 `model/requirements.txt`。不需要 ROS，也不连接机械臂。
例如在项目父目录建立与采集隔离的 Python 环境：

```bash
cd /home/lhq/dice-rl
python3 -m venv --without-pip --system-site-packages my_dice_rl/.venv-dp
my_dice_rl/.venv-dp/bin/python -m pip install --no-user -r my_dice_rl/model/requirements.txt
my_dice_rl/.venv-dp/bin/python -m pytest -q my_dice_rl/tests/test_dp_model.py
```

查看结构摘要或所有网络层：

```bash
my_dice_rl/.venv-dp/bin/python -m my_dice_rl.model
my_dice_rl/.venv-dp/bin/python -m my_dice_rl.model --full
```

用 4090 检查默认模型的 224×224 输入、损失反向传播与动作采样：

```bash
my_dice_rl/.venv-dp/bin/python -m my_dice_rl.model --smoke --device cuda
```

检查涵盖两路视觉与夹爪输入的梯度、10维动作采样、归一化保存恢复、
恒定动作分量以及序列长度约束。随机输入只用于验证计算流程，不代表模型已经学会装配。

参考源码：`dice_rl/model/diffusion/conditional_unet1d.py`、
`DICE-RL-Robot/diffusion_policy/policy/`、`DICE-RL-Robot/diffusion_policy/model/vision/`。
外部接口说明：[DDIMScheduler](https://huggingface.co/docs/diffusers/api/schedulers/ddim)、
[torchvision ResNet18](https://docs.pytorch.org/vision/0.20/models/generated/torchvision.models.resnet18.html)。
