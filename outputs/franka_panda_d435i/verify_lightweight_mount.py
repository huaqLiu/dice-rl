"""Finalize the lightweight STL and render a comparison from the actual meshes."""

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import trimesh


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "tilt_15deg_lightweight"
STEM = "Panda_D435i_Wrist_Mount_15deg_Lightweight"
BASELINE_DIR = ROOT / "tilt_15deg"
BASELINE_STEM = "Panda_D435i_Wrist_Mount_Inward_15deg"
BASELINE_LABEL = "Previous 15-degree mount"
NEW_LABEL = None


def render_mesh(part, view, light, base_color, limits, pixels=720):
    """Orthographic depth-buffer rendering, avoiding overlapping-triangle artifacts."""
    right = np.cross([0, 0, 1], view)
    right /= np.linalg.norm(right)
    up = np.cross(view, right)
    projected = part.vertices @ np.column_stack([right, up, view])
    low, high = limits
    scale = (pixels - 48) / max(high - low)
    projected[:, :2] = (projected[:, :2] - (low + high) / 2) * scale + pixels / 2
    projected[:, 1] = pixels - projected[:, 1]
    canvas = np.ones((pixels, pixels, 3))
    depth = np.full((pixels, pixels), -np.inf)
    for face, normal in zip(part.faces, part.face_normals):
        if normal @ view <= 1e-7:
            continue
        tri = projected[face]
        xmin, ymin = np.maximum(np.floor(tri[:, :2].min(axis=0)).astype(int), 0)
        xmax, ymax = np.minimum(np.ceil(tri[:, :2].max(axis=0)).astype(int), pixels - 1)
        if xmin > xmax or ymin > ymax:
            continue
        x0, y0, z0 = tri[0]
        x1, y1, z1 = tri[1]
        x2, y2, z2 = tri[2]
        denominator = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(denominator) < 1e-10:
            continue
        yy, xx = np.mgrid[ymin:ymax + 1, xmin:xmax + 1]
        xx = xx + 0.5
        yy = yy + 0.5
        w0 = ((y1 - y2) * (xx - x2) + (x2 - x1) * (yy - y2)) / denominator
        w1 = ((y2 - y0) * (xx - x2) + (x0 - x2) * (yy - y2)) / denominator
        w2 = 1 - w0 - w1
        z = w0 * z0 + w1 * z1 + w2 * z2
        region = depth[ymin:ymax + 1, xmin:xmax + 1]
        mask = (w0 >= -1e-7) & (w1 >= -1e-7) & (w2 >= -1e-7) & (z > region)
        region[mask] = z[mask]
        color = np.array(base_color) * (0.4 + 0.6 * max(normal @ light, 0))
        canvas[ymin:ymax + 1, xmin:xmax + 1][mask] = color
    return canvas


