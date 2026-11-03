#include "panda_drag_controller/drag_controller.hpp"
#include <chrono>
#include <hardware_interface/types/hardware_interface_type_values.hpp>
#include <pluginlib/class_list_macros.hpp>

namespace panda_drag_controller {
int64_t DragController::steady_ns() {
  return std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::steady_clock::now().time_since_epoch()).count();
}

controller_interface::CallbackReturn DragController::on_init() {
  auto_declare<std::vector<std::string>>("joints", {});
  auto_declare<std::vector<double>>("drag_damping", {2., 2., 2., 2., 1., 1., 1.});
  auto_declare<std::vector<double>>("hold_stiffness", {60., 60., 60., 50., 30., 20., 10.});
  auto_declare<std::vector<double>>("hold_damping", {8., 8., 8., 7., 5., 4., 3.});
  auto_declare<double>("heartbeat_timeout_s", 0.5);
  auto_declare<double>("max_joint_velocity", 0.5);
  auto_declare<double>("torque_limit", 8.);
  auto_declare<double>("torque_rate", 500.);
  return controller_interface::CallbackReturn::SUCCESS;
}

controller_interface::InterfaceConfiguration DragController::command_interface_configuration() const {
  controller_interface::InterfaceConfiguration result;
  result.type = controller_interface::interface_configuration_type::INDIVIDUAL;
  for (const auto &joint : joints_) result.names.push_back(joint + "/effort");
  return result;
}

controller_interface::InterfaceConfiguration DragController::state_interface_configuration() const {
  controller_interface::InterfaceConfiguration result;
  result.type = controller_interface::interface_configuration_type::INDIVIDUAL;
  for (const auto &joint : joints_) {
    result.names.push_back(joint + "/position");
    result.names.push_back(joint + "/velocity");
  }
  return result;
}

controller_interface::CallbackReturn DragController::on_configure(const rclcpp_lifecycle::State &) {
  try {
    joints_ = get_node()->get_parameter("joints").as_string_array();
    if (joints_.size() != 7) throw std::invalid_argument("joints must contain seven names");
    auto read = [this](const char *name, Joints &target) {
      const auto values = get_node()->get_parameter(name).as_double_array();
      if (values.size() != 7) throw std::invalid_argument("gains must contain seven values");
      std::copy(values.begin(), values.end(), target.begin());
    };
    read("drag_damping", law_.drag_damping);
    read("hold_stiffness", law_.hold_stiffness);
    read("hold_damping", law_.hold_damping);
    heartbeat_timeout_ = get_node()->get_parameter("heartbeat_timeout_s").as_double();
    law_.max_velocity = get_node()->get_parameter("max_joint_velocity").as_double();
    law_.torque_limit = get_node()->get_parameter("torque_limit").as_double();
    law_.torque_rate = get_node()->get_parameter("torque_rate").as_double();
    law_.validate();
    if (!std::isfinite(heartbeat_timeout_) || heartbeat_timeout_ <= 0. || heartbeat_timeout_ > 1.)
      throw std::invalid_argument("heartbeat_timeout_s must be in (0, 1]");
  } catch (const std::exception &error) {
    RCLCPP_ERROR(get_node()->get_logger(), "%s", error.what());
    return controller_interface::CallbackReturn::ERROR;
  }
  enable_ = get_node()->create_service<std_srvs::srv::SetBool>("~/enable",
      [this](std::shared_ptr<std_srvs::srv::SetBool::Request> request,
             std::shared_ptr<std_srvs::srv::SetBool::Response> response) {
        response->success = active_.load() && (!request->data ||
            (steady_ns()-heartbeat_ns_.load())*1e-9 < heartbeat_timeout_);
        if (response->success) requested_.store(request->data);
        response->message = response->success ? "Request accepted; check state for confirmation" :
            "Controller inactive or heartbeat missing";
      });
  heartbeat_ = get_node()->create_subscription<std_msgs::msg::Empty>("~/heartbeat", 1,
      [this](std_msgs::msg::Empty::ConstSharedPtr) { heartbeat_ns_.store(steady_ns()); });
  // Plain publisher reports inactive state too; lifecycle publishers are muted there.
  state_ = rclcpp::create_publisher<std_msgs::msg::String>(*get_node(), "~/state", rclcpp::QoS(1));
  timer_ = get_node()->create_wall_timer(std::chrono::milliseconds(20), [this]() {
    std_msgs::msg::String message;
    message.data = "{\"active\":" + std::string(active_.load() ? "true" : "false") +
        ",\"mode\":" + std::to_string(mode_.load()) + ",\"stamp\":" +
        std::to_string(get_node()->now().seconds()) + "}";
    state_->publish(message);
  });
  return controller_interface::CallbackReturn::SUCCESS;
}

controller_interface::CallbackReturn DragController::on_activate(const rclcpp_lifecycle::State &) {
  if (command_interfaces_.size() != 7 || state_interfaces_.size() != 14)
    return controller_interface::CallbackReturn::ERROR;
  Joints q{};
  for (size_t i = 0; i < 7; ++i) {
    q[i] = state_interfaces_[2*i].get_value();
    if (!std::isfinite(q[i])) return controller_interface::CallbackReturn::ERROR;
  }
  law_.reset(q);
  requested_.store(false);
  mode_.store(DragControl::HOLD);
  active_.store(true);
  return controller_interface::CallbackReturn::SUCCESS;
}

controller_interface::CallbackReturn DragController::on_deactivate(const rclcpp_lifecycle::State &) {
  active_.store(false);
  requested_.store(false);
  for (auto &command : command_interfaces_) command.set_value(0.);
  return controller_interface::CallbackReturn::SUCCESS;
}

controller_interface::return_type DragController::update(const rclcpp::Time &,
                                                        const rclcpp::Duration &period) {
  Joints q{}, dq{};
  for (size_t i = 0; i < 7; ++i) {
    q[i] = state_interfaces_[2*i].get_value();
    dq[i] = state_interfaces_[2*i+1].get_value();
    if (!std::isfinite(q[i]) || !std::isfinite(dq[i])) {
      requested_.store(false);
      return controller_interface::return_type::ERROR;
    }
  }
  const double dt = period.seconds();
  if (!std::isfinite(dt) || dt <= 0. || dt > 0.1) return controller_interface::return_type::ERROR;
  const bool fresh = (steady_ns()-heartbeat_ns_.load())*1e-9 < heartbeat_timeout_;
  const bool requested = requested_.load();
  const auto torque = law_.step(q, dq, requested, fresh, dt);
  // Faults require a new explicit enable request; heartbeats cannot re-enable dragging.
  if (law_.mode == DragControl::WATCHDOG || law_.mode == DragControl::VELOCITY_FAULT)
    requested_.store(false);
  if (requested || mode_.load() < DragControl::WATCHDOG) mode_.store(law_.mode);
  for (size_t i = 0; i < 7; ++i) command_interfaces_[i].set_value(torque[i]);
  return controller_interface::return_type::OK;
}
}  // namespace panda_drag_controller

PLUGINLIB_EXPORT_CLASS(panda_drag_controller::DragController, controller_interface::ControllerInterface)
