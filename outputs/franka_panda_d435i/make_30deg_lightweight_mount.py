"""Build a 30-degree lightweight variant without altering the 15-degree files."""

import make_tilted_mount as tilted
import make_lightweight_mount as lightweight


def main():
    tilted.ANGLE = 30.0
    tilted.OFFSET = 8.0
    # Extra root height maintains a substantial bridge at the larger angle.
    tilted.CAMERA_LIFT = 3.0
    tilted.OUT = tilted.ROOT / "tilt_30deg"
    tilted.main()
    lightweight.OUT = lightweight.ROOT / "tilt_30deg_lightweight"
    lightweight.STEM = "Panda_D435i_Wrist_Mount_30deg_Lightweight"
    lightweight.SOURCE_STEP = tilted.OUT / "Panda_D435i_Wrist_Mount_Inward_30deg.step"
    lightweight.main()


if __name__ == "__main__":
    main()
