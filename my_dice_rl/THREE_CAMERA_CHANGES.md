# 三相机版本文件清单

代码保留在 `my_dice_rl`，新增独立三相机配置；其余为双/三相机共用实现。

本次统计：修改 32 个现有文件，新增 21 个文件（含本清单与验证摘要）。

## 新增文件

| 文件 | 大致作用 |
|---|---|
| [THREE_CAMERA_GUIDE.md](THREE_CAMERA_GUIDE.md) | 采集、处理、训练、部署、恢复、自动循环的完整指令与注意事项。 |
| [cfg/cameras.yaml](cfg/cameras.yaml) | 兼容双相机入口的设备绑定配置。 |
| [cfg/cameras_three.yaml](cfg/cameras_three.yaml) | 腕部、前方、侧面三相机设备绑定配置。 |
| [cfg/collect_drag_three.yaml](cfg/collect_drag_three.yaml) | 拖拽三相机采集配置，沿用随机复位设置。 |
| [cfg/collect_three.yaml](cfg/collect_three.yaml) | SpaceMouse 三相机采集配置和独立数据目录。 |
| [cfg/execute_dp_three.yaml](cfg/execute_dp_three.yaml) | 三相机 DP 执行和标注配置。 |
| [cfg/execute_rl_three.yaml](cfg/execute_rl_three.yaml) | 三相机 RL 执行、自动退轴复位、策略切换配置。 |
| [cfg/finetune/train_dice_three.yaml](cfg/finetune/train_dice_three.yaml) | 三相机 RL 模板，首轮2000步、以后每10条1000步。 |
| [cfg/pretrain/train_dp_three.yaml](cfg/pretrain/train_dp_three.yaml) | 三路预训练 CLIP 的 DP 训练配置。 |
| [cfg/task/gear_insertion_three.yaml](cfg/task/gear_insertion_three.yaml) | 三相机任务、专家数据包和观测/动作窗口。 |
| [script/collect_drag_three.sh](script/collect_drag_three.sh) | 三相机专用命令入口，复用对应原脚本并选择三相机配置。 |
| [script/collect_three.sh](script/collect_three.sh) | 三相机专用命令入口，复用对应原脚本并选择三相机配置。 |
| [script/execute_dp_three.sh](script/execute_dp_three.sh) | 三相机专用命令入口，复用对应原脚本并选择三相机配置。 |
| [script/execute_rl_three.sh](script/execute_rl_three.sh) | 三相机专用命令入口，复用对应原脚本并选择三相机配置。 |
| [script/train_dp_three.sh](script/train_dp_three.sh) | 三相机专用命令入口，复用对应原脚本并选择三相机配置。 |
| [script/train_rl_three.sh](script/train_rl_three.sh) | 三相机专用命令入口，复用对应原脚本并选择三相机配置。 |
| [tests/test_three_cameras.py](tests/test_three_cameras.py) | 验证三相机数据、网络、真实 ROS 消息/伪设备链路或兼容性。 |
| [util/camera_launch.py](util/camera_launch.py) | 启动前检查相机序列号绑定，阻止两台奥比中光使用同一序列号。 |
| [util/cameras.py](util/cameras.py) | 统一视角顺序、话题检查以及多路图像匹配。 |

## 修改文件

