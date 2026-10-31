"""Validate the 40-degree lightweight mount with a flattened M6 tower."""

import verify_lightweight_mount as verify


if __name__ == "__main__":
    verify.OUT = verify.ROOT / "tilt_40deg_lightweight_flat_m6"
    verify.STEM = "Panda_D435i_Wrist_Mount_40deg_FlatM6"
    verify.BASELINE_DIR = verify.ROOT / "tilt_30deg_lightweight"
    verify.BASELINE_STEM = "Panda_D435i_Wrist_Mount_30deg_Lightweight"
    verify.BASELINE_LABEL = "Previous 30-degree mount / raised M6 top"
    verify.main()
