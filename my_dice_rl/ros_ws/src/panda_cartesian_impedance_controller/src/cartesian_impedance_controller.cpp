#include "panda_cartesian_impedance_controller/cartesian_impedance_controller.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <memory>
#include <string>
#include <vector>

#include <Eigen/Core>
#include <Eigen/Dense>
#include <geometry_msgs/msg/pose_stamped.hpp>
#include <kdl_parser/kdl_parser.hpp>
#include <pluginlib/class_list_macros.hpp>

namespace panda_cartesian_impedance_controller
{

CartesianImpedanceController::CartesianImpedanceController()
: controller_interface::ControllerInterface()
{
}

controller_interface::CallbackReturn CartesianImpedanceController::on_init()
{
  try {
    auto_declare<std::vector<std::string>>("joints", std::vector<std::string>{});
    auto_declare<std::string>("base_link", "panda_link0");
    auto_declare<std::string>("ee_link", "panda_hand");
    auto_declare<std::string>("robot_description_param", "robot_description");

    auto_declare<std::vector<double>>("stiffness", std::vector<double>{200.0, 200.0, 200.0, 15.0, 15.0, 15.0});
    auto_declare<std::vector<double>>("damping", std::vector<double>{20.0, 20.0, 20.0, 3.0, 3.0, 3.0});

    auto_declare<double>("delta_tau_max", 1.0);
    auto_declare<bool>("use_current_pose_as_initial_target", true);
    auto_declare<std::string>("target_topic", "/cartesian_impedance_target");
  } catch (const std::exception & e) {
    RCLCPP_ERROR(get_node()->get_logger(), "on_init exception: %s", e.what());
    return controller_interface::CallbackReturn::ERROR;
  }

  return controller_interface::CallbackReturn::SUCCESS;
}

controller_interface::InterfaceConfiguration
CartesianImpedanceController::command_interface_configuration() const
{
  controller_interface::InterfaceConfiguration config;
  config.type = controller_interface::interface_configuration_type::INDIVIDUAL;

  for (const auto & joint : joint_names_) {
    config.names.push_back(joint + "/" + hardware_interface::HW_IF_EFFORT);
  }
  return config;
}

controller_interface::InterfaceConfiguration
CartesianImpedanceController::state_interface_configuration() const
{
  controller_interface::InterfaceConfiguration config;
  config.type = controller_interface::interface_configuration_type::INDIVIDUAL;

  for (const auto & joint : joint_names_) {
    config.names.push_back(joint + "/" + hardware_interface::HW_IF_POSITION);
    config.names.push_back(joint + "/" + hardware_interface::HW_IF_VELOCITY);
  }
  return config;
}

bool CartesianImpedanceController::read_parameters()
{
  joint_names_ = get_node()->get_parameter("joints").as_string_array();
  base_link_ = get_node()->get_parameter("base_link").as_string();
  ee_link_ = get_node()->get_parameter("ee_link").as_string();
  robot_description_param_ = get_node()->get_parameter("robot_description_param").as_string();
  delta_tau_max_ = get_node()->get_parameter("delta_tau_max").as_double();
  use_current_pose_as_initial_target_ =
    get_node()->get_parameter("use_current_pose_as_initial_target").as_bool();

  const auto stiffness_vec = get_node()->get_parameter("stiffness").as_double_array();
  const auto damping_vec = get_node()->get_parameter("damping").as_double_array();

  if (joint_names_.size() != kNumJoints) {
    RCLCPP_ERROR(
      get_node()->get_logger(),
      "Parameter 'joints' must contain exactly %zu joints.", kNumJoints);
    return false;
  }

  if (stiffness_vec.size() != 6 || damping_vec.size() != 6) {
    RCLCPP_ERROR(get_node()->get_logger(), "'stiffness' and 'damping' must have size 6.");
    return false;
  }

  for (size_t i = 0; i < 6; ++i) {
    stiffness_[i] = stiffness_vec[i];
    damping_[i] = damping_vec[i];
  }

  return true;
}

bool CartesianImpedanceController::init_kdl()
{
  std::string robot_description;
  if (!get_node()->get_parameter(robot_description_param_, robot_description)) {
    RCLCPP_ERROR(
      get_node()->get_logger(),
      "Failed to get parameter '%s'. Make sure robot_description is available.",
      robot_description_param_.c_str());
    return false;
  }

  KDL::Tree tree;
  if (!kdl_parser::treeFromString(robot_description, tree)) {
    RCLCPP_ERROR(get_node()->get_logger(), "Failed to parse robot_description into KDL tree.");
    return false;
  }

  if (!tree.getChain(base_link_, ee_link_, kdl_chain_)) {
    RCLCPP_ERROR(
      get_node()->get_logger(),
      "Failed to get KDL chain from '%s' to '%s'.",
      base_link_.c_str(), ee_link_.c_str());
    return false;
  }

  fk_solver_ = std::make_unique<KDL::ChainFkSolverPos_recursive>(kdl_chain_);
  jac_solver_ = std::make_unique<KDL::ChainJntToJacSolver>(kdl_chain_);

  if (kdl_chain_.getNrOfJoints() != kNumJoints) {
    RCLCPP_ERROR(
      get_node()->get_logger(),
      "KDL chain joint count is %u, expected %zu.",
      kdl_chain_.getNrOfJoints(), kNumJoints);
    return false;
  }

  return true;
}

controller_interface::CallbackReturn CartesianImpedanceController::on_configure(
  const rclcpp_lifecycle::State & /*previous_state*/)
{
  if (!read_parameters()) {
    return controller_interface::CallbackReturn::ERROR;
  }

  if (!init_kdl()) {
    return controller_interface::CallbackReturn::ERROR;
  }

  const auto topic_name = get_node()->get_parameter("target_topic").as_string();
  target_sub_ = get_node()->create_subscription<geometry_msgs::msg::PoseStamped>(
    topic_name,
    rclcpp::SystemDefaultsQoS(),
    std::bind(&CartesianImpedanceController::target_pose_callback, this, std::placeholders::_1));

  RCLCPP_INFO(get_node()->get_logger(), "Configured Cartesian impedance controller.");
  return controller_interface::CallbackReturn::SUCCESS;
}

controller_interface::CallbackReturn CartesianImpedanceController::on_activate(
  const rclcpp_lifecycle::State & /*previous_state*/)
{
  if (command_interfaces_.size() != kNumJoints) {
    RCLCPP_ERROR(
      get_node()->get_logger(),
      "Expected %zu effort command interfaces, got %zu.",
      kNumJoints, command_interfaces_.size());
    return controller_interface::CallbackReturn::ERROR;
  }

  if (state_interfaces_.size() != 2 * kNumJoints) {
    RCLCPP_ERROR(
      get_node()->get_logger(),
      "Expected %zu state interfaces, got %zu.",
      2 * kNumJoints, state_interfaces_.size());
    return controller_interface::CallbackReturn::ERROR;
  }

  for (size_t i = 0; i < kNumJoints; ++i) {
    tau_cmd_prev_[i] = 0.0;
  }

  if (use_current_pose_as_initial_target_) {
    KDL::JntArray q(kNumJoints);
    for (size_t i = 0; i < kNumJoints; ++i) {
      q(i) = state_interfaces_[2 * i].get_value();
    }

    KDL::Frame current_pose;
    if (fk_solver_->JntToCart(q, current_pose) < 0) {
      RCLCPP_ERROR(get_node()->get_logger(), "Failed to compute initial FK.");
      return controller_interface::CallbackReturn::ERROR;
    }

    TargetPose target;
    target.position = current_pose.p;
    target.orientation = current_pose.M;
    target_pose_buffer_.writeFromNonRT(target);

    RCLCPP_INFO(get_node()->get_logger(), "Initial target set to current Cartesian pose.");
  }

  return controller_interface::CallbackReturn::SUCCESS;
}

controller_interface::CallbackReturn CartesianImpedanceController::on_deactivate(
  const rclcpp_lifecycle::State & /*previous_state*/)
{
  for (size_t i = 0; i < kNumJoints; ++i) {
    command_interfaces_[i].set_value(0.0);
  }
  return controller_interface::CallbackReturn::SUCCESS;
}

void CartesianImpedanceController::target_pose_callback(
  const geometry_msgs::msg::PoseStamped::SharedPtr msg)
{
  TargetPose target;
  target.position = KDL::Vector(msg->pose.position.x, msg->pose.position.y, msg->pose.position.z);

  double x = msg->pose.orientation.x;
  double y = msg->pose.orientation.y;
  double z = msg->pose.orientation.z;
  double w = msg->pose.orientation.w;
  target.orientation = KDL::Rotation::Quaternion(x, y, z, w);

  target_pose_buffer_.writeFromNonRT(target);
}

std::array<double, CartesianImpedanceController::kNumJoints>
CartesianImpedanceController::saturate_torque_rate(
  const std::array<double, kNumJoints> & tau_d_calculated,
  const std::array<double, kNumJoints> & tau_d_previous) const
{
  std::array<double, kNumJoints> tau_d_saturated{};
  for (size_t i = 0; i < kNumJoints; ++i) {
    const double diff = tau_d_calculated[i] - tau_d_previous[i];
    const double clamped_diff = std::clamp(diff, -delta_tau_max_, delta_tau_max_);
    tau_d_saturated[i] = tau_d_previous[i] + clamped_diff;
  }
  return tau_d_saturated;
}

controller_interface::return_type CartesianImpedanceController::update(
  const rclcpp::Time & /*time*/,
  const rclcpp::Duration & /*period*/)
{
  KDL::JntArray q(kNumJoints);
  KDL::JntArray dq(kNumJoints);
  for (size_t i = 0; i < kNumJoints; ++i) {
    q(i) = state_interfaces_[2 * i].get_value();
    dq(i) = state_interfaces_[2 * i + 1].get_value();
  }

  KDL::Frame current_pose;
  if (fk_solver_->JntToCart(q, current_pose) < 0) {
    RCLCPP_ERROR_THROTTLE(get_node()->get_logger(), *get_node()->get_clock(), 1000, "FK failed.");
    return controller_interface::return_type::ERROR;
  }

  KDL::Jacobian jacobian(kNumJoints);
  if (jac_solver_->JntToJac(q, jacobian) < 0) {
    RCLCPP_ERROR_THROTTLE(get_node()->get_logger(), *get_node()->get_clock(), 1000, "Jacobian failed.");
    return controller_interface::return_type::ERROR;
  }

  const TargetPose target = *target_pose_buffer_.readFromRT();

  Eigen::Vector3d p(
    current_pose.p.x(),
    current_pose.p.y(),
    current_pose.p.z());

  double qx, qy, qz, qw;
  current_pose.M.GetQuaternion(qx, qy, qz, qw);
  Eigen::Quaterniond q_current(qw, qx, qy, qz);

  double qdx, qdy, qdz, qdw;
  target.orientation.GetQuaternion(qdx, qdy, qdz, qdw);
  Eigen::Quaterniond q_target(qdw, qdx, qdy, qdz);

  if (q_current.coeffs().dot(q_target.coeffs()) < 0.0) {
    q_current.coeffs() << -q_current.coeffs();
  }

  Eigen::Vector3d position_error =
    p - Eigen::Vector3d(target.position.x(), target.position.y(), target.position.z());

  Eigen::Quaterniond q_error = q_current.inverse() * q_target;
  Eigen::Vector3d orientation_error(q_error.x(), q_error.y(), q_error.z());
  orientation_error = -q_current.toRotationMatrix() * orientation_error;

  Eigen::Matrix<double, 6, 1> error;
  error.head<3>() = position_error;
  error.tail<3>() = orientation_error;

  Eigen::Matrix<double, 6, 7> J;
  for (size_t r = 0; r < 6; ++r) {
    for (size_t c = 0; c < kNumJoints; ++c) {
      J(r, c) = jacobian(r, c);
    }
  }

  Eigen::Matrix<double, 7, 1> dq_eigen;
  for (size_t i = 0; i < kNumJoints; ++i) {
    dq_eigen(i) = dq(i);
  }

  Eigen::Matrix<double, 6, 1> xdot = J * dq_eigen;

  Eigen::Matrix<double, 6, 6> K = Eigen::Matrix<double, 6, 6>::Zero();
  Eigen::Matrix<double, 6, 6> D = Eigen::Matrix<double, 6, 6>::Zero();
  for (size_t i = 0; i < 6; ++i) {
    K(i, i) = stiffness_[i];
    D(i, i) = damping_[i];
  }

  Eigen::Matrix<double, 6, 1> wrench_cmd = -K * error - D * xdot;
  Eigen::Matrix<double, 7, 1> tau_task = J.transpose() * wrench_cmd;

  std::array<double, kNumJoints> tau_d_calculated{};
  for (size_t i = 0; i < kNumJoints; ++i) {
    tau_d_calculated[i] = tau_task(i);
  }

  const auto tau_d = saturate_torque_rate(tau_d_calculated, tau_cmd_prev_);

  for (size_t i = 0; i < kNumJoints; ++i) {
    command_interfaces_[i].set_value(tau_d[i]);
    tau_cmd_prev_[i] = tau_d[i];
  }

  return controller_interface::return_type::OK;
}

}  // namespace panda_cartesian_impedance_controller

PLUGINLIB_EXPORT_CLASS(
  panda_cartesian_impedance_controller::CartesianImpedanceController,
  controller_interface::ControllerInterface)