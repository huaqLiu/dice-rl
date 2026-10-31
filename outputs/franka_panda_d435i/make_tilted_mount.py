"""Tilt the official Panda D435i camera wing 15 degrees toward the gripper.

Requires CadQuery 2.7. Units: mm. Uses the original STEP coordinate system:
the wrist axis is X=Y=0, the gripper direction is +Z, and the camera is at -X.
"""

import json
import math
from pathlib import Path

import cadquery as cq


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "tilt_15deg"
ANGLE = 15.0
OFFSET = 5.0
CAMERA_LIFT = 0.0
ROOT_X = -38.2
REAR_Z = 32.45
BOTTOM_Z = 22.45
PIVOT = (ROOT_X, 0, REAR_Z)
AXIS_END = (ROOT_X, 1, REAR_Z)


def moved(shape):
    return shape.rotate(PIVOT, AXIS_END, ANGLE).translate((-OFFSET, 0, CAMERA_LIFT))


def moved_point(x, z):
    a = math.radians(ANGLE)
    dx, dz = x - ROOT_X, z - REAR_Z
    return (
        ROOT_X + dx * math.cos(a) + dz * math.sin(a) - OFFSET,
        REAR_Z - dx * math.sin(a) + dz * math.cos(a) + CAMERA_LIFT,
    )


def main():
    OUT.mkdir(exist_ok=True)
    original = cq.importers.importStep(
        str(ROOT / "Panda_D435i_Wrist_Camera_Mount.step")
    ).val()
    left_box = cq.Solid.makeBox(200, 200, 200, (ROOT_X - 200, -100, -100))
    fixed = original.cut(left_box)
    wing = original.intersect(left_box)
    tilted_wing = moved(wing)

    # A solid transition with 0.5 mm overlap into each side of the joint.
    # The top stays on the tilted camera mounting plane, leaving its rear clear.
    overlap = 0.5
    moving_top = moved_point(ROOT_X - overlap, REAR_Z)
    moving_bottom = moved_point(ROOT_X - overlap, BOTTOM_Z)
    fixed_x = ROOT_X + overlap
    fixed_top_z = REAR_Z + CAMERA_LIFT - (fixed_x - (ROOT_X - OFFSET)) * math.tan(
        math.radians(ANGLE)
    )
    cross_section = [
        (fixed_x, fixed_top_z),
        moving_top,
        moving_bottom,
        (fixed_x, BOTTOM_Z),
    ]
    bridge = (
        cq.Workplane("XZ", origin=(0, 30, 0))
        .polyline(cross_section)
        .close()
        .extrude(60)
        .val()
    )
    fixed_region = cq.Solid.makeBox(200, 200, 200, (ROOT_X, -100, -100))
    # Trim the overlapping bridge to the original fixed-side fillets/chamfers.
    bridge = bridge.cut(fixed_region).fuse(bridge.intersect(fixed)).clean()
    assert tilted_wing.intersect(bridge).Volume() > 1
    assert fixed.intersect(bridge).Volume() > 1
    result = fixed.fuse(bridge, tilted_wing).clean()
    assert result.isValid(), "Invalid CAD solid"
    assert len(result.Solids()) == 1, "Disconnected CAD bodies"

    # No change to the robot attachment side, including the M6 passage,
    # screw-head recess, curved flange contact, and locating geometry.
    result_fixed = result.intersect(fixed_region)
    fixed_difference = fixed.cut(result_fixed).Volume() + result_fixed.cut(fixed).Volume()
    assert fixed_difference < 1e-5, fixed_difference

    # Conservatively approximate the camera housing by a 25 x 90 x 25 box.
    # Rear M3 centers: X=-50, Y=+-22.5, Z=32.45; optical axis is initially +Z.
    # A 0.01 mm gap avoids counting coplanar contact as an interference.
    camera_box = moved(cq.Solid.makeBox(25, 90, 24.99, (-62.5, -45, REAR_Z + 0.01)))
    camera_interference = result.intersect(camera_box).Volume()
    assert camera_interference < 1e-5, camera_interference
    camera_clearance = fixed.distance(camera_box)

    stem = OUT / f"Panda_D435i_Wrist_Mount_Inward_{ANGLE:g}deg"
    cq.exporters.export(result, str(stem.with_suffix(".step")))
    cq.exporters.export(
        result, str(stem.with_suffix(".stl")), tolerance=0.01, angularTolerance=0.05
    )
    a = math.radians(ANGLE)
    new_hole_centers = []
    for y in (-22.5, 22.5):
        x, z = moved_point(-50, REAR_Z)
        new_hole_centers.append([x, y, z])
    bbox = result.BoundingBox()
    report = {
        "source": "https://download.franka.de/panda_camera_mount.zip",
        "angle_degrees": ANGLE,
        "camera_outward_offset_mm": OFFSET,
        "camera_lift_mm": CAMERA_LIFT,
        "coordinate_system": "Original STEP: wrist center at X=Y=0; gripper direction +Z",
        "camera_optical_axis_unit_vector": [math.sin(a), 0, math.cos(a)],
        "camera_screw_clearance_diameter_mm": 3.4,
        "camera_hole_spacing_mm": 45.0,
        "camera_rear_hole_centers_mm": new_hole_centers,
        "robot_interface_symmetric_difference_mm3": fixed_difference,
        "cad_solid_valid": result.isValid(),
        "cad_solid_count": len(result.Solids()),
        "bounding_dimensions_mm": [bbox.xlen, bbox.ylen, bbox.zlen],
        "volume_mm3": result.Volume(),
        "camera_box_interference_mm3": camera_interference,
        "camera_box_to_fixed_mount_clearance_mm": camera_clearance,
        "camera_collision_check_limit": "Uses a nominal rectangular camera body, not the USB plug/cable or user robot.",
        "physical_fit_tested": False,
    }
    (OUT / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
