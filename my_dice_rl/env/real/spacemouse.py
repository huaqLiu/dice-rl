"""Read the Linux SpaceNavigator joystick without extra HID libraries.

This process publishes input only. It has no robot/gripper command publishers.
"""
import argparse
import glob
import os
import struct


class LinuxSpaceMouse:
    def __init__(self, path=None):
        if path is None:
            matches = sorted(glob.glob('/dev/input/by-id/*Space*-joystick'))
            matches = [p for p in matches if 'event-' not in p]
            if len(matches) != 1:
                raise RuntimeError('未找到唯一 SpaceMouse，请使用 --device 指定 /dev/input/jsN')
            path = matches[0]
        self.path = path
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        self.axes = [0.0] * 6
        self.buttons = [0, 0]

    def read(self):
        # Repeated publications allow an intentionally held input to remain fresh.
        # Unplugging gives ENODEV/EOF, closes this process, and stops publications.
        while True:
            try:
                data = os.read(self.fd, 8)
            except BlockingIOError:
                break
            if len(data) != 8:
                raise OSError('SpaceMouse disconnected or returned a partial event')
            _, value, kind, index = struct.unpack('=IhBB', data)
            kind &= ~0x80  # Initial state events also seed the device state.
            if kind == 2 and index < 6:
                self.axes[index] = max(-1.0, min(1.0, value / 32767.0))
            elif kind == 1 and index < 2:
                self.buttons[index] = int(bool(value))
        return self.axes.copy(), self.buttons.copy()

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--device')
    parser.add_argument('--read-once', action='store_true', help='Read input without ROS')
    args = parser.parse_args()
    device = LinuxSpaceMouse(args.device)
    if args.read_once:
        try:
            print(device.path, device.read())
        finally:
            device.close()
        return
    import rclpy
    from rclpy.node import Node
    from sensor_msgs.msg import Joy
    rclpy.init(args=[])
    node = Node('dice_spacemouse_input')
    publisher = node.create_publisher(Joy, '/dice_spacemouse/joy', 10)

    def publish():
        axes, buttons = device.read()
        message = Joy()
        message.header.stamp = node.get_clock().now().to_msg()
        message.axes, message.buttons = axes, buttons
        publisher.publish(message)

    node.create_timer(0.02, publish)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except Exception:
        if rclpy.ok():
            raise
    finally:
        device.close()
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
