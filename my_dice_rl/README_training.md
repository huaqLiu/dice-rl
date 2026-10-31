# 双相机、10 维动作 DP：从采集数据到离线训练

本阶段补齐数据处理、离线训练、checkpoint 和离线推理检查。
模型沿用 `model/` 中的双路 ResNet18 + 条件一维 U-Net。
所有训练命令均不启动 ROS，不操作 Panda 或夹爪。

夜间 GPU 空闲训练、最新产物与查收入口见 [OVERNIGHT_REPORT.md](OVERNIGHT_REPORT.md)。
DP 训练与执行现在共用 GPU 资源锁，默认每 500 次更新保存断点；SIGUSR1/SIGTERM/SIGINT
请求在优化器更新边界保存并退出。`progress.json` 记录最近保存的进度。

## 1. 文件构成与阅读顺序

| 文件 | 职责 |
|---|---|
| `script/process_gear_dataset.py` | 检查原始 HDF5、转换位姿和夹爪标签、导出 Zarr |
| `util/rotation.py` | XYZW 四元数转旋转矩阵与六维旋转表示 |
| `agent/dataset/sequence_sampler.py` | 同一轨迹内的历史观测、未来动作切片与有效掩码 |
| `agent/dataset/gear_dataset.py` | 按轨迹分训练/验证集，从磁盘按需读取双路图像 |
| `model/common/normalizer.py` | 状态、动作的归一化；只拟合训练集 |
| `model/` | 图像编码器、条件 U-Net、DP 损失和动作采样 |
| `util/ema.py` | 更新 EMA 模型，供验证和离线推理使用 |
| `util/checkpoint.py` | 原子保存、恢复 RNG 状态、加载 EMA 策略 |
| `agent/pretrain/train_dp_agent.py` | 优化器、学习率、AMP、梯度裁剪、验证与断点恢复 |
| `util/config.py` | 安全读取 YAML，解析明确的 `--set key=value` 参数 |
| `cfg/task/gear_insertion.yaml` | 数据路径、坐标系、频率、窗口和轨迹划分 |
| `cfg/pretrain/train_dp.yaml` | 模型、优化器与训练配置 |
| `script/train_dp.py` / `train_dp.sh` | 训练入口 |
| `script/check_dp_pipeline.py` | 数据前向/反向检查，以及 checkpoint 离线动作预测 |

数据流：

```text
trajectory.h5 + metadata.json
        ↓ 检查长度、时间、位姿、夹爪模式与 JPEG
统一 Zarr：data/* + meta/episode_ends
        ↓ 按完整轨迹划分训练/验证
历史观测 + 未来10维目标动作 + 有效掩码
        ↓ 仅用训练轨迹拟合归一化
DP 加噪/去噪训练 → EMA 验证 → last.pt / best.pt
```

## 2. 训练环境

在本项目创建的 `.venv-dp` 中安装训练依赖，采集端继续使用系统 Python：

```bash
cd /home/lhq/dice-rl
my_dice_rl/.venv-dp/bin/python -m pip install --no-user -r my_dice_rl/requirements-training.txt
```

本机已经补装 Zarr 和 numcodecs；没有修改 ROS Python 环境。
实际验证版本：PyTorch 2.5.1+cu118、torchvision 0.20.1+cu118、diffusers 0.27.2、
Zarr 2.18.2、numcodecs 0.12.1。

以下命令从 `/home/lhq/dice-rl` 执行。配置中的相对数据/输出路径统一以
`/home/lhq/dice-rl/my_dice_rl` 为根目录，不随终端当前目录变化。

## 3. 转换采集数据

先通过采集界面回放并标记成功。转换一个任务目录：

```bash
my_dice_rl/.venv-dp/bin/python -m my_dice_rl.script.process_gear_dataset \
  --input datasets/demonstrations/gear_insertion \
  --output datasets/processed/gear_insertion.zarr
```

