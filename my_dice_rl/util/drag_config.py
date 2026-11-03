"""Validation independent of ROS and Qt."""
import math


def validate_drag_config(config):
    if config.get('collection_mode') != 'kinesthetic' or config.get('action_source') != 'measured_eef_pose':
        raise ValueError('拖拽采集需要 collection_mode=kinesthetic 和 action_source=measured_eef_pose')
    if config.get('controller_name') != 'panda_drag_controller' or config.get('controller_command_interface') != 'effort':
        raise ValueError('拖拽采集需要 panda_drag_controller 的 effort 接口')
    for key in ('max_joint_velocity', 'max_force_n', 'max_displacement_m'):
        value = config['drag'][key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError('drag.' + key + ' must be positive and finite')
    for key in ('heartbeat_topic', 'state_topic'):
        if not isinstance(config['drag'].get(key), str) or not config['drag'][key].startswith('/'):
            raise ValueError('Invalid drag.' + key)
    if config['save_width'] != 224 or config['save_height'] != 224:
        raise ValueError('拖拽策略画面必须为224×224')
    if config.get('reset') is not None:
        reset = config['reset']
        for key in ('position_noise_m', 'rotation_noise_deg', 'clearance_m', 'linear_speed',
                    'angular_speed', 'linear_acceleration', 'angular_acceleration',
                    'max_distance_m', 'max_angle_rad', 'position_tolerance_m',
                    'orientation_tolerance_rad', 'tracking_position_error_m',
                    'tracking_orientation_error_rad', 'max_force_n', 'max_torque_nm', 'settle_s', 'timeout_s'):
            value = reset[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError('reset.'+key+' must be positive and finite')
        if reset['position_tolerance_m'] >= reset['tracking_position_error_m']:
            raise ValueError('复位位置容差必须小于跟踪保护阈值')
        if reset['orientation_tolerance_rad'] >= reset['tracking_orientation_error_rad']:
            raise ValueError('复位姿态容差必须小于跟踪保护阈值')
        if reset['controller_name'] != 'panda_cartesian_impedance_controller':
            raise ValueError('复位使用 panda_cartesian_impedance_controller')
