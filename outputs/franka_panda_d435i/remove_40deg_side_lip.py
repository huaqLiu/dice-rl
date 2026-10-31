"""Remove the side lip identified in the user's image; retain original M6 tower."""

import json
import os
from pathlib import Path

import cadquery as cq


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "tilt_40deg_lightweight"
SOURCE_STEM = "Panda_D435i_Wrist_Mount_40deg_Lightweight"
OUT = ROOT / "tilt_40deg_lightweight_flush_side"
STEM = "Panda_D435i_Wrist_Mount_40deg_FlushSide"
CUT_X = -20.2


def seat_area(shape):
    area = 0.0
    for face in shape.Faces():
        box = face.BoundingBox()
        if (face.geomType() == "PLANE" and face.normalAt().z > 0.99
                and abs(box.zmin - 11.95) < 1e-5 and abs(box.zmax - 11.95) < 1e-5):
            area += face.Area()
    return area


def main():
    OUT.mkdir(exist_ok=True)
    source = cq.importers.importStep(str(SOURCE / f"{SOURCE_STEM}.step")).val()
    cutter = cq.Solid.makeBox(100, 200, 200, (CUT_X, -100, -100))
    result = source.cut(cutter).clean()
    assert result.isValid() and len(result.Solids()) == 1
    assert abs(result.BoundingBox().xmax - CUT_X) < 1e-5
    assert abs(source.BoundingBox().xmax - CUT_X - 5.2) < 1e-5
    assert result.intersect(cutter).Volume() < 1e-5

    # Use the model with the original upper tower. Only the side lip at
    # X > -20.2 is removed.
    preserved = [
        ("All geometry on the retained side of the flush cut",
         cq.Solid.makeBox(179.8, 200, 200, (-200, -100, -100))),
        ("M6 shank passage and central bearing material",
         cq.Solid.makeCylinder(4.7, 100, (-25, 0, -20), (0, 0, 1))),
        ("Robot bottom mating faces and curved locating surface",
         cq.Solid.makeBox(200, 200, 102, (-100, -100, -100))),
    ]
    differences = {}
    for name, region in preserved:
        before, after = source.intersect(region), result.intersect(region)
        difference = before.cut(after).Volume() + after.cut(before).Volume()
        assert difference < 1e-5, (name, difference)
        differences[name] = difference
    before_seat, after_seat = seat_area(source), seat_area(result)
    assert before_seat > 50 and after_seat / before_seat > 0.9
    removed = source.Volume() - result.Volume()
    assert removed > 100
    report = json.loads((SOURCE / "validation.json").read_text())
    report.update({
        "based_on": os.path.relpath(SOURCE / f"{SOURCE_STEM}.step", OUT),
        "side_lip_removed": True,
        "side_lip_depth_removed_mm": 5.2,
        "side_flush_plane_original_step_x_mm": CUT_X,
        "m6_top_flattened": False,
        "m6_original_tower_restored": True,
        "m6_tower_top_original_z_mm": 42.45,
        "m6_shank_clearance_diameter_mm": 7.0,
        "m6_shank_outward_wall_mm": 1.3,
        "m6_bearing_seat_area_before_mm2": before_seat,
        "m6_bearing_seat_area_after_mm2": after_seat,
        "m6_bearing_seat_area_retained_percent": 100 * after_seat / before_seat,
        "m6_bearing_seat_change": "The outermost 0.7-mm cap is trimmed flush along with the side lip; central seat remains.",
        "lightweight_cad_volume_mm3": result.Volume(),
        "solid_volume_reduction_percent": 100 * (1 - result.Volume() / report["original_cad_volume_mm3"]),
        "protected_interface_difference_mm3": differences,
        "cad_valid": result.isValid(),
        "cad_solid_count": len(result.Solids()),
    })
    report["removed_features"].append({"feature": "Side lip removed flush with the rear wall", "removed_volume_mm3": removed})
    cq.exporters.export(result, str(OUT / f"{STEM}.step"))
    cq.exporters.export(result, str(OUT / f"{STEM}.stl"), tolerance=0.01, angularTolerance=0.05)
    (OUT / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        "angle_degrees": report["angle_degrees"], "removed_lip_depth_mm": 5.2,
        "cad_valid": result.isValid(), "solid_count": len(result.Solids()),
        "bearing_area_retained_percent": 100 * after_seat / before_seat,
        "retained_geometry_differences_mm3": differences,
        "new_cad_volume_mm3": result.Volume(),
    }, indent=2))


if __name__ == "__main__":
    main()
