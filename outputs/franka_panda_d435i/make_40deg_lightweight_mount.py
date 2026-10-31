"""Build the lightweight 40-degree inward camera mount."""

import make_tilted_mount as tilted
import make_lightweight_mount as lightweight


if __name__ == "__main__":
    tilted.ANGLE = 40.0
    tilted.OFFSET = 8.0
    tilted.CAMERA_LIFT = 5.0
    tilted.OUT = tilted.ROOT / "tilt_40deg"
    tilted.main()
    lightweight.OUT = lightweight.ROOT / "tilt_40deg_lightweight"
    lightweight.STEM = "Panda_D435i_Wrist_Mount_40deg_Lightweight"
    lightweight.SOURCE_STEP = tilted.OUT / "Panda_D435i_Wrist_Mount_Inward_40deg.step"
    lightweight.main()
