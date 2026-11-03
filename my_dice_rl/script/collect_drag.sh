#!/usr/bin/env bash
set -eo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec bash "$project_root/script/collect.sh" --drag --config "$project_root/cfg/collect_drag.yaml" "$@"
