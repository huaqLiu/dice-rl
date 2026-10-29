import json
import time

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from rclpy.time import Time
from geometry_msgs.msg import PoseStamped
from sensor_msgs.msg import CameraInfo, Image, JointState
from std_msgs.msg import String
from std_srvs.srv import SetBool, Trigger
from franka_msgs.msg import FrankaRobotState
from tf2_ros import Buffer, TransformListener


def stamp_seconds(stamp):
    return stamp.sec + stamp.nanosec * 1e-9


def decode_image(message):
    channels = {"rgb8": 3, "bgr8": 3, "rgba8": 4, "bgra8": 4}
    if message.encoding not in channels:
        raise ValueError(f"不支持图像编码 {message.encoding}")
    c = channels[message.encoding]
    rows = np.frombuffer(message.data, dtype=np.uint8).reshape(message.height, message.step)
    rgb = rows[:, :message.width * c].reshape(message.height, message.width, c)[..., :3]
    if message.encoding.startswith("bgr"):
        rgb = rgb[..., ::-1]
    return rgb.copy()


class CollectorNode(Node):
    """Read sensors; GUI can request teleop enable/pause or register a held gear."""
    def __init__(self, config, buffers):
        super().__init__("dice_data_collector")
        self.config, self.buffers = config, buffers
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.enable_client = self.create_client(SetBool, config['teleop_enable_service'])
        self.hold_client = self.create_client(Trigger, config['register_hold_service'])
        self.control_message = ""
        for key in ("wrist", "external"):
            topic = config[f"{key}_topic"]
            self.create_subscription(Image, topic,
                lambda m, k=key: self.guarded(k, self.image, m, k), qos_profile_sensor_data)
            self.create_subscription(CameraInfo, topic.rsplit("/", 1)[0] + "/camera_info",
                lambda m, k=key: self.camera_info(m, k), qos_profile_sensor_data)
        self.create_subscription(PoseStamped, config["target_topic"],
            lambda m: self.guarded("target", self.target, m), qos_profile_sensor_data)
        self.create_subscription(JointState, config["gripper_topic"],
            lambda m: self.guarded("gripper", self.gripper, m), qos_profile_sensor_data)
        self.create_subscription(FrankaRobotState, config["robot_state_topic"],
            lambda m: self.guarded("robot", self.robot, m), qos_profile_sensor_data)
        self.create_subscription(String, config["teleop_topic"],
            lambda m: self.guarded("teleop", self.teleop, m), 10)
        self.create_timer(.02, lambda: self.guarded("eef", self.eef))

    def guarded(self, key, fn, *args):
        try:
            fn(*args)
        except Exception as exc:
            self.buffers.error(key, str(exc))

    def fresh(self, stamp):
        source = stamp_seconds(stamp)
        age = self.get_clock().now().nanoseconds * 1e-9 - source
        if source <= 0 or not -.1 <= age <= self.config["max_age_s"]:
            raise ValueError(f"源时间戳异常或过旧 ({age:.3f}s)")
        return source

    def image(self, message, key):
        received = time.monotonic()
        source = self.fresh(message.header.stamp)
        self.buffers.put(key, decode_image(message), source, received)

    def camera_info(self, m, key):
        with self.buffers.lock:
            self.buffers.camera_info[key] = dict(width=m.width, height=m.height,
                k=list(m.k), d=list(m.d), p=list(m.p), distortion_model=m.distortion_model,
                frame_id=m.header.frame_id)

    def target(self, m):
        source = self.fresh(m.header.stamp)
        if m.header.frame_id != self.config["base_frame"]:
            raise ValueError("目标位姿坐标系不匹配")
        p, q = m.pose.position, m.pose.orientation
        self.buffers.put("target", [p.x, p.y, p.z, q.x, q.y, q.z, q.w], source)

    def gripper(self, m):
        source = self.fresh(m.header.stamp)
        positions = dict(zip(m.name, m.position))
        width = sum(positions[n] for n in self.config["gripper_joint_names"])
        if not np.isfinite(width) or not 0 <= width <= .085:
            raise ValueError("夹爪开度无效")
        self.buffers.put("gripper", width, source)

    def robot(self, m):
        source = self.fresh(m.header.stamp)
        q, dq = np.asarray(m.q), np.asarray(m.dq)
        if q.shape != (7,) or dq.shape != (7,) or not np.all(np.isfinite([q, dq])):
            raise ValueError("关节状态无效")
        wrench = np.asarray(m.o_f_ext_hat_k, dtype=np.float64)
        if wrench.shape != (6,) or not np.all(np.isfinite(wrench)):
            raise ValueError("外力估计无效")
        self.buffers.put("robot", dict(q=q, dq=dq, wrench=wrench), source)

    def request_control(self, action):
        client = self.hold_client if action == 'hold' else self.enable_client
        if not client.service_is_ready():
            self.control_message = "遥操作服务未就绪，请先启动 teleop"
            return
        request = Trigger.Request() if action == 'hold' else SetBool.Request(data=action == 'enable')
        future = client.call_async(request)
        self.control_message = "等待遥操作响应……"
        def complete(result):
            try:
                response = result.result()
                self.control_message = ('✓ ' if response.success else '✗ ') + response.message
            except Exception as exc:
                self.control_message = str(exc)
        future.add_done_callback(complete)

    def teleop(self, m):
        value = json.loads(m.data)
        for name in ("base_frame", "ee_frame"):
            if value[name] != self.config[name]:
                raise ValueError(f"遥操作 {name} 与采集配置不匹配")
        age = self.get_clock().now().nanoseconds * 1e-9 - value["stamp"]
        if not -.1 <= age <= self.config["max_age_s"]:
            raise ValueError("遥操作状态时间戳过旧")
        if value.get("gripper_known"):
            width = value["gripper_target_width"]
            if not np.isfinite(width) or not 0 <= width <= .08:
                raise ValueError("夹爪目标开度无效")
        self.buffers.put("teleop", value, value["stamp"])

    def eef(self):
        t = self.tf_buffer.lookup_transform(self.config["base_frame"], self.config["ee_frame"], Time())
        source = self.fresh(t.header.stamp)
        p, q = t.transform.translation, t.transform.rotation
        self.buffers.put("eef", [p.x, p.y, p.z, q.x, q.y, q.z, q.w], source)
