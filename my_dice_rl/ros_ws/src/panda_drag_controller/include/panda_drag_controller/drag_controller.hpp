#pragma once

#include <atomic>
#include <controller_interface/controller_interface.hpp>
#include <rclcpp/rclcpp.hpp>
#include <std_msgs/msg/empty.hpp>
#include <std_msgs/msg/string.hpp>
#include <std_srvs/srv/set_bool.hpp>
#include "panda_drag_controller/drag_control.hpp"

namespace panda_drag_controller {
class DragController : public controller_interface::ControllerInterface {
 public:
  controller_interface::CallbackReturn on_init() override;
  controller_interface::CallbackReturn on_configure(const rclcpp_lifecycle::State &) override;
  controller_interface::CallbackReturn on_activate(const rclcpp_lifecycle::State &) override;
  controller_interface::CallbackReturn on_deactivate(const rclcpp_lifecycle::State &) override;
  controller_interface::InterfaceConfiguration command_interface_configuration() const override;
  controller_interface::InterfaceConfiguration state_interface_configuration() const override;
  controller_interface::return_type update(const rclcpp::Time &, const rclcpp::Duration &) override;

 private:
  static int64_t steady_ns();
  std::vector<std::string> joints_;
  DragControl law_;
  double heartbeat_timeout_{0.5};
  std::atomic<bool> active_{false}, requested_{false};
  std::atomic<int64_t> heartbeat_ns_{0};
  std::atomic<int> mode_{DragControl::HOLD};
  rclcpp::Service<std_srvs::srv::SetBool>::SharedPtr enable_;
  rclcpp::Subscription<std_msgs::msg::Empty>::SharedPtr heartbeat_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr state_;
  rclcpp::TimerBase::SharedPtr timer_;
};
}  // namespace panda_drag_controller
