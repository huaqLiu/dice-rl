# 双相机 CLIP ViT-B/32、9维状态/动作的 Diffusion Policy

本说明聚焦 `model`；数据集和训练器位于 `agent/dataset` 和 `agent/pretrain`。
建议按下面四个文件依次阅读：

| 文件 | 核心类 | 要理解的问题 |
|---|---|---|
| `vision/multi_image_encoder.py` | `MultiImageEncoder` | 两幅图像和机器人状态如何变成观测特征？ |
| `diffusion/conditional_unet1d.py` | `ConditionalUnet1D` | 带噪动作如何结合观测、扩散步预测噪声？ |
| `common/normalizer.py` | `FeatureNormalizer` | 位置和旋转表示如何缩放到训练范围？ |
| `diffusion/diffusion_policy.py` | `DiffusionPolicy` | 如何组织训练损失和完整的动作采样？ |

## 1. 数据约定

`B` 是 batch size，`To` 是观测历史长度，`H` 是预测动作长度。

| 输入字段 | 形状 | 内容 |
|---|---|---|
| `obs["camera_wrist"]` | `[B, To, 3, 224, 224]` | D435i RGB，浮点数，范围 `[0,1]` |
| `obs["camera_external"]` | `[B, To, 3, 224, 224]` | 前方 Femto Bolt RGB，同上 |
| `obs["robot_eef_pose"]` | `[B, To, 9]` | 实际末端 XYZ + rotation6d |
| `action` | `[B, H, 9]` | 目标 XYZ + rotation6d，不包含夹爪 |
| `valid_mask`（可选） | `[B, H]` | 布尔值，标记哪些动作时间步参与训练损失 |

9 维动作的固定顺序：

```text
[x, y, z, r0, r1, r2, r3, r4, r5]
 └位置3维┘ └────────旋转6维─────────┘
```

新模型的机器人状态只有9维末端位姿，夹爪实际开度不进入观测网络。
采集文件和数据集仍保留夹爪实际开度、夹持模式等诊断字段；它们不进入新模型的状态或动作。
9维策略执行时不发送夹爪Move/Grasp指令，保持进入执行前的夹持。

采集 HDF5 的位姿目前是 `[XYZ, qx,qy,qz,qw]` 七维。数据转换会将其旋转
转换为六维表示，统一采用旋转矩阵前两行展开的约定；模型不直接读取 HDF5。
推理得到的 rotation6d 也需要在执行模块中重新构造合法的旋转矩阵/四元数。
训练与执行应沿用同一 `panda_link0 -> panda_hand_tcp` 坐标定义。

## 2. 编码器

```text
腕部 RGB ── CLIP ViT-B/32 ── CLS 768维 ──┐
前方 RGB ── CLIP ViT-B/32 ── CLS 768维 ──┤
归一化实际末端位姿 ─────────────── 9维 ──┘ 每帧拼接：1545维
历史 To 帧按时间展开 ─────────────────── 全局条件：To × 1545维；To=2时3090维
```

新训练默认配置为 `cfg/pretrain/train_dp_clip.yaml`，使用原项目同款
`timm` 的 `vit_base_patch32_clip_224.openai`，加载OpenAI CLIP预训练权重。
两路相机使用独立参数，均从相同预训练权重初始化。取最终LayerNorm后的768维CLS，
不使用512维图文投影头，不需要文本输入。图像按CLIP权重配套的mean/std标准化。

视觉网络继续微调，初始学习率1e-5，策略U-Net初始学习率1e-4；两组一起使用warmup和cosine调度。
训练时使用原项目的0.95随机裁剪比例和ColorJitter（brightness .3、contrast .4、saturation .5、hue .08），
验证和执行时关闭增强。`frozen_vision: true` 可冻结视觉编码器，当前默认false以对应原项目。

ResNet18实现保留给旧checkpoint和小规模离线测试；其旧配置为 `cfg/pretrain/train_dp.yaml`。
这里对齐的是论文真机视觉backbone和CLS特征选择；多相机融合仍采用当前工程的拼接，
不是原仓库完整的模态注意力融合模块。

