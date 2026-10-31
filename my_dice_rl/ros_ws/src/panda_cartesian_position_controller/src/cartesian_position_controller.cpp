#include "panda_cartesian_position_controller/cartesian_position_controller.hpp"

#include <cmath>
#include <set>
#include <kdl_parser/kdl_parser.hpp>
#include <pluginlib/class_list_macros.hpp>
#include <urdf/model.h>
#include <hardware_interface/types/hardware_interface_type_values.hpp>

namespace panda_cartesian_position_controller {
using Callback = controller_interface::CallbackReturn;

double CartesianPositionController::steady_now() {
  return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

Callback CartesianPositionController::on_init() {
  auto_declare<std::vector<std::string>>("joints", {});
  auto_declare<std::string>("base_link", "panda_link0");
  auto_declare<std::string>("ee_link", "panda_hand_tcp");
  auto_declare<std::string>("robot_description_param", "robot_description");
  auto_declare<std::string>("target_topic", "/cartesian_position_target");
  for (const auto & item : std::vector<std::pair<std::string, double>>{
      {"target_timeout", .25}, {"ik_gain", 4.}, {"ik_damping", .03},
      {"max_linear_speed", .025}, {"max_angular_speed", .15},
      {"max_joint_speed", .3}, {"max_joint_acceleration", .5}, {"max_joint_jerk", 5.},
      {"max_joint_tracking_error", .08}, {"max_position_error", .05},
      {"max_rotation_error", .5}, {"joint_limit_margin", .02}}) {
    auto_declare<double>(item.first, item.second);
  }
  return Callback::SUCCESS;
}

controller_interface::InterfaceConfiguration CartesianPositionController::command_interface_configuration() const {
  controller_interface::InterfaceConfiguration result;
  result.type = controller_interface::interface_configuration_type::INDIVIDUAL;
  for (const auto & joint : joints_) result.names.push_back(joint + "/position");
  return result;
}

controller_interface::InterfaceConfiguration CartesianPositionController::state_interface_configuration() const {
  return command_interface_configuration();  // Measured joint positions, in exactly the same order.
}

Callback CartesianPositionController::on_configure(const rclcpp_lifecycle::State &) {
  joints_ = get_node()->get_parameter("joints").as_string_array();
  if (joints_.size() != 7 || std::set<std::string>(joints_.begin(), joints_.end()).size() != 7) {
    RCLCPP_ERROR(get_node()->get_logger(), "Exactly seven distinct joints are required");
    return Callback::ERROR;
  }
  auto value = [this](const std::string & name) { return get_node()->get_parameter(name).as_double(); };
  timeout_ = value("target_timeout"); gain_ = value("ik_gain"); damping_ = value("ik_damping");
  linear_speed_ = value("max_linear_speed"); angular_speed_ = value("max_angular_speed");
  joint_speed_ = value("max_joint_speed"); joint_acceleration_ = value("max_joint_acceleration");
  joint_jerk_ = value("max_joint_jerk"); max_tracking_error_ = value("max_joint_tracking_error");
  max_position_error_ = value("max_position_error"); max_rotation_error_ = value("max_rotation_error");
  const double margin = value("joint_limit_margin");
  for (double parameter : {timeout_, gain_, damping_, linear_speed_, angular_speed_, joint_speed_,
                           joint_acceleration_, joint_jerk_, max_tracking_error_, max_position_error_,
                           max_rotation_error_, margin}) {
    if (!std::isfinite(parameter) || parameter <= 0) return Callback::ERROR;
  }
  base_link_ = get_node()->get_parameter("base_link").as_string();
  ee_link_ = get_node()->get_parameter("ee_link").as_string();
  target_topic_ = get_node()->get_parameter("target_topic").as_string();
  std::string description;
  const auto description_name = get_node()->get_parameter("robot_description_param").as_string();
  if (!get_node()->get_parameter(description_name, description)) return Callback::ERROR;
  urdf::Model model;
  KDL::Tree tree;
  if (!model.initString(description) || !kdl_parser::treeFromString(description, tree) ||
      !tree.getChain(base_link_, ee_link_, chain_) || chain_.getNrOfJoints() != 7) return Callback::ERROR;
  size_t index = 0;
  for (const auto & segment : chain_.segments) {
    if (segment.getJoint().getType() == KDL::Joint::None) continue;
    if (segment.getJoint().getName() != joints_[index]) return Callback::ERROR;
    const auto joint = model.getJoint(joints_[index]);
    if (!joint || !joint->limits) return Callback::ERROR;
    lower_(index) = joint->limits->lower + margin;
    upper_(index) = joint->limits->upper - margin;
    if (lower_(index) >= upper_(index)) return Callback::ERROR;
    ++index;
  }
  fk_ = std::make_unique<KDL::ChainFkSolverPos_recursive>(chain_);
  jacobian_ = std::make_unique<KDL::ChainJntToJacSolver>(chain_);
  subscription_ = get_node()->create_subscription<geometry_msgs::msg::PoseStamped>(target_topic_, 1,
      std::bind(&CartesianPositionController::target_callback, this, std::placeholders::_1));
  RCLCPP_INFO(get_node()->get_logger(), "Position control: %s -> %s, seven position command interfaces",
              base_link_.c_str(), ee_link_.c_str());
  return Callback::SUCCESS;
}

Callback CartesianPositionController::on_activate(const rclcpp_lifecycle::State &) {
  if (state_interfaces_.size() != 7 || command_interfaces_.size() != 7) return Callback::ERROR;
  for (size_t i = 0; i < 7; ++i) {
    position_(i) = state_interfaces_[i].get_value();
    if (!std::isfinite(position_(i)) || position_(i) < lower_(i) || position_(i) > upper_(i)) {
      return Callback::ERROR;
    }
  }
  ++activation_;
  integrator_.reset();
  hold();
  return Callback::SUCCESS;
}

Callback CartesianPositionController::on_deactivate(const rclcpp_lifecycle::State &) {
  ++activation_;
  hold();
  return Callback::SUCCESS;
}

void CartesianPositionController::target_callback(const geometry_msgs::msg::PoseStamped::SharedPtr message) {
  if (message->header.frame_id != base_link_) return;
  const double age = (get_node()->now() - rclcpp::Time(message->header.stamp)).seconds();
  if (!std::isfinite(age) || age < -.1 || age > timeout_) return;
  const auto & p = message->pose.position;
  const auto & q = message->pose.orientation;
  Eigen::Quaterniond quaternion(q.w, q.x, q.y, q.z);
  if (!Eigen::Vector3d(p.x, p.y, p.z).allFinite() || !quaternion.coeffs().allFinite() ||
      std::abs(quaternion.norm() - 1.) > .02) return;
  quaternion.normalize();
  Target target;
  target.pose = KDL::Frame(KDL::Rotation::Quaternion(quaternion.x(), quaternion.y(), quaternion.z(), quaternion.w()),
                           KDL::Vector(p.x, p.y, p.z));
  target.received = steady_now(); target.activation = activation_;
  target_.writeFromNonRT(target);
}

void CartesianPositionController::hold() {
  integrator_.reset();
  if (command_interfaces_.size() == 7) {
    for (size_t i = 0; i < 7; ++i) command_interfaces_[i].set_value(position_(i));
  }
}

controller_interface::return_type CartesianPositionController::update(
    const rclcpp::Time &, const rclcpp::Duration & period) {
  for (size_t i = 0; i < 7; ++i) {
    const double measured = state_interfaces_[i].get_value();
    if (!std::isfinite(measured) || std::abs(measured - position_(i)) > max_tracking_error_) {
      hold();
      return controller_interface::return_type::ERROR;
    }
    kdl_q_(i) = position_(i);
  }
  const auto target = *target_.readFromRT();
  if (target.activation != activation_ || steady_now()-target.received > timeout_) {
    hold();
    return controller_interface::return_type::OK;
  }
  KDL::Frame current;
  if (fk_->JntToCart(kdl_q_, current) < 0 || jacobian_->JntToJac(kdl_q_, kdl_jacobian_) < 0) {
    hold();
    return controller_interface::return_type::ERROR;
  }
  const auto delta = KDL::diff(current, target.pose);
  if (delta.vel.Norm() > max_position_error_ || delta.rot.Norm() > max_rotation_error_) {
    hold();
    return controller_interface::return_type::OK;
  }
  Twist twist;
  twist << delta.vel.x(), delta.vel.y(), delta.vel.z(), delta.rot.x(), delta.rot.y(), delta.rot.z();
  twist *= gain_;
  twist.head<3>() *= std::min(1., linear_speed_ / std::max(1e-12, twist.head<3>().norm()));
  twist.tail<3>() *= std::min(1., angular_speed_ / std::max(1e-12, twist.tail<3>().norm()));
  if (!integrator_.update(kdl_jacobian_.data, twist, period.seconds(), damping_, joint_speed_,
                           joint_acceleration_, joint_jerk_, lower_, upper_, position_)) {
    hold();
    return controller_interface::return_type::OK;
  }
  for (size_t i = 0; i < 7; ++i) command_interfaces_[i].set_value(position_(i));
  return controller_interface::return_type::OK;
}
}  // namespace panda_cartesian_position_controller

PLUGINLIB_EXPORT_CLASS(panda_cartesian_position_controller::CartesianPositionController,
                       controller_interface::ControllerInterface)
