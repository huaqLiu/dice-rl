"""Validate the 30-degree STL and compare it with the 15-degree lightweight one."""

import verify_lightweight_mount as verify


if __name__ == "__main__":
    verify.OUT = verify.ROOT / "tilt_30deg_lightweight"
    verify.STEM = "Panda_D435i_Wrist_Mount_30deg_Lightweight"
    verify.BASELINE_DIR = verify.ROOT / "tilt_15deg_lightweight"
    verify.BASELINE_STEM = "Panda_D435i_Wrist_Mount_15deg_Lightweight"
    verify.BASELINE_LABEL = "Lightweight 15-degree mount"
    verify.main()