## 3. 条件一维 U-Net

网络沿**动作序列时间轴**做卷积，不在这里处理图像。

```text
带噪动作 [B,H,9] ─ 下采样残差块 ─ 中间块 ─ 上采样残差块 ─ 预测噪声 [B,H,9]
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
3. 将9维位姿结果反归一化为物理单位。
4. 返回完整 `action_pred`，以及当前计划执行的前 `act_steps` 个 `action`。

默认 `To=2, H=20, act_steps=4`。这些是可调整的模型窗口，尚未确定最终控制频率。
我们的采集是 10 Hz，后续数据集应按此设置窗口：观测截止当前时刻 t，
标签从 t 的目标开始。执行后更新观测，再预测新序列；执行器是否需要更短窗口应实测确定。

归一化必须先通过 `fit_normalizer(training_states, training_actions)` 拟合，
其中 states 和 actions 都是位姿9维。统计量只能来自训练集，保存在 `state_dict` 中。
示范中恒定的动作分量在反归一化时保持该常量；若希望学习开闭，示范必须包含对应变化。
后续 checkpoint 还需保存构造配置、坐标和旋转约定，而不只是网络权重。

数据转换保留 `hold_existing / move / grasp` 和夹爪原始信息，仅用于检查。
新转换默认写入9维动作；已有10维Zarr可以直接读取，训练时仅使用前9维，不改写旧文件。

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

检查涵盖两路视觉与位姿输入的梯度、夹爪观测不影响预测、9维动作采样、归一化保存恢复、
恒定动作分量以及序列长度约束。随机输入只用于验证计算流程，不代表模型已经学会装配。

新默认 `cfg/pretrain/train_dp_clip.yaml` 的 `model.state_dim` 和 `model.action_dim` 均为9。
旧DP/RL部署包会根据保存的归一化参数还原状态/动作维度；旧10维动作仍按旧模型执行。
旧模型不会通过截断输出伪装成新模型。
新9维动作模型需从头训练，不能直接恢复之前10维动作的checkpoint；新DP训练好后，RL也需要以新DP重新建立网络和回放。
已有示教不需要重采，新训练请用独立输出目录，例如 `log_dir/pretrain/gear_dp_drag_pose9`。

参考源码：`dice_rl/model/diffusion/conditional_unet1d.py`、
`DICE-RL-Robot/diffusion_policy/policy/`、`DICE-RL-Robot/diffusion_policy/model/vision/`。
外部接口说明：[DDIMScheduler](https://huggingface.co/docs/diffusers/api/schedulers/ddim)、
[torchvision ResNet18](https://docs.pytorch.org/vision/0.20/models/generated/torchvision.models.resnet18.html)。

## CLIP 新训练

权重已在本机缓存，依赖为 `timm==1.0.19`。新建训练时加载CLIP预训练权重；
恢复训练及加载DP/RL部署包时 `pretrained_vision=False`，直接使用包内权重，不下载权重。
新checkpoint保存 `vision_encoder`、增强/冻结设置和9维动作，旧包缺少此字段时按ResNet18读取。

```bash
cd /home/lhq/dice-rl/my_dice_rl
bash script/train_dp.sh --config cfg/pretrain/train_dp_clip.yaml
```

默认复用 `datasets/processed/gear_insertion_drag_v1.zarr`，旧10维存储由数据读取器提供前9维训练标签。
输出独立保存到 `log_dir/pretrain/gear_dp_clip_pose9/<时间戳>/`，不覆盖旧ResNet模型。
最多300轮，验证损失连续30轮未改善至少1e-4时早停；这是本项目的防过拟合设置，
不是声称论文报告了相同epoch/早停参数。`best.pt` 仍按最低验证损失选择。

正式后台训练的进程、日志和状态路径见该输出根目录下的 `latest_launch.json`。
不要在后台训练运行时重复启动同一任务。
