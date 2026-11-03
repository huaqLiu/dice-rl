#include <gtest/gtest.h>
#include "panda_drag_controller/drag_control.hpp"

using panda_drag_controller::DragControl;
using panda_drag_controller::Joints;

TEST(DragControl, StartsHoldingAndIgnoresOldCartesianGoals) {
  DragControl law;
  Joints q{}, dq{};
  q[0] = 0.5;
  law.reset(q);
  EXPECT_EQ(law.mode, DragControl::HOLD);
  EXPECT_DOUBLE_EQ(law.step(q, dq, false, false, .001)[0], 0.);
  q[0] += .01;
  EXPECT_LT(law.step(q, dq, false, true, .001)[0], 0.);
}

TEST(DragControl, DragOnlyDampsVelocityAndPauseCapturesCurrentJointPositions) {
  DragControl law;
  Joints q{}, dq{};
  law.reset(q);
  q[0] = .2;
  EXPECT_DOUBLE_EQ(law.step(q, dq, true, true, .001)[0], 0.);
  dq[0] = .1;
  EXPECT_NEAR(law.step(q, dq, true, true, .001)[0], -.2, 1e-12);
  dq[0] = 0.;
  EXPECT_DOUBLE_EQ(law.step(q, dq, false, true, .001)[0], 0.);
  q[0] += .01;
  EXPECT_LT(law.step(q, dq, false, true, .001)[0], 0.);
}

TEST(DragControl, LostHeartbeatAndOverspeedSwitchToHold) {
  DragControl law;
  Joints q{}, dq{};
  law.reset(q);
  law.step(q, dq, true, true, .001);
  q[0] = .1;
  law.step(q, dq, true, false, .001);
  EXPECT_EQ(law.mode, DragControl::WATCHDOG);
  q[0] += .01;
  EXPECT_LT(law.step(q, dq, false, true, .001)[0], 0.);
  dq[0] = .6;
  law.step(q, dq, true, true, .001);
  EXPECT_EQ(law.mode, DragControl::VELOCITY_FAULT);
}

TEST(DragControl, TorqueIsBoundedAndRateLimited) {
  DragControl law;
  Joints q{}, dq{};
  law.reset(q);
  q[0] = 2.;
  double previous = 0.;
  for (int i = 0; i < 100; ++i) {
    const double torque = law.step(q, dq, false, true, .001)[0];
    EXPECT_LE(std::abs(torque), law.torque_limit);
    EXPECT_LE(std::abs(torque-previous), law.torque_rate*.001 + 1e-12);
    previous = torque;
  }
  law.drag_damping[0] = -1.;
  EXPECT_THROW(law.validate(), std::invalid_argument);
}
