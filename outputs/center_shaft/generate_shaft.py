"""Generate a single watertight shaft and circular base; all dimensions in mm."""

import argparse
from pathlib import Path

import numpy as np
import trimesh


def build_shaft(diameter, length, base_diameter, base_thickness, segments=256):
    if min(diameter, length, base_diameter, base_thickness) <= 0:
        raise ValueError("All dimensions must be positive")
    if base_diameter <= diameter:
        raise ValueError("Base diameter must exceed shaft diameter")
    angles = np.arange(segments) * (2 * np.pi / segments)
    vertices = []
    for radius, height in [
        (base_diameter / 2, 0),
        (base_diameter / 2, base_thickness),
        (diameter / 2, base_thickness),
        (diameter / 2, base_thickness + length),
    ]:
        vertices.extend((radius * np.cos(a), radius * np.sin(a), height) for a in angles)
    bottom_center = len(vertices)
    vertices.append((0, 0, 0))
    top_center = len(vertices)
    vertices.append((0, 0, base_thickness + length))
    faces = []
    for i in range(segments):
        j = (i + 1) % segments
        faces.append((bottom_center, j, i))
        for ring in range(3):
            lower = ring * segments
            upper = (ring + 1) * segments
            faces.extend([
                (lower + i, lower + j, upper + j),
                (lower + i, upper + j, upper + i),
            ])
        faces.append((top_center, 3 * segments + i, 3 * segments + j))
    return trimesh.Trimesh(vertices=vertices, faces=faces, process=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diameter", type=float, default=4.0)
    parser.add_argument("--length", type=float, required=True, help="Shaft length above base")
    parser.add_argument("--base-diameter", type=float, required=True)
    parser.add_argument("--base-thickness", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    mesh = build_shaft(args.diameter, args.length, args.base_diameter, args.base_thickness)
    assert mesh.is_watertight and mesh.is_winding_consistent and mesh.is_volume
    assert len(mesh.split()) == 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(args.output)
    loaded = trimesh.load_mesh(args.output)
    assert loaded.is_watertight and loaded.is_volume
    assert len(loaded.split()) == 1
    expected = [args.base_diameter, args.base_diameter, args.base_thickness + args.length]
    assert np.allclose(loaded.extents, expected, atol=1e-5)
    print(f"Created: {args.output}")
    print(f"Dimensions (mm): {loaded.extents.tolist()}")
    print(f"Shaft diameter: {args.diameter:g}; length above base: {args.length:g}")
    print(f"Base diameter: {args.base_diameter:g}; thickness: {args.base_thickness:g}")
    print(f"Closed single solid; triangles: {len(loaded.faces)}")


if __name__ == "__main__":
    main()
