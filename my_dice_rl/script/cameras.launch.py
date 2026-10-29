"""RGB capture profiles supported by the two local camera drivers.

Both policy images are cropped to 224x224 by util/recording.py, like DICE-RL-Robot.
"""
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    def include(package, filename, arguments):
        path = get_package_share_directory(package) + '/launch/' + filename
        return IncludeLaunchDescription(PythonLaunchDescriptionSource(path),
                                        launch_arguments=arguments.items())

    return LaunchDescription([
        include('realsense2_camera', 'rs_launch.py', {
            'camera_namespace': 'wrist', 'camera_name': 'd435i',
            'enable_color': 'true', 'enable_depth': 'false',
            'enable_infra1': 'false', 'enable_infra2': 'false',
            'enable_gyro': 'false', 'enable_accel': 'false',
            'rgb_camera.color_profile': '640,480,30',
        }),
        include('orbbec_camera', 'femto_bolt.launch.py', {
            'camera_name': 'external', 'enable_color': 'true',
            'enable_depth': 'false', 'enable_ir': 'false',
            'enable_accel': 'false', 'enable_gyro': 'false',
            'enable_sync_output_accel_gyro': 'false', 'enable_frame_sync': 'false',
            'color_width': '1280', 'color_height': '720', 'color_fps': '30',
            'color_format': 'MJPG',
        }),
    ])
