#pragma once

#include <atomic>
#include <chrono>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include <controller_interface/controller_interface.hpp>
#include <geometry_msgs/msg/pose_stamped.hpp>
#include <kdl/chain.hpp>
#include <kdl/chainfksolverpos_recursive.hpp>
#include <kdl/chainjnttojacsolver.hpp>
#include <kdl/jacobian.hpp>
#include <kdl/jntarray.hpp>
#include <realtime_tools/realtime_buffer.hpp>

#include "panda_cartesian_position_controller/position_step.hpp"

namespace panda_cartesian_position_controller {

class CartesianPositionController : public controller_interface::ControllerInterface {
public:
  controller_interface::CallbackReturn on_init() override;
  controller_interface::CallbackReturn on_configure(const rclcpp_lifecycle::State &) override;
  controller_interface::CallbackReturn on_activate(const rclcpp_lifecycle::State &) override;
  controller_interface::CallbackReturn on_deactivate(const rclcpp_lifecycle::State &) override;
  controller_interface::InterfaceConfiguration command_interface_configuration() const override;
  controller_interface::InterfaceConfiguration state_interface_configuration() const override;
  controller_interface::return_type update(const rclcpp::Time &, const rclcpp::Duration &) override;

private:
  struct Target { KDL::Frame pose; double received{0.}; uint64_t activation{0}; };
  static double steady_now();
  void target_callback(const geometry_msgs::msg::PoseStamped::SharedPtr message);
  void hold();
  std::vector<std::string> joints_;
  std::string base_link_, ee_link_, target_topic_;
  double timeout_{.25}, gain_{4.}, damping_{.03};
  double linear_speed_{.025}, angular_speed_{.15};
  double joint_speed_{.3}, joint_acceleration_{.5}, joint_jerk_{5.};
  double max_tracking_error_{.08}, max_position_error_{.05}, max_rotation_error_{.5};
  KDL::Chain chain_;
  KDL::JntArray kdl_q_{7};
  KDL::Jacobian kdl_jacobian_{7};
  std::unique_ptr<KDL::ChainFkSolverPos_recursive> fk_;
  std::unique_ptr<KDL::ChainJntToJacSolver> jacobian_;
  JointVector position_, lower_, upper_;
  PositionStep integrator_;
  std::atomic<uint64_t> activation_{0};
  realtime_tools::RealtimeBuffer<Target> target_;
  rclcpp::Subscription<geometry_msgs::msg::PoseStamped>::SharedPtr subscription_;
};
}  // namespace panda_cartesian_position_controller
