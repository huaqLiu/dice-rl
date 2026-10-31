"""Build a 35-degree variant of the user-approved flush-side lightweight mount."""

import make_tilted_mount as tilted
import make_lightweight_mount as lightweight
import remove_40deg_side_lip as side_lip


if __name__ == "__main__":
    tilted.ANGLE = 35.0
    tilted.OFFSET = 8.0
    tilted.CAMERA_LIFT = 5.0
    tilted.OUT = tilted.ROOT / "tilt_35deg"
    tilted.main()
    lightweight.OUT = lightweight.ROOT / "tilt_35deg_lightweight"
    lightweight.STEM = "Panda_D435i_Wrist_Mount_35deg_Lightweight"
    lightweight.SOURCE_STEP = tilted.OUT / "Panda_D435i_Wrist_Mount_Inward_35deg.step"
    lightweight.main()
    side_lip.SOURCE = lightweight.OUT
    side_lip.SOURCE_STEM = lightweight.STEM
    side_lip.OUT = lightweight.ROOT / "tilt_35deg_lightweight_flush_side"
    side_lip.STEM = "Panda_D435i_Wrist_Mount_35deg_FlushSide"
    side_lip.main()
