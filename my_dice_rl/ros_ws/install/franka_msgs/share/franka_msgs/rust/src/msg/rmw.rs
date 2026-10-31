#[cfg(feature = "serde")]
use serde::{Deserialize, Serialize};


#[link(name = "franka_msgs__rosidl_typesupport_c")]
extern "C" {
    fn rosidl_typesupport_c__get_message_type_support_handle__franka_msgs__msg__Errors() -> *const std::ffi::c_void;
}

#[link(name = "franka_msgs__rosidl_generator_c")]
extern "C" {
    fn franka_msgs__msg__Errors__init(msg: *mut Errors) -> bool;
    fn franka_msgs__msg__Errors__Sequence__init(seq: *mut rosidl_runtime_rs::Sequence<Errors>, size: usize) -> bool;
    fn franka_msgs__msg__Errors__Sequence__fini(seq: *mut rosidl_runtime_rs::Sequence<Errors>);
    fn franka_msgs__msg__Errors__Sequence__copy(in_seq: &rosidl_runtime_rs::Sequence<Errors>, out_seq: *mut rosidl_runtime_rs::Sequence<Errors>) -> bool;
}

// Corresponds to franka_msgs__msg__Errors
#[cfg_attr(feature = "serde", derive(Deserialize, Serialize))]


// This struct is not documented.
#[allow(missing_docs)]

#[repr(C)]
#[derive(Clone, Debug, PartialEq, PartialOrd)]
pub struct Errors {

    // This member is not documented.
    #[allow(missing_docs)]
    pub joint_position_limits_violation: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub cartesian_position_limits_violation: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub self_collision_avoidance_violation: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub joint_velocity_violation: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub cartesian_velocity_violation: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub force_control_safety_violation: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub joint_reflex: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub cartesian_reflex: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub max_goal_pose_deviation_violation: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub max_path_pose_deviation_violation: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub cartesian_velocity_profile_safety_violation: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub joint_position_motion_generator_start_pose_invalid: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub joint_motion_generator_position_limits_violation: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub joint_motion_generator_velocity_limits_violation: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub joint_motion_generator_velocity_discontinuity: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub joint_motion_generator_acceleration_discontinuity: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub cartesian_position_motion_generator_start_pose_invalid: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub cartesian_motion_generator_elbow_limit_violation: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub cartesian_motion_generator_velocity_limits_violation: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub cartesian_motion_generator_velocity_discontinuity: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub cartesian_motion_generator_acceleration_discontinuity: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub cartesian_motion_generator_elbow_sign_inconsistent: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub cartesian_motion_generator_start_elbow_invalid: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub cartesian_motion_generator_joint_position_limits_violation: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub cartesian_motion_generator_joint_velocity_limits_violation: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub cartesian_motion_generator_joint_velocity_discontinuity: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub cartesian_motion_generator_joint_acceleration_discontinuity: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub cartesian_position_motion_generator_invalid_frame: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub force_controller_desired_force_tolerance_violation: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub controller_torque_discontinuity: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub start_elbow_sign_inconsistent: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub communication_constraints_violation: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub power_limit_violation: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub joint_p2p_insufficient_torque_for_planning: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub tau_j_range_violation: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub instability_detected: bool,

}



impl Default for Errors {
  fn default() -> Self {
    unsafe {
      let mut msg = std::mem::zeroed();
      if !franka_msgs__msg__Errors__init(&mut msg as *mut _) {
        panic!("Call to franka_msgs__msg__Errors__init() failed");
      }
      msg
    }
  }
}

impl rosidl_runtime_rs::SequenceAlloc for Errors {
  fn sequence_init(seq: &mut rosidl_runtime_rs::Sequence<Self>, size: usize) -> bool {
    // SAFETY: This is safe since the pointer is guaranteed to be valid/initialized.
    unsafe { franka_msgs__msg__Errors__Sequence__init(seq as *mut _, size) }
  }
  fn sequence_fini(seq: &mut rosidl_runtime_rs::Sequence<Self>) {
    // SAFETY: This is safe since the pointer is guaranteed to be valid/initialized.
    unsafe { franka_msgs__msg__Errors__Sequence__fini(seq as *mut _) }
  }
  fn sequence_copy(in_seq: &rosidl_runtime_rs::Sequence<Self>, out_seq: &mut rosidl_runtime_rs::Sequence<Self>) -> bool {
    // SAFETY: This is safe since the pointer is guaranteed to be valid/initialized.
    unsafe { franka_msgs__msg__Errors__Sequence__copy(in_seq, out_seq as *mut _) }
  }
}

