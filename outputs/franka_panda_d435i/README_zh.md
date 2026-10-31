# Panda / RealSense D435i 腕部相机支架

该模型来自 Franka 官方 Panda 相机支架压缩包：
https://download.franka.de/panda_camera_mount.zip

本文件以官方原版 Panda_RealSenseD435_Camera_Mount.STL 为基础，导出为二进制 STL，未修改几何尺寸。不是本次从零设计的模型。

## 文件

- Panda_D435i_Wrist_Camera_Mount.stl：打印文件，尺寸单位按毫米导入，缩放 100%。
- Panda_D435i_Wrist_Camera_Mount.step：官方原版可编辑 CAD。
- Printing_Mounting_Instructions.pdf：压缩包内的 Panda 原版打印、装配说明。
- validation.json：本次网格检查结果及来源哈希。

## 适用安装方式

按原版说明，适用于 Panda + 原装 Franka Hand + RealSense D435 / D435i，将相机安装在靠近夹爪的腕部侧面，保留夹爪。若已经更换第三方夹爪或末端转接法兰，需要重新核对接口。

## 打印与装配（依据随附原版说明）

- 按 PDF 第 2 页右侧图摆放 RealSense 支架。
- 填充率大于 50%；支撑仅接触打印平台。
- 先用两颗 M3×10 螺钉固定相机。
- 再将 Franka Hand 原装两颗 M6×12、8.8 级螺钉中的一颗换为 M6×28、8.8 级螺钉以固定支架；锁紧扭矩按机器人对应手册。
- 支架具有数据线扎带固定槽。

外包络约为 48 × 60 × 49.95 mm。已检查单一连通实体、封闭性、法线一致性、正体积，以及二进制导出前后的尺寸与体积一致性。尚未在用户实机上试装。
