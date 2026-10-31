#[cfg(feature = "serde")]
use serde::{Deserialize, Serialize};



// Corresponds to franka_msgs__msg__Errors

// This struct is not documented.
#[allow(missing_docs)]

#[cfg_attr(feature = "serde", derive(Deserialize, Serialize))]
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
    <Self as rosidl_runtime_rs::Message>::from_rmw_message(super::msg::rmw::Errors::default())
  }
}

impl rosidl_runtime_rs::Message for Errors {
  type RmwMsg = super::msg::rmw::Errors;

  fn into_rmw_message(msg_cow: std::borrow::Cow<'_, Self>) -> std::borrow::Cow<'_, Self::RmwMsg> {
    match msg_cow {
      std::borrow::Cow::Owned(msg) => std::borrow::Cow::Owned(Self::RmwMsg {
        joint_position_limits_violation: msg.joint_position_limits_violation,
        cartesian_position_limits_violation: msg.cartesian_position_limits_violation,
        self_collision_avoidance_violation: msg.self_collision_avoidance_violation,
        joint_velocity_violation: msg.joint_velocity_violation,
        cartesian_velocity_violation: msg.cartesian_velocity_violation,
        force_control_safety_violation: msg.force_control_safety_violation,
        joint_reflex: msg.joint_reflex,
        cartesian_reflex: msg.cartesian_reflex,
        max_goal_pose_deviation_violation: msg.max_goal_pose_deviation_violation,
        max_path_pose_deviation_violation: msg.max_path_pose_deviation_violation,
        cartesian_velocity_profile_safety_violation: msg.cartesian_velocity_profile_safety_violation,
        joint_position_motion_generator_start_pose_invalid: msg.joint_position_motion_generator_start_pose_invalid,
        joint_motion_generator_position_limits_violation: msg.joint_motion_generator_position_limits_violation,
        joint_motion_generator_velocity_limits_violation: msg.joint_motion_generator_velocity_limits_violation,
        joint_motion_generator_velocity_discontinuity: msg.joint_motion_generator_velocity_discontinuity,
        joint_motion_generator_acceleration_discontinuity: msg.joint_motion_generator_acceleration_discontinuity,
        cartesian_position_motion_generator_start_pose_invalid: msg.cartesian_position_motion_generator_start_pose_invalid,
        cartesian_motion_generator_elbow_limit_violation: msg.cartesian_motion_generator_elbow_limit_violation,
        cartesian_motion_generator_velocity_limits_violation: msg.cartesian_motion_generator_velocity_limits_violation,
        cartesian_motion_generator_velocity_discontinuity: msg.cartesian_motion_generator_velocity_discontinuity,
        cartesian_motion_generator_acceleration_discontinuity: msg.cartesian_motion_generator_acceleration_discontinuity,
        cartesian_motion_generator_elbow_sign_inconsistent: msg.cartesian_motion_generator_elbow_sign_inconsistent,
        cartesian_motion_generator_start_elbow_invalid: msg.cartesian_motion_generator_start_elbow_invalid,
        cartesian_motion_generator_joint_position_limits_violation: msg.cartesian_motion_generator_joint_position_limits_violation,
        cartesian_motion_generator_joint_velocity_limits_violation: msg.cartesian_motion_generator_joint_velocity_limits_violation,
        cartesian_motion_generator_joint_velocity_discontinuity: msg.cartesian_motion_generator_joint_velocity_discontinuity,
        cartesian_motion_generator_joint_acceleration_discontinuity: msg.cartesian_motion_generator_joint_acceleration_discontinuity,
        cartesian_position_motion_generator_invalid_frame: msg.cartesian_position_motion_generator_invalid_frame,
        force_controller_desired_force_tolerance_violation: msg.force_controller_desired_force_tolerance_violation,
        controller_torque_discontinuity: msg.controller_torque_discontinuity,
        start_elbow_sign_inconsistent: msg.start_elbow_sign_inconsistent,
        communication_constraints_violation: msg.communication_constraints_violation,
        power_limit_violation: msg.power_limit_violation,
        joint_p2p_insufficient_torque_for_planning: msg.joint_p2p_insufficient_torque_for_planning,
        tau_j_range_violation: msg.tau_j_range_violation,
        instability_detected: msg.instability_detected,
      }),
      std::borrow::Cow::Borrowed(msg) => std::borrow::Cow::Owned(Self::RmwMsg {
      joint_position_limits_violation: msg.joint_position_limits_violation,
      cartesian_position_limits_violation: msg.cartesian_position_limits_violation,
      self_collision_avoidance_violation: msg.self_collision_avoidance_violation,
      joint_velocity_violation: msg.joint_velocity_violation,
      cartesian_velocity_violation: msg.cartesian_velocity_violation,
      force_control_safety_violation: msg.force_control_safety_violation,
      joint_reflex: msg.joint_reflex,
      cartesian_reflex: msg.cartesian_reflex,
      max_goal_pose_deviation_violation: msg.max_goal_pose_deviation_violation,
      max_path_pose_deviation_violation: msg.max_path_pose_deviation_violation,
      cartesian_velocity_profile_safety_violation: msg.cartesian_velocity_profile_safety_violation,
      joint_position_motion_generator_start_pose_invalid: msg.joint_position_motion_generator_start_pose_invalid,
      joint_motion_generator_position_limits_violation: msg.joint_motion_generator_position_limits_violation,
      joint_motion_generator_velocity_limits_violation: msg.joint_motion_generator_velocity_limits_violation,
      joint_motion_generator_velocity_discontinuity: msg.joint_motion_generator_velocity_discontinuity,
      joint_motion_generator_acceleration_discontinuity: msg.joint_motion_generator_acceleration_discontinuity,
      cartesian_position_motion_generator_start_pose_invalid: msg.cartesian_position_motion_generator_start_pose_invalid,
      cartesian_motion_generator_elbow_limit_violation: msg.cartesian_motion_generator_elbow_limit_violation,
      cartesian_motion_generator_velocity_limits_violation: msg.cartesian_motion_generator_velocity_limits_violation,
      cartesian_motion_generator_velocity_discontinuity: msg.cartesian_motion_generator_velocity_discontinuity,
      cartesian_motion_generator_acceleration_discontinuity: msg.cartesian_motion_generator_acceleration_discontinuity,
      cartesian_motion_generator_elbow_sign_inconsistent: msg.cartesian_motion_generator_elbow_sign_inconsistent,
      cartesian_motion_generator_start_elbow_invalid: msg.cartesian_motion_generator_start_elbow_invalid,
      cartesian_motion_generator_joint_position_limits_violation: msg.cartesian_motion_generator_joint_position_limits_violation,
      cartesian_motion_generator_joint_velocity_limits_violation: msg.cartesian_motion_generator_joint_velocity_limits_violation,
      cartesian_motion_generator_joint_velocity_discontinuity: msg.cartesian_motion_generator_joint_velocity_discontinuity,
      cartesian_motion_generator_joint_acceleration_discontinuity: msg.cartesian_motion_generator_joint_acceleration_discontinuity,
      cartesian_position_motion_generator_invalid_frame: msg.cartesian_position_motion_generator_invalid_frame,
      force_controller_desired_force_tolerance_violation: msg.force_controller_desired_force_tolerance_violation,
      controller_torque_discontinuity: msg.controller_torque_discontinuity,
      start_elbow_sign_inconsistent: msg.start_elbow_sign_inconsistent,
      communication_constraints_violation: msg.communication_constraints_violation,
      power_limit_violation: msg.power_limit_violation,
      joint_p2p_insufficient_torque_for_planning: msg.joint_p2p_insufficient_torque_for_planning,
      tau_j_range_violation: msg.tau_j_range_violation,
      instability_detected: msg.instability_detected,
      })
    }
  }

  fn from_rmw_message(msg: Self::RmwMsg) -> Self {
    Self {
      joint_position_limits_violation: msg.joint_position_limits_violation,
      cartesian_position_limits_violation: msg.cartesian_position_limits_violation,
      self_collision_avoidance_violation: msg.self_collision_avoidance_violation,
      joint_velocity_violation: msg.joint_velocity_violation,
      cartesian_velocity_violation: msg.cartesian_velocity_violation,
      force_control_safety_violation: msg.force_control_safety_violation,
      joint_reflex: msg.joint_reflex,
      cartesian_reflex: msg.cartesian_reflex,
      max_goal_pose_deviation_violation: msg.max_goal_pose_deviation_violation,
      max_path_pose_deviation_violation: msg.max_path_pose_deviation_violation,
      cartesian_velocity_profile_safety_violation: msg.cartesian_velocity_profile_safety_violation,
      joint_position_motion_generator_start_pose_invalid: msg.joint_position_motion_generator_start_pose_invalid,
      joint_motion_generator_position_limits_violation: msg.joint_motion_generator_position_limits_violation,
      joint_motion_generator_velocity_limits_violation: msg.joint_motion_generator_velocity_limits_violation,
      joint_motion_generator_velocity_discontinuity: msg.joint_motion_generator_velocity_discontinuity,
      joint_motion_generator_acceleration_discontinuity: msg.joint_motion_generator_acceleration_discontinuity,
      cartesian_position_motion_generator_start_pose_invalid: msg.cartesian_position_motion_generator_start_pose_invalid,
      cartesian_motion_generator_elbow_limit_violation: msg.cartesian_motion_generator_elbow_limit_violation,
      cartesian_motion_generator_velocity_limits_violation: msg.cartesian_motion_generator_velocity_limits_violation,
      cartesian_motion_generator_velocity_discontinuity: msg.cartesian_motion_generator_velocity_discontinuity,
      cartesian_motion_generator_acceleration_discontinuity: msg.cartesian_motion_generator_acceleration_discontinuity,
      cartesian_motion_generator_elbow_sign_inconsistent: msg.cartesian_motion_generator_elbow_sign_inconsistent,
      cartesian_motion_generator_start_elbow_invalid: msg.cartesian_motion_generator_start_elbow_invalid,
      cartesian_motion_generator_joint_position_limits_violation: msg.cartesian_motion_generator_joint_position_limits_violation,
      cartesian_motion_generator_joint_velocity_limits_violation: msg.cartesian_motion_generator_joint_velocity_limits_violation,
      cartesian_motion_generator_joint_velocity_discontinuity: msg.cartesian_motion_generator_joint_velocity_discontinuity,
      cartesian_motion_generator_joint_acceleration_discontinuity: msg.cartesian_motion_generator_joint_acceleration_discontinuity,
      cartesian_position_motion_generator_invalid_frame: msg.cartesian_position_motion_generator_invalid_frame,
      force_controller_desired_force_tolerance_violation: msg.force_controller_desired_force_tolerance_violation,
      controller_torque_discontinuity: msg.controller_torque_discontinuity,
      start_elbow_sign_inconsistent: msg.start_elbow_sign_inconsistent,
      communication_constraints_violation: msg.communication_constraints_violation,
      power_limit_violation: msg.power_limit_violation,
      joint_p2p_insufficient_torque_for_planning: msg.joint_p2p_insufficient_torque_for_planning,
      tau_j_range_violation: msg.tau_j_range_violation,
      instability_detected: msg.instability_detected,
    }
  }
}


// Corresponds to franka_msgs__msg__GraspEpsilon
/// Maximum tolerated deviation when the actual grasped width
/// is smaller than the commanded grasp width. Unit:

#[cfg_attr(feature = "serde", derive(Deserialize, Serialize))]
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
    <Self as rosidl_runtime_rs::Message>::from_rmw_message(super::msg::rmw::GraspEpsilon::default())
  }
}