impl rosidl_runtime_rs::Message for Errors {
  type RmwMsg = Self;
  fn into_rmw_message(msg_cow: std::borrow::Cow<'_, Self>) -> std::borrow::Cow<'_, Self::RmwMsg> { msg_cow }
  fn from_rmw_message(msg: Self::RmwMsg) -> Self { msg }
}

impl rosidl_runtime_rs::RmwMessage for Errors where Self: Sized {
  const TYPE_NAME: &'static str = "franka_msgs/msg/Errors";
  fn get_type_support() -> *const std::ffi::c_void {
    // SAFETY: No preconditions for this function.
    unsafe { rosidl_typesupport_c__get_message_type_support_handle__franka_msgs__msg__Errors() }
  }
}


#[link(name = "franka_msgs__rosidl_typesupport_c")]
extern "C" {
    fn rosidl_typesupport_c__get_message_type_support_handle__franka_msgs__msg__GraspEpsilon() -> *const std::ffi::c_void;
}

#[link(name = "franka_msgs__rosidl_generator_c")]
extern "C" {
    fn franka_msgs__msg__GraspEpsilon__init(msg: *mut GraspEpsilon) -> bool;
    fn franka_msgs__msg__GraspEpsilon__Sequence__init(seq: *mut rosidl_runtime_rs::Sequence<GraspEpsilon>, size: usize) -> bool;
    fn franka_msgs__msg__GraspEpsilon__Sequence__fini(seq: *mut rosidl_runtime_rs::Sequence<GraspEpsilon>);
    fn franka_msgs__msg__GraspEpsilon__Sequence__copy(in_seq: &rosidl_runtime_rs::Sequence<GraspEpsilon>, out_seq: *mut rosidl_runtime_rs::Sequence<GraspEpsilon>) -> bool;
}

// Corresponds to franka_msgs__msg__GraspEpsilon
#[cfg_attr(feature = "serde", derive(Deserialize, Serialize))]

/// Maximum tolerated deviation when the actual grasped width
/// is smaller than the commanded grasp width. Unit:

#[repr(C)]
#[derive(Clone, Debug, PartialEq, PartialOrd)]
pub struct GraspEpsilon {

    // This member is not documented.
    #[allow(missing_docs)]
    pub inner: f64,

    /// Maximum tolerated deviation when the actual grasped width
    /// is larger than the commanded grasp width. Unit:
    pub outer: f64,

}



impl Default for GraspEpsilon {
  fn default() -> Self {
    unsafe {
      let mut msg = std::mem::zeroed();
      if !franka_msgs__msg__GraspEpsilon__init(&mut msg as *mut _) {
        panic!("Call to franka_msgs__msg__GraspEpsilon__init() failed");
      }
      msg
    }
  }
}

impl rosidl_runtime_rs::SequenceAlloc for GraspEpsilon {
  fn sequence_init(seq: &mut rosidl_runtime_rs::Sequence<Self>, size: usize) -> bool {
    // SAFETY: This is safe since the pointer is guaranteed to be valid/initialized.
    unsafe { franka_msgs__msg__GraspEpsilon__Sequence__init(seq as *mut _, size) }
  }
  fn sequence_fini(seq: &mut rosidl_runtime_rs::Sequence<Self>) {
    // SAFETY: This is safe since the pointer is guaranteed to be valid/initialized.
    unsafe { franka_msgs__msg__GraspEpsilon__Sequence__fini(seq as *mut _) }
  }
  fn sequence_copy(in_seq: &rosidl_runtime_rs::Sequence<Self>, out_seq: &mut rosidl_runtime_rs::Sequence<Self>) -> bool {
    // SAFETY: This is safe since the pointer is guaranteed to be valid/initialized.
    unsafe { franka_msgs__msg__GraspEpsilon__Sequence__copy(in_seq, out_seq as *mut _) }
  }
}

impl rosidl_runtime_rs::Message for GraspEpsilon {
  type RmwMsg = Self;
  fn into_rmw_message(msg_cow: std::borrow::Cow<'_, Self>) -> std::borrow::Cow<'_, Self::RmwMsg> { msg_cow }
  fn from_rmw_message(msg: Self::RmwMsg) -> Self { msg }
}

impl rosidl_runtime_rs::RmwMessage for GraspEpsilon where Self: Sized {
  const TYPE_NAME: &'static str = "franka_msgs/msg/GraspEpsilon";
  fn get_type_support() -> *const std::ffi::c_void {
    // SAFETY: No preconditions for this function.
    unsafe { rosidl_typesupport_c__get_message_type_support_handle__franka_msgs__msg__GraspEpsilon() }
  }
}


#[link(name = "franka_msgs__rosidl_typesupport_c")]
extern "C" {
    fn rosidl_typesupport_c__get_message_type_support_handle__franka_msgs__msg__FrankaRobotState() -> *const std::ffi::c_void;
}

#[link(name = "franka_msgs__rosidl_generator_c")]
extern "C" {
    fn franka_msgs__msg__FrankaRobotState__init(msg: *mut FrankaRobotState) -> bool;
    fn franka_msgs__msg__FrankaRobotState__Sequence__init(seq: *mut rosidl_runtime_rs::Sequence<FrankaRobotState>, size: usize) -> bool;
    fn franka_msgs__msg__FrankaRobotState__Sequence__fini(seq: *mut rosidl_runtime_rs::Sequence<FrankaRobotState>);
    fn franka_msgs__msg__FrankaRobotState__Sequence__copy(in_seq: &rosidl_runtime_rs::Sequence<FrankaRobotState>, out_seq: *mut rosidl_runtime_rs::Sequence<FrankaRobotState>) -> bool;
}

// Corresponds to franka_msgs__msg__FrankaRobotState
#[cfg_attr(feature = "serde", derive(Deserialize, Serialize))]


// This struct is not documented.
#[allow(missing_docs)]

#[repr(C)]
#[derive(Clone, Debug, PartialEq, PartialOrd)]
pub struct FrankaRobotState {