def main():
    file = OUT / f"{STEM}.stl"
    report_file = OUT / "validation.json"
    report = json.loads(report_file.read_text())
    mesh = trimesh.load_mesh(file)
    if "stl_translation_from_step_mm" in report:
        mesh.apply_translation(-np.array(report["stl_translation_from_step_mm"]))
    face_count_before = len(mesh.faces)
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.update_faces(mesh.unique_faces())
    mesh.remove_unreferenced_vertices()
    assert mesh.is_watertight and mesh.is_winding_consistent and mesh.is_volume
    assert len(mesh.split(only_watertight=False)) == 1
    cad_mesh = mesh.copy()
    shift = -mesh.bounds[0]
    mesh.apply_translation(shift)
    mesh.export(file)
    checked = trimesh.load_mesh(file)
    assert checked.is_watertight and checked.is_winding_consistent and checked.is_volume
    assert len(checked.split(only_watertight=False)) == 1
    assert np.allclose(mesh.extents, checked.extents, atol=1e-5)
    assert abs(checked.volume / report["lightweight_cad_volume_mm3"] - 1) < 1e-4
    report.update({
        "stl_watertight": bool(checked.is_watertight),
        "stl_consistent_winding": bool(checked.is_winding_consistent),
        "stl_solid_count": 1,
        "stl_triangle_count": len(checked.faces),
        "stl_zero_area_or_duplicate_faces_removed": face_count_before - len(mesh.faces),
        "stl_dimensions_mm": checked.extents.tolist(),
        "stl_translation_from_step_mm": shift.tolist(),
        "stl_volume_mm3": float(checked.volume),
    })
    report_file.write_text(json.dumps(report, indent=2) + "\n")

    old_report = json.loads((BASELINE_DIR / "validation.json").read_text())
    previous = trimesh.load_mesh(BASELINE_DIR / f"{BASELINE_STEM}.stl")
    old_shift = old_report.get("stl_translation_from_step_mm", old_report.get("stl_translation_from_original_step_frame_mm", [0, 0, 0]))
    previous.apply_translation(-np.array(old_shift))
    angle = report["angle_degrees"]
    offset = report["camera_outward_offset_mm"]
    lift = report.get("camera_lift_mm", 0)
    flat_m6 = report.get("m6_top_flattened", False)
    side_lip_removed = report.get("side_lip_removed", False)
    m6_description = (
        "M6 孔周围高台削平至两侧平台高度，降低 7 mm；新顶面以下的通孔、孔内承压台阶和周围实体保持原几何。"
        if flat_m6 else "M6 螺钉通道、沉孔和半径 9 mm 的周围立柱保持原几何。"
    )
    additional_change = "\n4. M6 孔周围高台降低 7 mm，与两侧平台齐平。" if flat_m6 else ""
    locating_description = "机器人下部接触面、定位曲面和外侧定位凸缘保持原几何。"
    if side_lip_removed:
        m6_description = "M6 孔座保持原高度；7 mm 通孔的位置、孔径不变。孔底承压台阶主体保留，最外侧边缘随凸台一起修去少量。"
        additional_change = "\n4. 去掉背向相机一侧外伸 5.2 mm 的凸台，使侧面齐平；保留 M6 上部孔座原高度。"
        locating_description = "机器人下部接触面及定位曲面保持原几何；外侧横向定位凸台按用户要求去除。"
    fig = plt.figure(figsize=(12, 7), facecolor="white")
    light = np.array([-0.5, -0.6, 0.7])
    light /= np.linalg.norm(light)
    azimuth, elevation = np.deg2rad([-125, 27])
    view = np.array([np.cos(elevation) * np.cos(azimuth), np.cos(elevation) * np.sin(azimuth), np.sin(elevation)])
    right = np.cross([0, 0, 1], view)
    right /= np.linalg.norm(right)
    up = np.cross(view, right)
    projected = np.vstack([previous.vertices, cad_mesh.vertices]) @ np.column_stack([right, up])
    limits = (projected.min(axis=0), projected.max(axis=0))
    for index, part, base_color, title in [
        (1, previous, [0.50, 0.58, 0.67], BASELINE_LABEL),
        (2, cad_mesh, [0.22, 0.63, 0.63], NEW_LABEL or (f"Lightweight {angle:g}-degree mount" + (" / flat M6 top" if flat_m6 else ""))),
    ]:
        ax = fig.add_subplot(1, 2, index)
        ax.imshow(render_mesh(part, view, light, base_color, limits))
        ax.set_title(title)
        ax.set_axis_off()
    reduction = report["solid_volume_reduction_percent"]
    fig.suptitle(f"Panda + D435i | Lightweight {angle:g}-degree inward mount", fontsize=15)
    fig.text(0.5, 0.05, f"Solid volume reduced by {reduction:.1f}% vs full {angle:g}-degree mount. Print time depends on slicing.", ha="center", fontsize=10)
    fig.subplots_adjust(left=0, right=1, bottom=0.09, top=0.9, wspace=0)
    fig.savefig(OUT / "lightweight_comparison.png", dpi=150)
    plt.close(fig)

    (OUT / "README_zh.md").write_text(f"""# Panda / D435i 内倾 {angle:g}° 减料版

基于内倾 {angle:g}°、向外移 {offset:g} mm、抬高 {lift:g} mm 的定制支架做减料，既有版本文件均保留。抬高和外移用于相机外壳间隙与根部过渡厚度。

## 减料

1. 相机安装板中央开口，保留两条名义宽 16 mm、原厚度的安装臂和实体根部。
2. 支撑壁两侧开圆角减料窗，圆角半径 3 mm。
3. 降低顶部两侧凸台。{additional_change}

CAD 实体体积从 {report['original_cad_volume_mm3'] / 1000:.2f} cm³ 减为 {report['lightweight_cad_volume_mm3'] / 1000:.2f} cm³，减少 {reduction:.1f}%。
此比例是模型实体体积变化，不是耗材或打印时间的实测变化。切片后的耗材、时间还取决于填充、壁数、支撑与打印方向，本次没有运行切片器。

## 保留的接口

- 相机角度 {angle:g}°、向外移 {offset:g} mm、抬高 {lift:g} mm。
- 两个 M3 安装孔、沉孔及半径 7 mm 的周围实体保持原几何；孔距 45 mm。
- {m6_description}
- {locating_description}
- 原来穿过相机安装板中部的扎带孔随面板中央去除，线缆可在安装臂附近另行固定。

报告中的保留区域均进行了 CAD 布尔差异验证，体积差异为零；削除区域不在该结论内。原固定螺钉规格不变。

## 文件与打印

- {STEM}.stl：毫米单位，100% 比例；已平移至底面 Z=0。
- {STEM}.step：可编辑模型，保留原 STEP 坐标。
- lightweight_comparison.png：实际网格与上一版本的对比图，具体版本见图中标签。
- validation.json：尺寸、体积、接口保留及网格检查数据。

打印前在切片预览中检查安装臂与减料窗上沿的支撑。开窗后仍有悬空面，不能视为免支撑模型。

## 验证范围

CAD 为有效单一实体；STL 封闭、法线一致、单一连通体；相机简化包络无交叠。只进行了几何与网格验证，尚未进行结构仿真、实物强度试验或实机试装，不能将减料视为强度不变。
""", encoding="utf-8")
    print(json.dumps({
        "volume_reduction_percent": reduction,
        "stl_watertight": checked.is_watertight,
        "stl_solid_count": 1,
        "stl_dimensions_mm": checked.extents.tolist(),
        "stl_triangle_count": len(checked.faces),
        "stl_bytes": file.stat().st_size,
    }, indent=2))


if __name__ == "__main__":
    main()
