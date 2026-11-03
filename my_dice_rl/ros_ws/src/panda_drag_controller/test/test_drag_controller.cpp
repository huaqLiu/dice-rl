#include <gtest/gtest.h>
#include <chrono>
#include <thread>
#include <hardware_interface/handle.hpp>
#include "panda_drag_controller/drag_controller.hpp"

using namespace std::chrono_literals;
using Return = controller_interface::CallbackReturn;

TEST(DragController, RealServicesAndLoanedInterfacesWithoutRobot) {
  rclcpp::init(0, nullptr);
  {
    panda_drag_controller::DragController controller;
    ASSERT_EQ(controller.init("test_drag_controller"), controller_interface::return_type::OK);
    std::vector<std::string> names;
    for (int i = 1; i <= 7; ++i) names.push_back("panda_joint"+std::to_string(i));
    controller.get_node()->set_parameter({"joints", names});
    controller.get_node()->set_parameter({"heartbeat_timeout_s", .1});
    ASSERT_EQ(controller.on_configure(rclcpp_lifecycle::State()), Return::SUCCESS);
    std::array<double, 7> q{}, dq{}, torque{};
    std::vector<hardware_interface::CommandInterface> commands;
    std::vector<hardware_interface::StateInterface> states;
    commands.reserve(7); states.reserve(14);
    for (int i = 0; i < 7; ++i) {
      commands.emplace_back(names[i], "effort", &torque[i]);
      states.emplace_back(names[i], "position", &q[i]);
      states.emplace_back(names[i], "velocity", &dq[i]);
    }
    std::vector<hardware_interface::LoanedCommandInterface> lc;
    std::vector<hardware_interface::LoanedStateInterface> ls;
    for (auto &handle : commands) lc.emplace_back(handle);
    for (auto &handle : states) ls.emplace_back(handle);
    controller.assign_interfaces(std::move(lc), std::move(ls));
    ASSERT_EQ(controller.on_activate(rclcpp_lifecycle::State()), Return::SUCCESS);
    auto client_node = std::make_shared<rclcpp::Node>("test_drag_client");
    auto client = client_node->create_client<std_srvs::srv::SetBool>("/test_drag_controller/enable");
    auto heartbeat = client_node->create_publisher<std_msgs::msg::Empty>("/test_drag_controller/heartbeat", 1);
    std::string state;
    auto subscription = client_node->create_subscription<std_msgs::msg::String>(
        "/test_drag_controller/state", 1, [&](std_msgs::msg::String::ConstSharedPtr msg) { state = msg->data; });
    rclcpp::executors::SingleThreadedExecutor executor;
    executor.add_node(controller.get_node()->get_node_base_interface());
    executor.add_node(client_node);
    auto pump = [&](int milliseconds, bool beats) {
      for (int i = 0; i < milliseconds; ++i) {
        if (beats && i%10 == 0) heartbeat->publish(std_msgs::msg::Empty());
        executor.spin_some();
        EXPECT_EQ(controller.update(rclcpp::Time(0), rclcpp::Duration::from_seconds(.001)),
            controller_interface::return_type::OK);
        std::this_thread::sleep_for(1ms);
      }
    };
    ASSERT_TRUE(client->wait_for_service(2s));
    auto request = [&](bool enabled) {
      auto msg = std::make_shared<std_srvs::srv::SetBool::Request>(); msg->data = enabled;
      auto future = client->async_send_request(msg);
      for (int i = 0; i < 1000 && future.wait_for(0ms) != std::future_status::ready; ++i) pump(1, false);
      EXPECT_EQ(future.wait_for(0ms), std::future_status::ready);
      return future.get()->success;
    };
    EXPECT_FALSE(request(true));  // No heartbeat: enable rejected.
    pump(100, true);
    EXPECT_TRUE(request(true));
    dq[0] = .1;
    pump(40, true);
    EXPECT_NEAR(torque[0], -.2, 1e-9);
    EXPECT_NE(state.find("\"mode\":1"), std::string::npos);
    dq[0] = 0.; q[0] = .1;
    pump(150, false);  // Watchdog holds where dragging ended.
    EXPECT_NE(state.find("\"mode\":2"), std::string::npos);
    q[0] += .01;
    pump(50, true);   // Restored heartbeats must not resume dragging.
    EXPECT_LT(torque[0], 0.);
    EXPECT_NE(state.find("\"mode\":2"), std::string::npos);
    EXPECT_TRUE(request(true));
    pump(40, true);
    EXPECT_NE(state.find("\"mode\":1"), std::string::npos);
    EXPECT_TRUE(request(false));
    pump(40, true);
    EXPECT_NE(state.find("\"mode\":0"), std::string::npos);
    EXPECT_EQ(controller.on_deactivate(rclcpp_lifecycle::State()), Return::SUCCESS);
    EXPECT_FALSE(request(true));
  }
  rclcpp::shutdown();
}
