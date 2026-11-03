"""RGB-only camera launch; device identities are pinned in the selected YAML."""
from pathlib import Path
import sys
# ros2 launch loads this file without adding the package parent to sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch.launch_description_sources import PythonLaunchDescriptionSource
from ament_index_python.packages import get_package_share_directory
from my_dice_rl.util.camera_launch import load_camera_config


def _launch(context):
    config = load_camera_config(LaunchConfiguration('config').perform(context))
    result = []
    for key in config['camera_names']:
        serial = str(config['devices'][key].get('serial_number', ''))
        if key == 'wrist':
            package, filename = 'realsense2_camera', 'rs_launch.py'
            args = dict(camera_namespace='wrist', camera_name='d435i',
                        enable_color='true', enable_depth='false', enable_infra1='false',
                        enable_infra2='false', enable_gyro='false', enable_accel='false',
                        **{'rgb_camera.color_profile': '640,480,30'})
            if serial:
                args['serial_no'] = '_' + serial
        else:
            package, filename = 'orbbec_camera', 'femto_bolt.launch.py'
            args = dict(camera_name=key, serial_number=serial, enable_color='true',
                        enable_depth='false', enable_ir='false', enable_accel='false',
                        enable_gyro='false', enable_sync_output_accel_gyro='false',
                        enable_frame_sync='false', color_width='1280', color_height='720',
                        color_fps='30', color_format='MJPG')
        path = get_package_share_directory(package) + '/launch/' + filename
        result.append(IncludeLaunchDescription(PythonLaunchDescriptionSource(path),
                                               launch_arguments=args.items()))
    return result


def generate_launch_description():
    default = str(Path(__file__).resolve().parents[1] / 'cfg/cameras.yaml')
    return LaunchDescription([DeclareLaunchArgument('config', default_value=default),
                              OpaqueFunction(function=_launch)])
