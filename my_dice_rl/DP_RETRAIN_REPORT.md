# 新增 demo 后的 DP 重新训练

**状态：训练与离线评估完成**

- 报告更新时间：2026-10-31T15:43:13+08:00
- 数据：74 条真实成功示范，8602 帧；失败/无效记录排除。
- 按整条轨迹划分：59 条训练，15 条验证。
- 本次从 ImageNet 初始化重新训练 DP，不恢复旧数据集的优化器或归一化统计。
- 设置：100 epochs，batch 8，学习率 1e-4，EMA，双 224×224 RGB，20 步/10 维动作。
- GPU 模式：按用户指令与现有 VLA 任务共享 GPU；启动前至少保留 6 GiB 可用显存，不终止其他任务。
- 本次自动等待/训练截止：2026-11-01T12:01:07+08:00
- 最新保存进度：`{"updated_at": "2026-10-31T15:42:09.698294+08:00", "global_step": 86365, "epoch": 100, "batch_in_epoch": 0, "training_complete": true, "best_loss": 0.029942524618995055}`

[训练配置](/home/lhq/dice-rl/my_dice_rl/log_dir/dp_retrain_20261031_115755/train_dp.yaml) · [实时状态](/home/lhq/dice-rl/my_dice_rl/log_dir/dp_retrain_20261031_115755/supervisor/status.json) · [后台日志](/home/lhq/dice-rl/my_dice_rl/log_dir/dp_retrain_20261031_115755/supervisor.log)

最佳 EMA 权重：

```text
/home/lhq/dice-rl/my_dice_rl/log_dir/pretrain/gear_dp_retrain_20261031_115755/20261031_130741_689656/checkpoint/best.pt
```

最新恢复断点：`/home/lhq/dice-rl/my_dice_rl/log_dir/pretrain/gear_dp_retrain_20261031_115755/20261031_130741_689656/checkpoint/last.pt`。最优轮次可能早于 100，运行完成情况以最终断点和状态为准。

离线评估完成 45 个窗口（覆盖验证轨迹开头/中段/结尾）：前 4 步平均位置误差 15.19 mm，95 分位 29.34 mm。

[完整评估及预测](/home/lhq/dice-rl/my_dice_rl/log_dir/dp_retrain_20261031_115755/evaluation.json)。此次验证集与上一版不同，新旧误差不是同一验证集上的严格对照。

训练不自动操作机械臂，也不自动修改执行范围或负载参数。当前示范的最大运动包络约 132.4 mm，部署配置仍为 100 mm，试机前需现场核对。

[数据转换日志](/home/lhq/dice-rl/my_dice_rl/log_dir/dp_retrain_20261031_115755/dataset_conversion.log) · [数据划分与覆盖](/home/lhq/dice-rl/my_dice_rl/log_dir/dp_retrain_20261031_115755/split_coverage.json) · [部署范围检查](/home/lhq/dice-rl/my_dice_rl/log_dir/dp_retrain_20261031_115755/deployment_preflight.json)