也可将 `--input` 指向单个批次或 episode。默认仅选择任务名匹配的成功真实示范；
failed、discarded、invalid、recording 与模拟数据不会进入正式导出。
成功条目损坏时直接报错，不会静默丢掉坏帧。输出已存在时拒绝覆盖，
补采后应导出为新名称，再修改 `task.dataset_path`。

每条示范会检查：

- HDF5 与元数据的格式、帧数、必要字段长度一致。
- 轨迹时间间隔与采样频率一致，接收数据没有超时或来自采样时刻之后。
- 两路相机接收时间差符合采集配置；这仍不代表硬件曝光同步。
- 位姿和开度有限、四元数有效，JPEG 可解码且为 224×224。
- 不混合不同基坐标系、TCP、采样频率或真实/模拟数据。

导出内容：

```text
gear_insertion.zarr/
├── data/
│   ├── camera_wrist                 # N,224,224,3 RGB uint8
│   ├── camera_external              # N,224,224,3 RGB uint8
│   ├── robot_eef_pose               # N,9 实际 XYZ+rotation6d
│   ├── gripper_width                # N,1 实际两指总开度，米
│   ├── action                       # N,10 目标 XYZ+rotation6d+目标开度
│   ├── gripper_mode                 # 原始0/1/2模式
│   ├── gripper_recorded_target_width # 原采集字段，不覆盖
│   ├── external_wrench              # N,6，留存但当前模型不使用
│   └── timestamp
├── meta/episode_ends                # 各条轨迹的累计结束索引
└── manifest.json                    # 导出摘要及跳过原因
```

### 夹爪动作标签

连续的第10维是两指间**目标总开度**，单位米：

- `move` 和 `grasp` 帧使用采集时已经接受的目标开度。
- `hold_existing` 是登记已有夹持，没有发送新命令。本项目明确将其训练标签
  映射为闭合/抓握目标，默认 **0 米**，原始登记开度另存。
- 映射由转换参数 `--hold-target-width` 与任务配置 `hold_target_width` 显式定义，
  二者必须一致。这是一项训练标签约定，不是对历史硬件命令的伪造。

因此一条始终夹着齿轮的示范会有恒定闭合标签；需要学习开闭时，必须采集实际
包含开闭示范的成功轨迹。将来执行策略时，闭合目标需要通过 Franka Hand 的
抓握逻辑解释，不能把“0米目标”理解成一定要压到物体不存在时的宽度。

## 4. 数据窗口与划分

默认 `obs_steps=2, horizon_steps=20, act_steps=4`：

- 在当前时刻 t，观测使用 t-1、t；动作标签从 t 开始。
- 轨迹开头缺少的历史观测重复首帧。
- 轨迹末尾不足的未来目标重复末帧，但通过 `valid_mask` 排除其损失。
- 窗口始终限制在一条轨迹内，不能跨到下一条示范。
- 每条轨迹所有窗口只属于同一个集合；验证比例默认20%，划分种子42。
- 只有一条示范时不划验证集，程序会明确提示 best 按训练损失选择。

图像按需从 Zarr 读取，不会一次把所有 RGB 装入内存。低维统计数据只取训练轨迹，
不存在验证集归一化泄漏。采样是10 Hz，不能照搬原高频原始数据的降采样倍数。

## 5. 先检查，再训练

先跑一个 batch 的读取、损失、反向传播与动作采样：

```bash
my_dice_rl/.venv-dp/bin/python -m my_dice_rl.script.check_dp_pipeline \
  --output log_dir/pretrain/preflight.npz
```

生成 NPZ 及同名 JSON，包含预测/参考动作、有效掩码、输入形状、位置与夹爪误差。
没有 checkpoint 时使用随机模型，只能确认计算链路，不能据此评价装配能力。

正式开始训练：

```bash
bash my_dice_rl/script/train_dp.sh
```

默认使用4090、batch size 8、100 epochs、AdamW、warmup+cosine 学习率、
AMP、梯度裁剪和 EMA。模型默认不下载 ImageNet 权重；需要使用预训练视觉骨干时，
可以显式设置 `model.pretrained_vision=true`，这可能触发权重下载。

