"""Synthetic in-process devices for UI checks; no ROS imports."""
import numpy as np
import cv2
import time


def feed_demo(buffer, backend, stop):
    while not stop.is_set():
        now = time.monotonic()
        for key, color in (('wrist', (40, 70, 110)), ('external', (30, 95, 65))):
            rgb = np.full((224, 224, 3), color, np.uint8)
            cv2.circle(rgb, (112, 112), 30, (220, 170, 40), -1)
            cv2.putText(rgb, 'SIMULATED', (20, 30), cv2.FONT_HERSHEY_SIMPLEX, .5, (255, 255, 255), 1)
            buffer.put(key, rgb, time.time(), now)
        buffer.put('eef', backend.pose.copy(), time.time(), now)
        buffer.put('gripper', max(.035, backend.gripper_target), time.time(), now)
        buffer.put('robot', dict(q=np.zeros(7), dq=np.zeros(7), wrench=np.zeros(6), mode=2, unsafe=False),
                   time.time(), now)
        stop.wait(1/50)


class DemoBackend:
    """In-process simulation; never imports a hardware driver or publishes ROS."""
    def __init__(self, buffer):
        self.buffer = buffer
        self.pose = np.array([.45, 0., .3, 1., 0., 0., 0.])
        self.gripper_target, self.gripper_mode = 0., 0
        self.gripper_busy, self.active = False, False
        self.command_error, self.has_lock = '', False
        self.commands = []

    def check_ready(self, require_lock=True):
        if self.command_error:
            raise ValueError(self.command_error)

    def acquire(self):
        return None

    def acquired(self):
        self.has_lock = True

    def activate(self):
        self.active = True

    def publish(self, pose):
        if not self.active:
            raise RuntimeError('Demo backend is inactive')
        self.pose = np.asarray(pose).copy()
        self.commands.append(self.pose.copy())

    def send_gripper(self, target):
        self.gripper_target = 0. if target <= .01 else float(target)
        self.gripper_mode = 2 if self.gripper_target == 0 else 1

    def cancel(self):
        pass

    def release(self):
        self.active, self.has_lock = False, False

    def deactivate(self):
        self.active = False
