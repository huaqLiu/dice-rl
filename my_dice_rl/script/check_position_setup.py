#!/usr/bin/env python3
"""Read-only check that the selected ROS overlay supports true position commands."""
from pathlib import Path
import sys

import yaml
from ament_index_python.packages import get_package_share_directory


def main():
    try:
        hardware = Path(get_package_share_directory('franka_hardware'))
        marker = yaml.safe_load((hardware / 'config/command_interfaces.yaml').read_text())
        if 'position' not in marker['supported_commands']:
            raise ValueError('franka_hardware 未提供 position 接口')
        get_package_share_directory('panda_cartesian_position_controller')
        description = Path(get_package_share_directory('franka_description'))
        if '<command_interface name="position"' not in (description / 'robots/panda_arm.ros2_control.xacro').read_text():
            raise ValueError('franka_description 未声明 position 接口')
    except Exception as error:
        print(f'位置控制环境未就绪：{error}\n请编译并 source 本项目 ros_ws；详见 README_position_control.md。', file=sys.stderr)
        return 1
    print(f'位置控制环境检查通过：{hardware}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
