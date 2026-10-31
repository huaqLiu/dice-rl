// Copyright (c) 2023 Franka Emika GmbH
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#include <gmock/gmock.h>
#include <chrono>
#include <cstring>
#include <future>
#include <thread>

#include "controller_interface/controller_interface.hpp"
#include "franka_robot_state_broadcaster/franka_robot_state_broadcaster.hpp"
#include "hardware_interface/types/hardware_interface_return_values.hpp"
#include "hardware_interface/types/hardware_interface_type_values.hpp"
#include "rclcpp/rclcpp.hpp"

using namespace franka_robot_state_broadcaster;

class TestableBroadcaster : public FrankaRobotStateBroadcaster {
 public:
  auto publisher() { return realtime_franka_state_publisher; }
};

class TestFrankaRobotStateBroadcaster : public ::testing::Test {
 protected:
  void SetUp() override {
    broadcaster_ = std::make_unique<TestableBroadcaster>();
    broadcaster_->init("test_broadcaster");
  }

  std::unique_ptr<TestableBroadcaster> broadcaster_;
};

TEST_F(TestFrankaRobotStateBroadcaster, test_init_return_success) {
  EXPECT_EQ(broadcaster_->on_init(), controller_interface::CallbackReturn::SUCCESS);
}

TEST_F(TestFrankaRobotStateBroadcaster, test_configure_return_success) {
  EXPECT_EQ(broadcaster_->on_configure(rclcpp_lifecycle::State()),
            controller_interface::CallbackReturn::SUCCESS);
}

TEST_F(TestFrankaRobotStateBroadcaster, test_activate_return_success) {
  EXPECT_EQ(broadcaster_->on_configure(rclcpp_lifecycle::State()),
            controller_interface::CallbackReturn::SUCCESS);
  EXPECT_EQ(broadcaster_->on_activate(rclcpp_lifecycle::State()),
            controller_interface::CallbackReturn::SUCCESS);
}

TEST_F(TestFrankaRobotStateBroadcaster, test_deactivate_return_success) {
  EXPECT_EQ(broadcaster_->on_configure(rclcpp_lifecycle::State()),
            controller_interface::CallbackReturn::SUCCESS);

  EXPECT_EQ(broadcaster_->on_deactivate(rclcpp_lifecycle::State()),
            controller_interface::CallbackReturn::SUCCESS);
}

TEST_F(TestFrankaRobotStateBroadcaster, test_update_without_franka_state_interface_returns_error) {
  EXPECT_EQ(broadcaster_->on_configure(rclcpp_lifecycle::State()),
            controller_interface::CallbackReturn::SUCCESS);
  EXPECT_EQ(broadcaster_->on_activate(rclcpp_lifecycle::State()),
            controller_interface::CallbackReturn::SUCCESS);
  rclcpp::Time time(0.0, 0.0);
  rclcpp::Duration period(0, 0);

  // A busy publisher may skip a sample; an available publisher must still detect missing state.
  auto result = controller_interface::return_type::OK;
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(1);
  while (result == controller_interface::return_type::OK &&
         std::chrono::steady_clock::now() < deadline) {
    result = broadcaster_->update(time, period);
    std::this_thread::yield();
  }
  EXPECT_EQ(result, controller_interface::return_type::ERROR);
}

TEST_F(TestFrankaRobotStateBroadcaster, test_update_with_franka_state_returns_success) {
  ASSERT_EQ(broadcaster_->on_configure(rclcpp_lifecycle::State()),
            controller_interface::CallbackReturn::SUCCESS);
  franka::RobotState state{};
  state.q = {.1, -.7, .2, -2., .3, 1.5, .4};
  auto* pointer = &state;
  double encoded_pointer;
  static_assert(sizeof(encoded_pointer) == sizeof(pointer), "State pointer must fit interface");
  std::memcpy(&encoded_pointer, &pointer, sizeof(pointer));
  hardware_interface::StateInterface handle("panda", "robot_state", &encoded_pointer);
  std::vector<hardware_interface::LoanedStateInterface> states;
  states.emplace_back(handle);
  broadcaster_->assign_interfaces({}, std::move(states));
  ASSERT_EQ(broadcaster_->on_activate(rclcpp_lifecycle::State()),
            controller_interface::CallbackReturn::SUCCESS);

  auto subscriber_node = std::make_shared<rclcpp::Node>("test_state_subscriber");
  bool received = false;
  auto subscription = subscriber_node->create_subscription<franka_msgs::msg::FrankaRobotState>(
      "/test_broadcaster/robot_state", 10,
      [&](const franka_msgs::msg::FrankaRobotState::SharedPtr message) {
        EXPECT_EQ(message->q, state.q);
        received = true;
      });
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
  while (!received && std::chrono::steady_clock::now() < deadline) {
    EXPECT_EQ(broadcaster_->update(rclcpp::Time(1, 0), rclcpp::Duration(0, 1000000)),
              controller_interface::return_type::OK);
    rclcpp::spin_some(subscriber_node);
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  EXPECT_TRUE(received);
  broadcaster_->on_deactivate(rclcpp_lifecycle::State());
}

TEST_F(TestFrankaRobotStateBroadcaster, test_busy_publisher_skips_sample_without_error) {
  ASSERT_EQ(broadcaster_->on_configure(rclcpp_lifecycle::State()),
            controller_interface::CallbackReturn::SUCCESS);
  auto publisher = broadcaster_->publisher();
  std::promise<void> locked, release;
  auto released = release.get_future();
  std::thread holder([&]() {
    publisher->lock();
    locked.set_value();
    released.wait();
    publisher->unlock();
  });
  locked.get_future().wait();
  EXPECT_EQ(broadcaster_->update(rclcpp::Time(0), rclcpp::Duration(0, 1000000)),
            controller_interface::return_type::OK);
  release.set_value();
  holder.join();
}

TEST_F(TestFrankaRobotStateBroadcaster, test_unconfigured_publisher_still_returns_error) {
  EXPECT_EQ(broadcaster_->update(rclcpp::Time(0), rclcpp::Duration(0, 1000000)),
            controller_interface::return_type::ERROR);
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  rclcpp::init(argc, argv);
  int result = RUN_ALL_TESTS();
  rclcpp::shutdown();
  return result;
}