impl rosidl_runtime_rs::Message for GraspEpsilon {
  type RmwMsg = super::msg::rmw::GraspEpsilon;

  fn into_rmw_message(msg_cow: std::borrow::Cow<'_, Self>) -> std::borrow::Cow<'_, Self::RmwMsg> {
    match msg_cow {
      std::borrow::Cow::Owned(msg) => std::borrow::Cow::Owned(Self::RmwMsg {
        inner: msg.inner,
        outer: msg.outer,
      }),
      std::borrow::Cow::Borrowed(msg) => std::borrow::Cow::Owned(Self::RmwMsg {
      inner: msg.inner,
      outer: msg.outer,
      })
    }
  }

  fn from_rmw_message(msg: Self::RmwMsg) -> Self {
    Self {
      inner: msg.inner,
      outer: msg.outer,
    }
  }
}


// Corresponds to franka_msgs__msg__FrankaRobotState

// This struct is not documented.
#[allow(missing_docs)]

#[cfg_attr(feature = "serde", derive(Deserialize, Serialize))]
#[derive(Clone, Debug, PartialEq, PartialOrd)]
pub struct FrankaRobotState {

    // This member is not documented.
    #[allow(missing_docs)]
    pub header: std_msgs::msg::Header,


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
    pub current_errors: super::msg::Errors,


