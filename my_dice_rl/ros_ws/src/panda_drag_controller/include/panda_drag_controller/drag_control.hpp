#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <stdexcept>

namespace panda_drag_controller {
using Joints = std::array<double, 7>;

// Franka torque mode already compensates gravity. No additional gravity torque here.
// This law never tracks a Cartesian target and never opens the hand.
class DragControl {
 public:
  enum Mode { HOLD = 0, DRAG = 1, WATCHDOG = 2, VELOCITY_FAULT = 3 };
  Joints drag_damping{2., 2., 2., 2., 1., 1., 1.};
  Joints hold_stiffness{60., 60., 60., 50., 30., 20., 10.};
  Joints hold_damping{8., 8., 8., 7., 5., 4., 3.};
  double max_velocity{0.5}, torque_limit{8.}, torque_rate{500.};
  Mode mode{HOLD};

  void validate() const {
    for (size_t i = 0; i < 7; ++i) {
      if (!std::isfinite(drag_damping[i]) || drag_damping[i] <= 0. ||
          !std::isfinite(hold_stiffness[i]) || hold_stiffness[i] <= 0. ||
          !std::isfinite(hold_damping[i]) || hold_damping[i] <= 0.) {
        throw std::invalid_argument("Drag/hold gains must be positive and finite");
      }
    }
    for (double value : {max_velocity, torque_limit, torque_rate}) {
      if (!std::isfinite(value) || value <= 0.) {
        throw std::invalid_argument("Drag limits must be positive and finite");
      }
    }
  }

  void reset(const Joints &q) { hold_q_ = q; previous_.fill(0.); mode = HOLD; }

  Joints step(const Joints &q, const Joints &dq, bool requested, bool heartbeat,
              double dt) {
    Mode next = requested ? DRAG : HOLD;
    if (requested && !heartbeat) next = WATCHDOG;
    for (double velocity : dq) {
      if (requested && std::abs(velocity) > max_velocity) next = VELOCITY_FAULT;
    }
    if ((mode == DRAG && next != DRAG) || (mode != DRAG && next == DRAG)) hold_q_ = q;
    mode = next;
    Joints result{};
    for (size_t i = 0; i < 7; ++i) {
      const double torque = mode == DRAG ? -drag_damping[i]*dq[i] :
          -hold_stiffness[i]*(q[i]-hold_q_[i]) - hold_damping[i]*dq[i];
      const double bounded = std::clamp(torque, -torque_limit, torque_limit);
      result[i] = previous_[i] + std::clamp(bounded-previous_[i],
          -torque_rate*dt, torque_rate*dt);
    }
    previous_ = result;
    return result;
  }

 private:
  Joints hold_q_{}, previous_{};
};
}  // namespace panda_drag_controller
