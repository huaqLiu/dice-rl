"""Validate the corrected side-lip removal and compare against the protruding lip."""

import json
import verify_lightweight_mount as verify


if __name__ == "__main__":
    verify.OUT = verify.ROOT / "tilt_40deg_lightweight_flush_side"
    verify.STEM = "Panda_D435i_Wrist_Mount_40deg_FlushSide"
    verify.BASELINE_DIR = verify.ROOT / "tilt_40deg_lightweight"
    verify.BASELINE_STEM = "Panda_D435i_Wrist_Mount_40deg_Lightweight"
    verify.BASELINE_LABEL = "40 degrees / protruding side lip"
    verify.NEW_LABEL = "Corrected 40 degrees / flush side"
    verify.main()
    report = json.loads((verify.OUT / "validation.json").read_text())
    with (verify.OUT / "README_zh.md").open("a", encoding="utf-8") as file:
        file.write(f"\n## 侧面齐平修改的局部变化\n\nM6 孔底承压平面面积从 {report['m6_bearing_seat_area_before_mm2']:.2f} mm² 减至 {report['m6_bearing_seat_area_after_mm2']:.2f} mm²，保留约 {report['m6_bearing_seat_area_retained_percent']:.1f}%；主要通孔及中心承压区域未变。通孔向外侧的局部最小实体宽度约 1.3 mm，未做强度试验。本次修正不保证与用户实机完全贴合，需试装确认。\n")
