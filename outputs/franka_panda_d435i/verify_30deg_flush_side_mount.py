"""Validate the 30-degree flush-side mount and compare it with the 35-degree one."""

import json
import verify_lightweight_mount as verify


if __name__ == "__main__":
    verify.OUT = verify.ROOT / "tilt_30deg_lightweight_flush_side"
    verify.STEM = "Panda_D435i_Wrist_Mount_30deg_FlushSide"
    verify.BASELINE_DIR = verify.ROOT / "tilt_35deg_lightweight_flush_side"
    verify.BASELINE_STEM = "Panda_D435i_Wrist_Mount_35deg_FlushSide"
    verify.BASELINE_LABEL = "35 degrees / flush side"
    verify.NEW_LABEL = "30 degrees / flush side"
    verify.main()
    report = json.loads((verify.OUT / "validation.json").read_text())
    with (verify.OUT / "README_zh.md").open("a", encoding="utf-8") as file:
        file.write(f"\n## 侧面齐平的局部几何\n\nM6 孔底承压面保留约 {report['m6_bearing_seat_area_retained_percent']:.1f}%；通孔及中心承压区域未变。通孔向外侧的局部实体宽度约 1.3 mm，未做强度试验。\n")