    // This member is not documented.
    #[allow(missing_docs)]
    pub last_motion_errors: super::msg::Errors,

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
    <Self as rosidl_runtime_rs::Message>::from_rmw_message(super::msg::rmw::FrankaRobotState::default())
  }
}

impl rosidl_runtime_rs::Message for FrankaRobotState {
  type RmwMsg = super::msg::rmw::FrankaRobotState;

  fn into_rmw_message(msg_cow: std::borrow::Cow<'_, Self>) -> std::borrow::Cow<'_, Self::RmwMsg> {
    match msg_cow {
      std::borrow::Cow::Owned(msg) => std::borrow::Cow::Owned(Self::RmwMsg {
        header: std_msgs::msg::Header::into_rmw_message(std::borrow::Cow::Owned(msg.header)).into_owned(),
        cartesian_collision: msg.cartesian_collision,
        cartesian_contact: msg.cartesian_contact,
        q: msg.q,
        q_d: msg.q_d,
        dq: msg.dq,
        dq_d: msg.dq_d,
        ddq_d: msg.ddq_d,
        theta: msg.theta,
        dtheta: msg.dtheta,
        tau_j: msg.tau_j,
        dtau_j: msg.dtau_j,
        tau_j_d: msg.tau_j_d,
        k_f_ext_hat_k: msg.k_f_ext_hat_k,
        elbow: msg.elbow,
        elbow_d: msg.elbow_d,
        elbow_c: msg.elbow_c,
        delbow_c: msg.delbow_c,
        ddelbow_c: msg.ddelbow_c,
        joint_collision: msg.joint_collision,
        joint_contact: msg.joint_contact,
        o_f_ext_hat_k: msg.o_f_ext_hat_k,
        o_dp_ee_d: msg.o_dp_ee_d,
        o_dp_ee_c: msg.o_dp_ee_c,
        o_ddp_ee_c: msg.o_ddp_ee_c,
        tau_ext_hat_filtered: msg.tau_ext_hat_filtered,
        m_ee: msg.m_ee,
        f_x_cee: msg.f_x_cee,
        i_ee: msg.i_ee,
        m_load: msg.m_load,
        f_x_cload: msg.f_x_cload,
        i_load: msg.i_load,
        m_total: msg.m_total,
        f_x_ctotal: msg.f_x_ctotal,
        i_total: msg.i_total,
        o_t_ee: msg.o_t_ee,
        o_t_ee_d: msg.o_t_ee_d,
        o_t_ee_c: msg.o_t_ee_c,
        f_t_ee: msg.f_t_ee,
        ee_t_k: msg.ee_t_k,
        time: msg.time,
        control_command_success_rate: msg.control_command_success_rate,
        robot_mode: msg.robot_mode,
        current_errors: super::msg::Errors::into_rmw_message(std::borrow::Cow::Owned(msg.current_errors)).into_owned(),
        last_motion_errors: super::msg::Errors::into_rmw_message(std::borrow::Cow::Owned(msg.last_motion_errors)).into_owned(),
      }),
      std::borrow::Cow::Borrowed(msg) => std::borrow::Cow::Owned(Self::RmwMsg {
        header: std_msgs::msg::Header::into_rmw_message(std::borrow::Cow::Borrowed(&msg.header)).into_owned(),
        cartesian_collision: msg.cartesian_collision,
        cartesian_contact: msg.cartesian_contact,
        q: msg.q,
        q_d: msg.q_d,
        dq: msg.dq,
        dq_d: msg.dq_d,
        ddq_d: msg.ddq_d,
        theta: msg.theta,
        dtheta: msg.dtheta,
        tau_j: msg.tau_j,
        dtau_j: msg.dtau_j,
        tau_j_d: msg.tau_j_d,
        k_f_ext_hat_k: msg.k_f_ext_hat_k,
        elbow: msg.elbow,
        elbow_d: msg.elbow_d,
        elbow_c: msg.elbow_c,
        delbow_c: msg.delbow_c,
        ddelbow_c: msg.ddelbow_c,
        joint_collision: msg.joint_collision,
        joint_contact: msg.joint_contact,
        o_f_ext_hat_k: msg.o_f_ext_hat_k,
        o_dp_ee_d: msg.o_dp_ee_d,
        o_dp_ee_c: msg.o_dp_ee_c,
        o_ddp_ee_c: msg.o_ddp_ee_c,
        tau_ext_hat_filtered: msg.tau_ext_hat_filtered,
      m_ee: msg.m_ee,
        f_x_cee: msg.f_x_cee,
        i_ee: msg.i_ee,
      m_load: msg.m_load,
        f_x_cload: msg.f_x_cload,
        i_load: msg.i_load,
      m_total: msg.m_total,
        f_x_ctotal: msg.f_x_ctotal,
        i_total: msg.i_total,
        o_t_ee: msg.o_t_ee,
        o_t_ee_d: msg.o_t_ee_d,
        o_t_ee_c: msg.o_t_ee_c,
        f_t_ee: msg.f_t_ee,
        ee_t_k: msg.ee_t_k,
      time: msg.time,
      control_command_success_rate: msg.control_command_success_rate,
      robot_mode: msg.robot_mode,
        current_errors: super::msg::Errors::into_rmw_message(std::borrow::Cow::Borrowed(&msg.current_errors)).into_owned(),
        last_motion_errors: super::msg::Errors::into_rmw_message(std::borrow::Cow::Borrowed(&msg.last_motion_errors)).into_owned(),
      })
    }
  }

  fn from_rmw_message(msg: Self::RmwMsg) -> Self {
    Self {
      header: std_msgs::msg::Header::from_rmw_message(msg.header),
      cartesian_collision: msg.cartesian_collision,
      cartesian_contact: msg.cartesian_contact,
      q: msg.q,
      q_d: msg.q_d,
      dq: msg.dq,
      dq_d: msg.dq_d,
      ddq_d: msg.ddq_d,
      theta: msg.theta,
      dtheta: msg.dtheta,
      tau_j: msg.tau_j,
      dtau_j: msg.dtau_j,
      tau_j_d: msg.tau_j_d,
      k_f_ext_hat_k: msg.k_f_ext_hat_k,
      elbow: msg.elbow,
      elbow_d: msg.elbow_d,
      elbow_c: msg.elbow_c,
      delbow_c: msg.delbow_c,
      ddelbow_c: msg.ddelbow_c,
      joint_collision: msg.joint_collision,
      joint_contact: msg.joint_contact,
      o_f_ext_hat_k: msg.o_f_ext_hat_k,
      o_dp_ee_d: msg.o_dp_ee_d,
      o_dp_ee_c: msg.o_dp_ee_c,
      o_ddp_ee_c: msg.o_ddp_ee_c,
      tau_ext_hat_filtered: msg.tau_ext_hat_filtered,
      m_ee: msg.m_ee,
      f_x_cee: msg.f_x_cee,
      i_ee: msg.i_ee,
      m_load: msg.m_load,
      f_x_cload: msg.f_x_cload,
      i_load: msg.i_load,
      m_total: msg.m_total,
      f_x_ctotal: msg.f_x_ctotal,
      i_total: msg.i_total,
      o_t_ee: msg.o_t_ee,
      o_t_ee_d: msg.o_t_ee_d,
      o_t_ee_c: msg.o_t_ee_c,
      f_t_ee: msg.f_t_ee,
      ee_t_k: msg.ee_t_k,
      time: msg.time,
      control_command_success_rate: msg.control_command_success_rate,
      robot_mode: msg.robot_mode,
      current_errors: super::msg::Errors::from_rmw_message(msg.current_errors),
      last_motion_errors: super::msg::Errors::from_rmw_message(msg.last_motion_errors),
    }
  }
}