    // This member is not documented.
    #[allow(missing_docs)]
    pub header: std_msgs::msg::rmw::Header,


    // This member is not documented.
    #[allow(missing_docs)]
    pub cartesian_collision: [f64; 6],


    // This member is not documented.
    #[allow(missing_docs)]
    pub cartesian_contact: [f64; 6],


    // This member is not documented.
    #[allow(missing_docs)]
    pub q: [f64; 7],


    // This member is not documented.
    #[allow(missing_docs)]
    pub q_d: [f64; 7],


    // This member is not documented.
    #[allow(missing_docs)]
    pub dq: [f64; 7],


    // This member is not documented.
    #[allow(missing_docs)]
    pub dq_d: [f64; 7],


    // This member is not documented.
    #[allow(missing_docs)]
    pub ddq_d: [f64; 7],


    // This member is not documented.
    #[allow(missing_docs)]
    pub theta: [f64; 7],


    // This member is not documented.
    #[allow(missing_docs)]
    pub dtheta: [f64; 7],


    // This member is not documented.
    #[allow(missing_docs)]
    pub tau_j: [f64; 7],


    // This member is not documented.
    #[allow(missing_docs)]
    pub dtau_j: [f64; 7],


    // This member is not documented.
    #[allow(missing_docs)]
    pub tau_j_d: [f64; 7],


    // This member is not documented.
    #[allow(missing_docs)]
    pub k_f_ext_hat_k: [f64; 6],


    // This member is not documented.
    #[allow(missing_docs)]
    pub elbow: [f64; 2],


    // This member is not documented.
    #[allow(missing_docs)]
    pub elbow_d: [f64; 2],


    // This member is not documented.
    #[allow(missing_docs)]
    pub elbow_c: [f64; 2],


    // This member is not documented.
    #[allow(missing_docs)]
    pub delbow_c: [f64; 2],


    // This member is not documented.
    #[allow(missing_docs)]
    pub ddelbow_c: [f64; 2],


    // This member is not documented.
    #[allow(missing_docs)]
    pub joint_collision: [f64; 7],


    // This member is not documented.
    #[allow(missing_docs)]
    pub joint_contact: [f64; 7],


    // This member is not documented.
    #[allow(missing_docs)]
    pub o_f_ext_hat_k: [f64; 6],


    // This member is not documented.
    #[allow(missing_docs)]
    pub o_dp_ee_d: [f64; 6],


    // This member is not documented.
    #[allow(missing_docs)]
    pub o_dp_ee_c: [f64; 6],


    // This member is not documented.
    #[allow(missing_docs)]
    pub o_ddp_ee_c: [f64; 6],


    // This member is not documented.
    #[allow(missing_docs)]
    pub tau_ext_hat_filtered: [f64; 7],


    // This member is not documented.
    #[allow(missing_docs)]
    pub m_ee: f64,


    // This member is not documented.
    #[allow(missing_docs)]
    pub f_x_cee: [f64; 3],


    // This member is not documented.
    #[allow(missing_docs)]
    pub i_ee: [f64; 9],


    // This member is not documented.
    #[allow(missing_docs)]
    pub m_load: f64,


    // This member is not documented.
    #[allow(missing_docs)]
    pub f_x_cload: [f64; 3],


    // This member is not documented.
    #[allow(missing_docs)]
    pub i_load: [f64; 9],


