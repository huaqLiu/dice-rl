#pragma once

#include <algorithm>
#include <cmath>
#include <Eigen/Dense>

namespace panda_cartesian_position_controller {

using JointVector = Eigen::Matrix<double, 7, 1>;
using Jacobian = Eigen::Matrix<double, 6, 7>;
using Twist = Eigen::Matrix<double, 6, 1>;

// Kinematic DLS inverse + joint rate limiting. No forces/torques are computed.
class PositionStep {
public:
  JointVector velocity = JointVector::Zero();
  JointVector acceleration = JointVector::Zero();

  void reset() { velocity.setZero(); acceleration.setZero(); }

  bool update(const Jacobian & jacobian, const Twist & twist, double dt,
              double damping, double max_velocity, double max_acceleration,
              double max_jerk, const JointVector & lower, const JointVector & upper,
              JointVector & position) {
    if (!jacobian.allFinite() || !twist.allFinite() || !position.allFinite() ||
        !std::isfinite(dt) || dt <= 0 || dt > .01) return false;
    const Eigen::Matrix<double, 6, 6> normal = jacobian * jacobian.transpose() +
      damping * damping * Eigen::Matrix<double, 6, 6>::Identity();
    JointVector desired = jacobian.transpose() * normal.ldlt().solve(twist);
    if (!desired.allFinite()) return false;
    const double scale = std::max(1., desired.cwiseAbs().maxCoeff() / max_velocity);
    desired /= scale;
    for (int i = 0; i < 7; ++i) {
      // Critically damped velocity tracking avoids hunting around a small pose target.
      constexpr double frequency = 20.;  // rad/s
      const double jerk = std::clamp(frequency * frequency * (desired(i) - velocity(i)) -
                                     2. * frequency * acceleration(i), -max_jerk, max_jerk);
      acceleration(i) = std::clamp(acceleration(i) + jerk * dt, -max_acceleration, max_acceleration);
      velocity(i) = std::clamp(velocity(i) + acceleration(i) * dt, -max_velocity, max_velocity);
    }
    const JointVector next = position + velocity * dt;
    if ((next.array() < lower.array()).any() || (next.array() > upper.array()).any()) {
      reset();
      return false;
    }
    position = next;
    return true;
  }
};
}  // namespace panda_cartesian_position_controller
