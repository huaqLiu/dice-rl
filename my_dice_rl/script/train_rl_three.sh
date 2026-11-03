#!/usr/bin/env bash
set -eo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
for arg in "$@"; do
    if [ "$arg" = --resume ]; then
        exec bash "$project_root/script/train_rl.sh" "$@"
    fi
done
exec bash "$project_root/script/train_rl.sh" --config "$project_root/cfg/finetune/train_dice_three.yaml" "$@"
