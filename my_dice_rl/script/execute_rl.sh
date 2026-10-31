#!/usr/bin/env bash
# 同机部署入口；机器人驱动/控制器仍由用户显式启动。
set -eo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec bash "$project_root/script/execute_dp.sh" --config "$project_root/cfg/execute_rl.yaml" "$@"
