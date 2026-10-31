#include <gtest/gtest.h>
#include "panda_cartesian_position_controller/position_step.hpp"

using namespace panda_cartesian_position_controller;

TEST(PositionStep, RestAndSingularJacobianRemainFinite) {
  PositionStep integrator;
  JointVector position = JointVector::Zero();
  const JointVector lower = JointVector::Constant(-2.), upper = JointVector::Constant(2.);
  for (int i = 0; i < 1000; ++i) {
    ASSERT_TRUE(integrator.update(Jacobian::Zero(), Twist::Ones(), .001, .03, .3, .5, 5., lower, upper, position));
  }
  EXPECT_DOUBLE_EQ(position.norm(), 0.);
}

TEST(PositionStep, PositionCommandsObeyVelocityAccelerationAndJerkLimits) {
  PositionStep integrator;
  Jacobian jacobian = Jacobian::Zero();
  jacobian.leftCols<6>().setIdentity();
  Twist twist = Twist::Zero(); twist(0) = 1.;
  JointVector position = JointVector::Zero();
  const JointVector lower = JointVector::Constant(-2.), upper = JointVector::Constant(2.);
  JointVector last_acceleration = JointVector::Zero();
  for (int i = 0; i < 1000; ++i) {
    const JointVector previous = position;
    ASSERT_TRUE(integrator.update(jacobian, twist, .001, .03, .3, .5, 5., lower, upper, position));
    EXPECT_LE((position-previous).cwiseAbs().maxCoeff(), .3*.001 + 1e-12);
    EXPECT_LE(integrator.acceleration.cwiseAbs().maxCoeff(), .5 + 1e-12);
    EXPECT_LE((integrator.acceleration-last_acceleration).cwiseAbs().maxCoeff(), 5.*.001 + 1e-12);
    last_acceleration = integrator.acceleration;
  }
  EXPECT_GT(position(0), .1);
  EXPECT_DOUBLE_EQ(position(6), 0.);
}

TEST(PositionStep, JointLimitsAndLongPeriodsAreRejected) {
  PositionStep integrator;
  Jacobian jacobian = Jacobian::Zero(); jacobian.leftCols<6>().setIdentity();
  JointVector position = JointVector::Zero();
  const JointVector lower = JointVector::Constant(-.000001), upper = JointVector::Constant(.000001);
  bool blocked = false;
  for (int i = 0; i < 1000; ++i) {
    if (!integrator.update(jacobian, Twist::Ones(), .001, .03, .3, .5, 5., lower, upper, position)) {
      blocked = true; break;
    }
  }
  EXPECT_TRUE(blocked);
  EXPECT_TRUE((position.array() <= upper.array()).all());
  EXPECT_FALSE(integrator.update(jacobian, Twist::Zero(), .1, .03, .3, .5, 5., lower, upper, position));
}

TEST(PositionStep, SmallStationaryTargetConverges) {
  PositionStep integrator;
  Jacobian jacobian = Jacobian::Zero(); jacobian.leftCols<6>().setIdentity();
  JointVector position = JointVector::Zero();
  const JointVector lower = JointVector::Constant(-2.), upper = JointVector::Constant(2.);
  for (int i = 0; i < 3000; ++i) {
    Twist twist = Twist::Zero(); twist(0) = 4. * (.002 - position(0));
    ASSERT_TRUE(integrator.update(jacobian, twist, .001, .03, .3, .5, 5., lower, upper, position));
  }
  EXPECT_NEAR(position(0), .002, 1e-6);
  EXPECT_LT(integrator.velocity.norm(), 1e-5);
}