| 文件 | 大致作用 |
|---|---|
| [agent/dataset/gear_dataset.py](agent/dataset/gear_dataset.py) | 按数据包相机清单读取三路图像，检查任务相机布局。 |
| [agent/dataset/rl_chunks.py](agent/dataset/rl_chunks.py) | 专家回放动作块包含全部配置视角。 |
| [agent/dataset/rl_dataset.py](agent/dataset/rl_dataset.py) | 校验与读取三相机在线观测及推理历史，拒绝混合两/三相机。 |
| [agent/eval/dp_executor.py](agent/eval/dp_executor.py) | 执行期间记录全部视角的推理输入。 |
| [agent/eval/dp_policy.py](agent/eval/dp_policy.py) | 从 checkpoint 确认相机布局，用三路图像预热和推理，拒绝视角不一致。 |
| [agent/finetune/train_dice_agent.py](agent/finetune/train_dice_agent.py) | 检查在线数据视角；支持首轮热身更新和自动维护 current 运行目录链接。 |
| [env/real/policy_demo.py](env/real/policy_demo.py) | 为三相机执行界面提供独立模拟图像。 |
| [env/real/policy_observation.py](env/real/policy_observation.py) | 三路图像因果匹配、时间差校验和历史观测。 |
| [env/real/ros_backend.py](env/real/ros_backend.py) | 按相机配置订阅图像和内参，校验话题互不重复。 |
| [model/__main__.py](model/__main__.py) | 支持 --camera-count 3 查看网络与合成检查。 |
| [model/diffusion/diffusion_policy.py](model/diffusion/diffusion_policy.py) | 扩散策略接收配置化相机分支，保留动作和去噪网络设置。 |
| [model/vision/__init__.py](model/vision/__init__.py) | 更新视觉模块说明。 |
| [model/vision/multi_image_encoder.py](model/vision/multi_image_encoder.py) | 每个视角独立 CLIP/ResNet，按固定顺序拼接；三路 CLIP 单帧特征为2313维。 |
| [script/cameras.launch.py](script/cameras.launch.py) | 从 YAML 按序列号启动双/三路 RGB 相机。 |
| [script/check_rl_pipeline.py](script/check_rl_pipeline.py) | 支持三相机全链路演练，以及完整网络规模的 GPU 合成更新验证。 |
| [script/collect.sh](script/collect.sh) | 从采集配置选择相机启动配置，保持拖拽/遥操作入口兼容。 |
| [script/collect_gui.py](script/collect_gui.py) | 三栏采集预览、回放、健康状态及模拟录制。 |
| [script/diagnose_observation_sync.py](script/diagnose_observation_sync.py) | 只读同步诊断支持三路图像和三路到达事件。 |
| [script/execute_dp.sh](script/execute_dp.sh) | 按执行配置启动相机，支持独立 --camera-config。 |
| [script/process_gear_dataset.py](script/process_gear_dataset.py) | 导出第三路图像到 Zarr，验证全部相机时间差和数据完整性。 |
| [script/profile_dp_runtime.py](script/profile_dp_runtime.py) | 根据 checkpoint 选择双/三相机示范做离线推理耗时检查。 |
| [script/rl_collect_gui.py](script/rl_collect_gui.py) | 三栏 DP/RL 执行预览；显示实际加载的策略，支持独立数据目录。 |
| [tests/test_clip_encoder.py](tests/test_clip_encoder.py) | 验证三相机数据、网络、真实 ROS 消息/伪设备链路或兼容性。 |
| [tests/test_dp_execution_ros.py](tests/test_dp_execution_ros.py) | 验证三相机数据、网络、真实 ROS 消息/伪设备链路或兼容性。 |
| [tests/test_drag_collection_ros.py](tests/test_drag_collection_ros.py) | 验证三相机数据、网络、真实 ROS 消息/伪设备链路或兼容性。 |
| [tests/test_rl_dataset.py](tests/test_rl_dataset.py) | 验证三相机功能与双相机回归；异步写盘测试等待调整为允许繁忙磁盘完成。 |
| [tests/test_ros_collection.py](tests/test_ros_collection.py) | 验证三相机数据、网络、真实 ROS 消息/伪设备链路或兼容性。 |
| [util/checkpoint.py](util/checkpoint.py) | 从权重恢复相机布局并核对元数据，保留旧双相机加载能力。 |
| [util/config.py](util/config.py) | 核对任务和模型视角设置，构建三相机模型参数。 |
| [util/recording.py](util/recording.py) | 示范写入第三路 RGB/原图/时间戳；回放审核完整性检查。 |
| [util/rl_config.py](util/rl_config.py) | 验证新增首轮训练步数和运行目录别名设置。 |
| [util/rl_recording.py](util/rl_recording.py) | 在线 HDF5 保存三路观测图像和时间戳。 |

新增的 `THREE_CAMERA_CHANGES.md` 是本文件；`THREE_CAMERA_VALIDATION.json` 保存验收摘要。

验证产物：`log_dir/three_camera_validation/` 链接到 `/tmp/dice_three_camera_validation_20261103`。三相机界面截图、完整网络 GPU 演练报告和真实相机出流统计保存在其中。临时产物不属于可部署的真机策略。