    // This member is not documented.
    #[allow(missing_docs)]
    pub m_total: f64,


    // This member is not documented.
    #[allow(missing_docs)]
    pub f_x_ctotal: [f64; 3],


    // This member is not documented.
    #[allow(missing_docs)]
    pub i_total: [f64; 9],


    // This member is not documented.
    #[allow(missing_docs)]
    pub o_t_ee: [f64; 16],


    // This member is not documented.
    #[allow(missing_docs)]
    pub o_t_ee_d: [f64; 16],


    // This member is not documented.
    #[allow(missing_docs)]
    pub o_t_ee_c: [f64; 16],


    // This member is not documented.
    #[allow(missing_docs)]
    pub f_t_ee: [f64; 16],


    // This member is not documented.
    #[allow(missing_docs)]
    pub ee_t_k: [f64; 16],


    // This member is not documented.
    #[allow(missing_docs)]
    pub time: f64,


    // This member is not documented.
    #[allow(missing_docs)]
    pub control_command_success_rate: f64,


    // This member is not documented.
    #[allow(missing_docs)]
    pub robot_mode: u8,


    // This member is not documented.
    #[allow(missing_docs)]
    pub current_errors: super::super::msg::rmw::Errors,


    // This member is not documented.
    #[allow(missing_docs)]
    pub last_motion_errors: super::super::msg::rmw::Errors,

}

impl FrankaRobotState {

    // This constant is not documented.
    #[allow(missing_docs)]
    pub const ROBOT_MODE_OTHER: u8 = 0;


    // This constant is not documented.
    #[allow(missing_docs)]
    pub const ROBOT_MODE_IDLE: u8 = 1;


    // This constant is not documented.
    #[allow(missing_docs)]
    pub const ROBOT_MODE_MOVE: u8 = 2;


    // This constant is not documented.
    #[allow(missing_docs)]
    pub const ROBOT_MODE_GUIDING: u8 = 3;


    // This constant is not documented.
    #[allow(missing_docs)]
    pub const ROBOT_MODE_REFLEX: u8 = 4;


    // This constant is not documented.
    #[allow(missing_docs)]
    pub const ROBOT_MODE_USER_STOPPED: u8 = 5;


    // This constant is not documented.
    #[allow(missing_docs)]
    pub const ROBOT_MODE_AUTOMATIC_ERROR_RECOVERY: u8 = 6;

}


impl Default for FrankaRobotState {
  fn default() -> Self {
    unsafe {
      let mut msg = std::mem::zeroed();
      if !franka_msgs__msg__FrankaRobotState__init(&mut msg as *mut _) {
        panic!("Call to franka_msgs__msg__FrankaRobotState__init() failed");
      }
      msg
    }
  }
}

impl rosidl_runtime_rs::SequenceAlloc for FrankaRobotState {
  fn sequence_init(seq: &mut rosidl_runtime_rs::Sequence<Self>, size: usize) -> bool {
    // SAFETY: This is safe since the pointer is guaranteed to be valid/initialized.
    unsafe { franka_msgs__msg__FrankaRobotState__Sequence__init(seq as *mut _, size) }
  }
  fn sequence_fini(seq: &mut rosidl_runtime_rs::Sequence<Self>) {
    // SAFETY: This is safe since the pointer is guaranteed to be valid/initialized.
    unsafe { franka_msgs__msg__FrankaRobotState__Sequence__fini(seq as *mut _) }
  }
  fn sequence_copy(in_seq: &rosidl_runtime_rs::Sequence<Self>, out_seq: &mut rosidl_runtime_rs::Sequence<Self>) -> bool {
    // SAFETY: This is safe since the pointer is guaranteed to be valid/initialized.
    unsafe { franka_msgs__msg__FrankaRobotState__Sequence__copy(in_seq, out_seq as *mut _) }
  }
}

impl rosidl_runtime_rs::Message for FrankaRobotState {
  type RmwMsg = Self;
  fn into_rmw_message(msg_cow: std::borrow::Cow<'_, Self>) -> std::borrow::Cow<'_, Self::RmwMsg> { msg_cow }
  fn from_rmw_message(msg: Self::RmwMsg) -> Self { msg }
}

impl rosidl_runtime_rs::RmwMessage for FrankaRobotState where Self: Sized {
  const TYPE_NAME: &'static str = "franka_msgs/msg/FrankaRobotState";
  fn get_type_support() -> *const std::ffi::c_void {
    // SAFETY: No preconditions for this function.
    unsafe { rosidl_typesupport_c__get_message_type_support_handle__franka_msgs__msg__FrankaRobotState() }
  }
}


