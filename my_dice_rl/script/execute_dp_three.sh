#!/usr/bin/env bash
set -eo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec bash "$project_root/script/execute_dp.sh" --config "$project_root/cfg/execute_dp_three.yaml" "$@"
