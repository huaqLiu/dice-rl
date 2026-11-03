"""Validate camera-to-device bindings before constructing ROS launch actions."""
from pathlib import Path
import yaml
from my_dice_rl.util.cameras import camera_names


def load_camera_config(path):
    config = yaml.safe_load(Path(path).read_text())
    names = camera_names(config)
    devices = config['devices']
    if set(devices) != set(names):
        raise ValueError('Camera devices must match the ordered camera_names')
    if len(names) == 3:
        serials = [str(devices[key].get('serial_number', '')).strip() for key in ('external', 'side')]
        if any(not serial for serial in serials) or len(set(serials)) != 2:
            raise ValueError('Three-camera capture requires two different explicit Orbbec serial numbers')
    return config