例如更改数据路径、batch size、轮数：

```bash
bash my_dice_rl/script/train_dp.sh --set \
  task.dataset_path=datasets/processed/gear_insertion_v2.zarr \
  training.batch_size=4 training.epochs=200
```

未知配置键会报错。`training.max_train_steps`、`max_val_steps` 是每轮检查上限，
`max_total_steps` 是整个运行的优化器更新上限；正式训练默认均为 null。

## 6. 输出与恢复

```text
log_dir/pretrain/gear_dp/日期_时间_微秒/
├── config.yaml          # 已解析完整配置，可直接再次加载
├── split.json           # 数据集身份、训练/验证轨迹编号
├── metrics.jsonl        # 每轮训练/验证损失、学习率、AMP跳过次数
└── checkpoint/
    ├── last.pt          # 最近完成的训练段，可恢复
    └── best.pt          # EMA验证损失最低；无验证集则用训练损失
```

checkpoint 保存原模型、EMA、归一化、优化器、学习率、AMP状态、随机数、
当前轮次与下一 batch 位置。写入使用临时文件和原子替换。

恢复时自动读取同一运行目录的 `config.yaml`：

```bash
bash my_dice_rl/script/train_dp.sh \
  --resume /完整运行路径/checkpoint/last.pt
```

模型、数据定义和优化器参数变更会拒绝续训；数据路径可以移动，但数据集身份需一致。
支持从 `max_total_steps` 停在一轮中间的位置恢复，按同一随机顺序跳过已完成 batch。
恢复后可增大 epochs，学习率沿用原 checkpoint 的计划，超过原计划保持最小学习率。
Ctrl+C 会保留最近一次完整的原子 checkpoint；尚未保存的当前训练段不会被冒充为完成。

## 7. 加载训练结果，离线检查动作

```bash
my_dice_rl/.venv-dp/bin/python -m my_dice_rl.script.check_dp_pipeline \
  --checkpoint /完整运行路径/checkpoint/best.pt \
  --output log_dir/pretrain/trained_prediction.npz
```

默认加载 EMA 和保存的归一化，优先取验证集窗口。报告位置 RMSE（米）、夹爪
开度 RMSE（米）、旋转误差（弧度）。这些是单个离线窗口的模仿误差，
不能当作整套数据的完整评估，更不能当作真机装配成功率。
如需检查另一套同定义数据，可以通过 `--dataset` 指定，报告会标明数据身份是否一致。

## 8. 验证与当前范围

```bash
my_dice_rl/.venv-dp/bin/python -m pytest -q \
  my_dice_rl/tests/test_dp_model.py \
  my_dice_rl/tests/test_training_data.py \
  my_dice_rl/tests/test_training_resume.py
```

测试使用临时模拟轨迹，覆盖旋转约定、标签映射、数据完整性、边界切片、
按轨迹划分、归一化隔离，以及中途恢复的数据顺序、随机数、模型和EMA一致性。
模拟数据必须在转换时 `--allow-demo`，训练时 `task.allow_demo=true`，否则被拒绝。

本次另用3条共60帧模拟示范在4090上检查实际 DP 的224×224输入、AMP、
训练、checkpoint保存、续训与加载推理；检查产物位于 `log_dir/pretrain/_integration_*/`。
其中使用缩小的 U-Net 通道以快速验证工程流程，不是用于装配的训练结果。

2026-10-31 已用 26 条真实成功示范、2925 帧完成两组各 100 轮的正式 DP 训练，
推荐权重、验证误差与 4090 推理耗时见 [OVERNIGHT_REPORT.md](OVERNIGHT_REPORT.md)。
真机装配成功率仍待现场人工试机验证。DP 执行与人工标注见
[README_execution.md](README_execution.md)，RL 流程见 [README_RL.md](README_RL.md)。
