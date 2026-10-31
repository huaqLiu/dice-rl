"""Validate and prepare the tilted STL, plus a side-view comparison."""

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon
import numpy as np
import trimesh


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "tilt_15deg"
STL = OUT / "Panda_D435i_Wrist_Mount_Inward_15deg.stl"


def main():
    mesh = trimesh.load_mesh(STL)
    faces_before = len(mesh.faces)
    # OCCT may export zero-area triangles at collapsed fillet endpoints.
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.update_faces(mesh.unique_faces())
    mesh.remove_unreferenced_vertices()
    assert mesh.is_watertight and mesh.is_winding_consistent and mesh.is_volume
    assert len(mesh.split(only_watertight=False)) == 1
    cad_frame_mesh = mesh.copy()
    translation = -mesh.bounds[0]
    mesh.apply_translation(translation)
    mesh.export(STL)
    reloaded = trimesh.load_mesh(STL)
    assert reloaded.is_watertight and reloaded.is_winding_consistent and reloaded.is_volume
    assert len(reloaded.split(only_watertight=False)) == 1
    assert np.allclose(reloaded.extents, mesh.extents, atol=1e-5)
    report_path = OUT / "validation.json"
    report = json.loads(report_path.read_text())
    assert abs(reloaded.volume / report["volume_mm3"] - 1) < 1e-4
    report.update({
        "stl_watertight": bool(reloaded.is_watertight),
        "stl_consistent_winding": bool(reloaded.is_winding_consistent),
        "stl_solid_components": 1,
        "stl_degenerate_or_duplicate_faces_removed": faces_before - len(mesh.faces),
        "stl_triangle_count": len(mesh.faces),
        "stl_units": "mm",
        "stl_dimensions_mm": reloaded.extents.tolist(),
        "stl_translation_from_original_step_frame_mm": translation.tolist(),
        "stl_mesh_volume_mm3": float(reloaded.volume),
    })
    report_path.write_text(json.dumps(report, indent=2) + "\n")

    original = trimesh.load_mesh(ROOT / "Panda_D435i_Wrist_Camera_Mount.stl")
    original.apply_translation([-63, -30, -7.5])
    camera = np.array([[-62.5, 32.45], [-37.5, 32.45], [-37.5, 57.45], [-62.5, 57.45]])
    theta = np.deg2rad(15)
    rotation = np.array([[np.cos(theta), np.sin(theta)], [-np.sin(theta), np.cos(theta)]])
    pivot = np.array([-38.2, 32.45])
    tilted_camera = (camera - pivot) @ rotation.T + pivot + [-5, 0]
    fig, axes = plt.subplots(1, 2, figsize=(11, 7), constrained_layout=True)
    for ax, part, housing, angle, title in zip(
        axes, [original, cad_frame_mesh], [camera, tilted_camera], [0, theta],
        ["Original mount: 0 degrees", "New mount: 15 degrees toward gripper"],
    ):
        section = part.section(plane_origin=[0, 10, 0], plane_normal=[0, 1, 0])
        for line in section.discrete:
            ax.add_patch(Polygon(line[:, [0, 2]], facecolor="#e1e4e8", edgecolor="#424b57", linewidth=1.1))
        ax.add_patch(Polygon(housing, facecolor="#b6d7f4", edgecolor="#2165a3", linewidth=1.6, alpha=0.8))
        front = (housing[2] + housing[3]) / 2
        direction = np.array([np.sin(angle), np.cos(angle)])
        ax.annotate("", xy=front + 36 * direction, xytext=front, arrowprops={"arrowstyle": "->", "color": "#d16520", "lw": 2.5})
        ax.text(-80, 100, "Orange arrow: camera viewing direction", fontsize=8, color="#a24f19")
        ax.annotate("", xy=(0, 95), xytext=(0, 0), arrowprops={"arrowstyle": "->", "color": "#32805a", "lw": 1.6, "linestyle": "--"})
        ax.text(2, 30, "Gripper axis / toward fingertips", rotation=90, fontsize=9, color="#267248")
        ax.set(xlim=(-85, 12), ylim=(-15, 108), xlabel="X (mm)", ylabel="Z (mm)", title=title)
        ax.set_aspect("equal")
        ax.grid(alpha=0.2)
    fig.suptitle("Panda + D435i wrist mount | nominal camera body shown in blue", fontsize=13)
    fig.savefig(OUT / "tilt_15deg_comparison.png", dpi=160)
    plt.close(fig)
    (OUT / "README_zh.md").write_text("""# Panda / D435i 向夹爪内倾 15° 支架

在 Franka 官方 Panda 支架的 STEP 模型上修改，属于定制版本，未经 Franka 认证。
原版来源：https://download.franka.de/panda_camera_mount.zip

## 改动

- 相机相对原朝向向夹爪中心线倾斜 15°。
- 相机安装翼向外平移 5 mm，为倾斜后的相机外壳留出空间。
- 增加实体过渡段，将相机安装翼与固定底座连成一体。
- 原机器人固定区域未改动；相机 M3 安装孔仍为直径 3.4 mm 通孔，孔距 45 mm，保留原孔内的螺钉头沉孔。
- STL 已平移至底部 Z=0；STEP 保留原始机器人坐标。两者几何形状相同。

## 文件

- Panda_D435i_Wrist_Mount_Inward_15deg.stl：打印文件，毫米，100% 比例。
- Panda_D435i_Wrist_Mount_Inward_15deg.step：可编辑 CAD。
- tilt_15deg_comparison.png：原版与内倾版侧视对比，蓝色为相机简化外壳。
- validation.json：CAD 与 STL 检查数据。

## 打印与安装

沿用原版说明中的紧固件：2 颗 M3×10 相机螺钉、1 颗 M6×28 / 8.8 级机器人固定螺钉。
导入切片软件后按毫米、100% 比例打印；填充率大于 50%，倾斜安装翼下方需要支撑，可使用仅接触打印平台的支撑并在切片预览中确认悬空区域。
原版 PDF 的安装方式可参考，但其中的相机角度图不是本定制版。

## 验证范围

已检查：CAD 有效且为单一实体；机器人固定区域几何差异为零；STL 封闭、法线一致、单一连通实体；相机名义 25×90×25 mm 包络与支架无交叠，与固定部分的最小间距约 2.52 mm。
上述包络未包含 USB 插头和线缆，也未验证用户实机、夹爪及夹爪全行程。装配后先检查夹爪开合与线缆空间，再低速试运行；相机朝向已变化，需要重新进行手眼标定。
""", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
