"""Synthetic collected episodes, kept under pytest's temporary directories."""
import numpy as np
import pytest

from my_dice_rl.util.recording import EpisodeWriter, Sample


@pytest.fixture
def collected_episodes(tmp_path):
    root = tmp_path / 'raw'
    for episode in range(3):
        config = dict(task='gear_insertion', simulated=True, record_hz=10.,
            min_episode_frames=16, save_width=224, save_height=224, save_raw_rgb=False,
            jpeg_quality=95, max_age_s=.2, max_camera_skew_s=.06,
            base_frame='panda_link0', ee_frame='panda_hand_tcp')
        writer = EpisodeWriter(root / 'session', config)
        for index in range(20):
            tick = 1000. + episode * 100 + index * .1
            mode = 'hold_existing' if index < 4 else ('grasp' if index < 10 else 'move')
            actual_width = .035 if index < 10 else .08
            target_width = {'hold_existing': .035, 'grasp': 0., 'move': .08}[mode]
            # Episode 2 is intentionally out of training range for split/leakage checks.
            x = [.40, .50, .90][episode] + index * .001
            angle = index * .01
            pose = [x, .02, .3, 0., 0., np.sin(angle/2), np.cos(angle/2)]
            target = [x+.002, *pose[1:]]
            values = dict(
                wrist=np.full((224, 224, 3), [180, 30+episode*30, 10], np.uint8),
                external=np.full((224, 224, 3), [10, 30, 180], np.uint8),
                eef=pose, target=target, gripper=actual_width,
                teleop=dict(gripper_target_width=target_width, gripper_busy=False,
                            gripper_speed=.02, gripper_force=10., gripper_command_mode=mode),
                robot=dict(q=np.zeros(7), dq=np.zeros(7), wrench=np.zeros(6)))
            writer.append(tick, {key: Sample(tick-.01, 1700000000+tick, value)
                                 for key, value in values.items()})
        writer.close('success')
    return root
