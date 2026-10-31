"""Flatten the raised M6 tower to the adjacent shoulders of the 40-degree mount."""

import json
from pathlib import Path

import cadquery as cq
import make_tilted_mount as tilted


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "tilt_40deg_lightweight"
OUT = ROOT / "tilt_40deg_lightweight_flat_m6"
STEM = "Panda_D435i_Wrist_Mount_40deg_FlatM6"
FLAT_Z = 35.45


def main():
    OUT.mkdir(exist_ok=True)
    source = cq.importers.importStep(str(SOURCE / "Panda_D435i_Wrist_Mount_40deg_Lightweight.step")).val()
    tool = cq.Solid.makeBox(60, 100, 70, (-37.45, -50, FLAT_Z))
    result = source.cut(tool).clean()
    assert result.isValid() and len(result.Solids()) == 1
    assert result.intersect(tool).Volume() < 1e-5
    assert source.Volume() - result.Volume() > 100

    tilted.ANGLE, tilted.OFFSET, tilted.CAMERA_LIFT = 40, 8, 5
    protected = [
        ("M6 passage and bearing seat below the new top plane",
         cq.Solid.makeCylinder(9, FLAT_Z + 20, (-25, 0, -20), (0, 0, 1))),
        ("Robot lower mating and locating surfaces",
         cq.Solid.makeBox(200, 200, 102, (-100, -100, -100))),
        ("Robot outer locating lip",
         cq.Solid.makeBox(120.2, 200, 200, (-20.2, -100, -100))),
    ]
    for y in (-22.5, 22.5):
        protected.append((f"Camera M3 boss Y={y}",
                          tilted.moved(cq.Solid.makeCylinder(7, 16, (-50, y, 20), (0, 0, 1)))))
    differences = {}
    for label, region in protected:
        before, after = source.intersect(region), result.intersect(region)
        difference = before.cut(after).Volume() + after.cut(before).Volume()
        assert difference < 1e-5, (label, difference)
        differences[label] = difference
    camera = tilted.moved(cq.Solid.makeBox(25, 90, 24.99, (-62.5, -45, 32.46)))
    interference = result.intersect(camera).Volume()
    assert interference < 1e-5

    report = json.loads((SOURCE / "validation.json").read_text())
    report.update({
        "based_on": "../tilt_40deg_lightweight/Panda_D435i_Wrist_Mount_40deg_Lightweight.step",
        "m6_top_flattened": True,
        "m6_top_z_before_mm": 42.45,
        "m6_top_z_after_mm": FLAT_Z,
        "m6_tower_height_removed_mm": 7.0,
        "m6_passage_and_bearing_seat_preserved": True,
        "lightweight_cad_volume_mm3": result.Volume(),
        "solid_volume_reduction_percent": 100 * (1 - result.Volume() / report["original_cad_volume_mm3"]),
        "protected_interface_difference_mm3": differences,
        "cad_valid": result.isValid(),
        "cad_solid_count": len(result.Solids()),
        "camera_box_interference_mm3": interference,
    })
    report["removed_features"].append({
        "feature": "Raised M6 tower, flattened to adjoining shoulder height",
        "removed_volume_mm3": source.Volume() - result.Volume(),
    })
    cq.exporters.export(result, str(OUT / f"{STEM}.step"))
    cq.exporters.export(result, str(OUT / f"{STEM}.stl"), tolerance=0.01, angularTolerance=0.05)
    (OUT / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        "cad_valid": result.isValid(), "solid_count": len(result.Solids()),
        "angle_degrees": 40, "tower_height_removed_mm": 7,
        "volume_mm3": result.Volume(), "protected_differences_mm3": differences,
        "camera_interference_mm3": interference,
    }, indent=2))


if __name__ == "__main__":
    main()
