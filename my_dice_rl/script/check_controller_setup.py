#!/usr/bin/env python3
"""Read-only ROS overlay check for the controller selected in the application config."""
import argparse
from pathlib import Path
import sys

from ament_index_python.packages import get_package_share_directory
import yaml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    args = parser.parse_args()
    try:
        config = yaml.safe_load(args.config.read_text())
        if 'dice_spacemouse_teleop' in config:
            config = config['dice_spacemouse_teleop']['ros__parameters']
        controller = config['controller_name']
        interface = config['controller_command_interface']
        expected = {'panda_cartesian_impedance_controller': 'effort',
                    'panda_cartesian_position_controller': 'position'}
        if expected.get(controller) != interface:
            raise ValueError('控制器名称与 command interface 不匹配')
        get_package_share_directory(controller)
        hardware = Path(get_package_share_directory('franka_hardware'))
        if interface == 'position':
            marker = yaml.safe_load((hardware / 'config/command_interfaces.yaml').read_text())
            if 'position' not in marker['supported_commands']:
                raise ValueError('franka_hardware 未提供 position 接口')
        description = Path(get_package_share_directory('franka_description'))
        if f'<command_interface name="{interface}"' not in (description / 'robots/panda_arm.ros2_control.xacro').read_text():
            raise ValueError(f'franka_description 未声明 {interface} 接口')
    except Exception as error:
        print(f'控制环境未就绪：{error}\n请检查配置并 source 本项目 ros_ws/install/setup.bash。', file=sys.stderr)
        return 1
    print(f'控制环境检查通过：{controller}，七个 {interface} 接口')
    return 0


if __name__ == '__main__':
    sys.exit(main())
