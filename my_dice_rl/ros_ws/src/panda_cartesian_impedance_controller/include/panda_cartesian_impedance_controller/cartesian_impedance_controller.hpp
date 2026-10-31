#pragma once

#include <array>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

#include <controller_interface/controller_interface.hpp>
#include <geometry_msgs/msg/pose_stamped.hpp>
#include <hardware_interface/types/hardware_interface_type_values.hpp>
#include <kdl/chain.hpp>
#include <kdl/chainfksolverpos_recursive.hpp>
#include <kdl/chainjnttojacsolver.hpp>
#include <kdl/frames.hpp>
#include <kdl/jacobian.hpp>
#include <kdl/jntarray.hpp>
#include <rclcpp/rclcpp.hpp>
#include <realtime_tools/realtime_buffer.hpp>

namespace panda_cartesian_impedance_controller
{

class CartesianImpedanceController : public controller_interface::ControllerInterface
{
public:
  CartesianImpedanceController();

  controller_interface::CallbackReturn on_init() override;
  controller_interface::InterfaceConfiguration command_interface_configuration() const override;
  controller_interface::InterfaceConfiguration state_interface_configuration() const override;

  controller_interface::CallbackReturn on_configure(
    const rclcpp_lifecycle::State & previous_state) override;
  controller_interface::CallbackReturn on_activate(
    const rclcpp_lifecycle::State & previous_state) override;
  controller_interface::CallbackReturn on_deactivate(
    const rclcpp_lifecycle::State & previous_state) override;

  controller_interface::return_type update(
    const rclcpp::Time & time,
    const rclcpp::Duration & period) override;

private:
  static constexpr size_t kNumJoints = 7;

  struct TargetPose
  {
    KDL::Vector position;
    KDL::Rotation orientation;
  };

  bool read_parameters();
  bool init_kdl();
  void target_pose_callback(const geometry_msgs::msg::PoseStamped::SharedPtr msg);

  std::array<double, kNumJoints> saturate_torque_rate(
    const std::array<double, kNumJoints> & tau_d_calculated,
    const std::array<double, kNumJoints> & tau_d_previous) const;

  std::vector<std::string> joint_names_;
  std::string base_link_;
  std::string ee_link_;
  std::string robot_description_param_;

  std::array<double, 6> stiffness_{};
  std::array<double, 6> damping_{};
  std::array<double, kNumJoints> tau_cmd_prev_{};

  double delta_tau_max_{1.0};
  bool use_current_pose_as_initial_target_{true};

  KDL::Chain kdl_chain_;
  std::unique_ptr<KDL::ChainFkSolverPos_recursive> fk_solver_;
  std::unique_ptr<KDL::ChainJntToJacSolver> jac_solver_;

  realtime_tools::RealtimeBuffer<TargetPose> target_pose_buffer_;
  rclcpp::Subscription<geometry_msgs::msg::PoseStamped>::SharedPtr target_sub_;
};

}  // namespace panda_cartesian_impedance_controller