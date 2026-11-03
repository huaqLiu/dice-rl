"""No hardware: vertical rotation, bounded correction and learner/deployment parity."""
import copy

import numpy as np
import pytest
import torch

from my_dice_rl.util.orientation_constraint import (orientation_config, vertical_rotation,
    constrain_action_tensor)
from my_dice_rl.util.rotation import (pose7_to_pose9, matrix_to_quaternion,
    quaternion_to_matrix, quaternion_angle)
from my_dice_rl.agent.eval.command_limits import limit_command
from my_dice_rl.script.rl_collect_gui import load_config
from my_dice_rl.util.config import PROJECT_ROOT
from my_dice_rl.util.rl_config import load_rl_config
from my_dice_rl.model.rl.distill_rl import DistillRL
from my_dice_rl.util.rl_checkpoint import ResidualPolicy
from my_dice_rl.tests.test_rl_core import TinyTeacher


def rotation(yaw, tilt=0.):
    c, s = np.cos(yaw), np.sin(yaw)
    ct, st = np.cos(tilt), np.sin(tilt)
    return np.array([[c, s, 0.], [s, -c, 0.], [0., 0., -1.]]) @ np.array(
        [[ct, 0., st], [0., 1., 0.], [-st, 0., ct]])


def state(matrix):
    return np.r_[.5, 0., .15, matrix[:2].reshape(6), .03]


@pytest.mark.parametrize('sign', [-1, 1])
def test_numpy_torch_projection_and_gradients(sign):
    config = orientation_config(dict(mode='vertical_keep_yaw', tool_z_sign=sign))
    matrices = [rotation(.4, .2), rotation(-1.1, -.3), np.eye(3)]
    actual = torch.tensor(state(rotation(0)), dtype=torch.float64)[None]
    actions = torch.tensor(np.array([state(m) for m in matrices])[None], requires_grad=True)
    projected = constrain_action_tensor(actions, actual, config)
    for index, matrix in enumerate(matrices):
        expected = vertical_rotation(matrix, sign)
        np.testing.assert_allclose(projected.detach()[0, index, 3:9], expected[:2].reshape(6), atol=1e-12)
        np.testing.assert_allclose(expected.T @ expected, np.eye(3), atol=1e-12)
        assert np.linalg.det(expected) == pytest.approx(1.)
        np.testing.assert_allclose(expected[:, 2], [0, 0, sign])
    projected[..., 4].sum().backward()
    assert torch.isfinite(actions.grad).all() and actions.grad.abs().sum() > 0
    np.testing.assert_allclose(vertical_rotation(rotation(.7)), rotation(.7), atol=1e-12)


def test_translation_holds_until_aligned_and_orientation_is_rate_limited():
    config, _ = load_config(PROJECT_ROOT/'cfg/execute_dp.yaml')
    config['orientation_constraint'] = dict(mode='vertical_keep_yaw')
    actual = np.r_[.5, 0., .15, matrix_to_quaternion(rotation(0., .1))]
    action = state(rotation(.02, .15))
    action[2] -= .01
    previous = actual.copy()
    previous[2] -= .002
    command = limit_command(action, previous, actual, actual, .02, config)
    np.testing.assert_array_equal(command[:3], actual[:3])
    assert quaternion_angle(command[3:], previous[3:]) <= config['max_angular_speed']*.02 + 1e-10
    assert abs(quaternion_to_matrix(command[3:])[2, 2]) > abs(quaternion_to_matrix(actual[3:])[2, 2])
    tensor_action = torch.tensor(action)[None, None]
    projected = constrain_action_tensor(tensor_action, torch.tensor(np.r_[pose7_to_pose9(actual), .03])[None],
                                        orientation_config(config['orientation_constraint']))
    np.testing.assert_allclose(projected[0, 0, :3], actual[:3], atol=1e-8)
    actual[3:] = matrix_to_quaternion(rotation(0.))
    projected = constrain_action_tensor(tensor_action, torch.tensor(np.r_[pose7_to_pose9(actual), .03])[None],
                                        orientation_config(config['orientation_constraint']))
    np.testing.assert_array_equal(projected[0, 0, :3], action[:3])


@pytest.mark.parametrize('state_dim', [9, 10])
def test_learner_deployment_parity_with_constant_rotation_dimension(state_dim):
    from my_dice_rl.model.common.normalizer import FeatureNormalizer
    teacher = TinyTeacher()
    teacher.obs_encoder.feature_dim = state_dim
    teacher.mapping = torch.nn.Linear(state_dim, 36)
    teacher.state_normalizer = FeatureNormalizer(state_dim)
    teacher.state_normalizer.fit(torch.tensor([[-1.]*state_dim, [1.]*state_dim]))
    teacher.encode_obs = lambda obs: obs
    # Constant original rotation component must not undo physical projection at deployment.
    teacher.action_normalizer.constant[5] = True
    config = load_rl_config(require_paths=False)['model']
    config.update(chunk_steps=2, hidden_dims=[16], ensemble_size=2)
    constraint = orientation_config(dict(mode='vertical_keep_yaw'))
    model = DistillRL(teacher, config, constraint)
    features = torch.tensor(np.stack([state(rotation(.3)), state(rotation(.1, .2))])[:, :state_dim], dtype=torch.float32)
    noise = torch.randn(2, 4, 9)
    normalized, _ = model.action(features, noise)
    # normalize retains all physical coordinates, including constant original axes.
    expected = normalized * teacher.action_normalizer.half_range + teacher.action_normalizer.center
    policy = ResidualPolicy(teacher, model.actor, 2, constraint)
    deployed = policy.predict_action(features, init_noise=noise)['action_pred']
    torch.testing.assert_close(expected[..., :9], deployed[..., :9])
    loss = normalized[..., 4].sum()
    loss.backward()
    assert torch.isfinite(model.actor.network[-1].weight.grad).all()


def test_disabled_and_invalid_config():
    action = torch.randn(1, 4, 10)
    assert constrain_action_tensor(action, None, orientation_config()) is action
    for value in [dict(mode='wrong'), dict(tool_z_sign=0), dict(translation_hold_tilt_rad=0)]:
        with pytest.raises(ValueError):
            orientation_config(value)
