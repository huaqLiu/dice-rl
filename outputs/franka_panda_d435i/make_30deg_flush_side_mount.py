"""Build the 30-degree version of the approved flush-side lightweight mount."""

import make_tilted_mount as tilted
import make_lightweight_mount as lightweight
import remove_40deg_side_lip as side_lip


if __name__ == "__main__":
    destination = tilted.ROOT / "tilt_30deg_lightweight_flush_side"
    destination.mkdir(exist_ok=True)
    tilted.ANGLE = 30.0
    tilted.OFFSET = 8.0
    tilted.CAMERA_LIFT = 5.0
    tilted.OUT = destination / "source_full"
    tilted.main()
    lightweight.OUT = destination / "source_lightweight"
    lightweight.STEM = "Panda_D435i_Wrist_Mount_30deg_Lightweight"
    lightweight.SOURCE_STEP = tilted.OUT / "Panda_D435i_Wrist_Mount_Inward_30deg.step"
    lightweight.main()
    side_lip.SOURCE = lightweight.OUT
    side_lip.SOURCE_STEM = lightweight.STEM
    side_lip.OUT = destination
    side_lip.STEM = "Panda_D435i_Wrist_Mount_30deg_FlushSide"
    side_lip.main()
