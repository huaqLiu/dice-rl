"""Make a lighter version of the previously generated 15-degree Panda mount."""

import json
import os
from pathlib import Path

import cadquery as cq

import make_tilted_mount as tilted
from make_tilted_mount import moved


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "tilt_15deg_lightweight"
STEM = "Panda_D435i_Wrist_Mount_15deg_Lightweight"
SOURCE_STEP = ROOT / "tilt_15deg/Panda_D435i_Wrist_Mount_Inward_15deg.step"


def main():
    OUT.mkdir(exist_ok=True)
    source = cq.importers.importStep(str(SOURCE_STEP)).val()
    result = source
    removals = []

    def remove(name, tool):
        nonlocal result
        previous_volume = result.Volume()
        result = result.cut(tool).clean()
        assert result.isValid(), name
        assert len(result.Solids()) == 1, name
        removals.append({"feature": name, "removed_volume_mm3": previous_volume - result.Volume()})

    # An open-ended window leaves two full-thickness 16-mm-wide camera arms.
    # Both M3 counterbores and the solid root crossbar remain.
    remove("Open window between camera mounting arms",
           cq.Solid.makeBox(60, 28, 100, (-100.5, -14, -20)))

    # Rounded windows reduce stress concentrations; the central M6 column,
    # bottom contact region, locating lip, and upper root beam are retained.
    for y in (-18, 18):
        tool = (
            cq.Workplane("YZ", origin=(-37.2, y, 14.5))
            .rect(16, 19)
            .extrude(17)
            .edges("|X")
            .fillet(3)
            .val()
        )
        remove(f"Rounded side window Y={y}", tool)

    # Lower only the unused shoulders. The original M6 head seat and central
    # column remain at their original height, retaining the same screw length.
    for y in (-30, 10):
        remove(f"Trim unused raised shoulder Y={y}",
               cq.Solid.makeBox(19.8, 20, 30, (-40, y, 35.45)))

    # Explicitly protect the original functional interfaces and screw bosses.
    protected = [
        ("Robot lower contact and locating surfaces",
         cq.Solid.makeBox(200, 200, 102, (-100, -100, -100))),
        ("Robot outer locating lip",
         cq.Solid.makeBox(120.2, 200, 200, (-20.2, -100, -100))),
        ("M6 passage, head seat, and 9-mm-radius surrounding column",
         cq.Solid.makeCylinder(9, 100, (-25, 0, -20), (0, 0, 1))),
    ]
    for y in (-22.5, 22.5):
        protected.append((
            f"M3 screw boss Y={y}",
            moved(cq.Solid.makeCylinder(7, 16, (-50, y, 20), (0, 0, 1))),
        ))
    interface_differences = {}
    for name, region in protected:
        before = source.intersect(region)
        after = result.intersect(region)
        difference = before.cut(after).Volume() + after.cut(before).Volume()
        assert difference < 1e-5, (name, difference)
        interface_differences[name] = difference

    # All changes are subtractive, so prior camera-body clearance is retained.
    added_volume = result.cut(source).Volume()
    assert added_volume < 1e-5
    camera = moved(cq.Solid.makeBox(25, 90, 24.99, (-62.5, -45, 32.46)))
    interference = result.intersect(camera).Volume()
    assert interference < 1e-5
    source_volume, volume = source.Volume(), result.Volume()
    report = {
        "based_on": os.path.relpath(SOURCE_STEP, OUT),
        "angle_degrees": tilted.ANGLE,
        "camera_outward_offset_mm": tilted.OFFSET,
        "camera_lift_mm": tilted.CAMERA_LIFT,
        "original_cad_volume_mm3": source_volume,
        "lightweight_cad_volume_mm3": volume,
        "solid_volume_reduction_percent": 100 * (1 - volume / source_volume),
        "removed_features": removals,
        "protected_interface_difference_mm3": interface_differences,
        "added_volume_mm3": added_volume,
        "camera_box_interference_mm3": interference,
        "cad_valid": result.isValid(),
        "cad_solid_count": len(result.Solids()),
        "camera_arm_nominal_width_mm": 16,
        "M3_clearance_diameter_mm": 3.4,
        "M3_spacing_mm": 45,
        "side_window_corner_radius_mm": 3,
        "print_time_savings_measured": False,
        "physical_strength_tested": False,
        "physical_fit_tested": False,
    }
    cq.exporters.export(result, str(OUT / f"{STEM}.step"))
    cq.exporters.export(result, str(OUT / f"{STEM}.stl"), tolerance=0.01, angularTolerance=0.05)
    (OUT / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
