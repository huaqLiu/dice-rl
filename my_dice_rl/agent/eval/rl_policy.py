"""统一 DP/RL 部署加载，支持 learner 的原子 latest_policy.json。"""
import json
from pathlib import Path

import torch
import yaml

from my_dice_rl.agent.eval.dp_policy import CheckpointPolicy
from my_dice_rl.util.checkpoint import CHECKPOINT_SCHEMA
from my_dice_rl.util.rl_checkpoint import RL_POLICY_SCHEMA, policy_from_payload
from my_dice_rl.util.rl_config import file_digest, require_review, digest, execution_limits


def resolve_policy_path(path):
    path = Path(path).expanduser().resolve()
    if path.suffix == '.json':
        index = json.loads(path.read_text())
        if index.get('schema') != 'dice_gear_rl_index_v1':
            raise ValueError('Invalid RL policy index')
        selected = Path(index['policy']).expanduser()
        path = selected.resolve() if selected.is_absolute() else (path.parent / selected).resolve()
        if file_digest(path) != index['sha256']:
            raise ValueError('Policy index hash does not match the selected weights')
    return path


class RLCheckpointPolicy(CheckpointPolicy):
    def __init__(self, path, device, collector, real=True, execution=None):
        self.path = str(resolve_policy_path(path))
        state = torch.load(self.path, map_location='cpu', weights_only=False)
        if state.get('schema') != RL_POLICY_SCHEMA:
            raise ValueError('RL execution requires an exported policy, not a learner checkpoint')
        if real:
            if not state.get('deployable') or state.get('simulated') or state.get('actor_updates', 0) < 1:
                raise ValueError('模拟/草稿 RL 策略不能部署到真机')
            require_review(state['review'])
            if state.get('review_digest') != digest(state['review']):
                raise ValueError('Deployment review checksum mismatch')
            if execution is None or not execution.get('deployment_review'):
                raise ValueError('RL 真机执行请使用 cfg/execute_rl.yaml')
            review = yaml.safe_load(Path(execution['deployment_review']).read_text())
            require_review(review)
            if digest(review) != state['review_digest'] or execution_limits(execution) != state['execution_limits']:
                raise ValueError('当前确认设置/控制限制与部署包不一致，请核对后重新导出')
        self.model = policy_from_payload(state, device)
        for value in self.model.state_dict().values():
            if value.is_floating_point() and not torch.isfinite(value).all():
                raise ValueError('Nonfinite RL policy weights')
        teacher = state['teacher']
        self._configure(teacher['task'], teacher['model_config'], device, collector, real)
        self.identity = dict(path=self.path, weights='residual_actor', global_step=state['global_step'],
            dataset_id=teacher['dataset_id'], task=teacher['task'],
            model_config={**teacher['model_config'], 'act_steps': self.act_steps},
            teacher_sha256=teacher['sha256'], sha256=file_digest(self.path), simulated=state['simulated'],
            algorithm=state['algorithm'])
        self._warmup()


def load_execution_policy(path, device, collector, real=True, execution=None):
    path = resolve_policy_path(path)
    if real and execution is not None and execution.get('deployment_review'):
        require_review(yaml.safe_load(Path(execution['deployment_review']).read_text()))
    state = torch.load(path, map_location='cpu', weights_only=False)
    schema = state.get('schema')
    del state
    if schema == CHECKPOINT_SCHEMA:
        return CheckpointPolicy(path, device, collector, real)
    if schema == RL_POLICY_SCHEMA:
        return RLCheckpointPolicy(path, device, collector, real, execution)
    raise ValueError('Unknown policy schema; export a policy from the RL learner checkpoint first')
