#!/usr/bin/env bash
# DP execution UI; the user starts the robot driver/controller separately.
set -eo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
invocation_dir="$PWD"
cd "$project_root/.."
demo=false
cameras=true
teleop=true
gui_args=()
execution_config="$project_root/cfg/execute_dp.yaml"
while [ "$#" -gt 0 ]; do
    case "$1" in
        --demo) demo=true; gui_args+=("$1"); shift;;
        --no-cameras) cameras=false; shift;;
        --no-teleop) teleop=false; shift;;
        --config)
            if [ "$#" -lt 2 ]; then echo "--config 需要配置路径" >&2; exit 2; fi
            if [[ "$2" = /* ]]; then
                execution_config="$(realpath -- "$2")"
            else
                execution_config="$(realpath -- "$invocation_dir/$2")"
            fi
            gui_args+=("--config" "$execution_config"); shift 2;;
        *) gui_args+=("$1"); shift;;
    esac
done
if [ "$demo" = true ]; then
    exec "$project_root/.venv-dp/bin/python" -m my_dice_rl.script.rl_collect_gui "${gui_args[@]}"
fi
source /opt/ros/humble/setup.bash
robot_setup="${DICE_ROBOT_SETUP:-$project_root/ros_ws/install/setup.bash}"
if [ ! -f "$robot_setup" ]; then
    echo "找不到 Franka ROS 工作空间，请设置 DICE_ROBOT_SETUP 指向 install/setup.bash" >&2
    exit 1
fi
source "$robot_setup"
/usr/bin/python3 "$project_root/script/check_controller_setup.py" "$execution_config"
"$project_root/.venv-dp/bin/python" - "$execution_config" <<'PY_REVIEW'
from pathlib import Path
import sys, yaml
from my_dice_rl.util.config import project_path
from my_dice_rl.util.rl_config import require_review
config = yaml.safe_load(Path(sys.argv[1]).read_text())
if config.get('deployment_review'):
    require_review(yaml.safe_load(project_path(config['deployment_review']).read_text()))
PY_REVIEW
mkdir -p "$project_root/log_dir/execution"
run_log="$(mktemp -d "$project_root/log_dir/execution/run_XXXXXXXX")"
pids=()
cleanup() {
    for pid in "${pids[@]}"; do kill -INT -- "-$pid" 2>/dev/null || true; done
    for pid in "${pids[@]}"; do
        for attempt in {1..20}; do
            kill -0 "$pid" 2>/dev/null || break
            sleep 0.1
        done
        kill -TERM -- "-$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    done
}
trap cleanup EXIT
trap 'exit 130' INT TERM
if [ "$cameras" = true ]; then
    setsid /usr/bin/python3 /opt/ros/humble/bin/ros2 launch "$project_root/script/cameras.launch.py" >"$run_log/cameras.log" 2>&1 &
    pids+=("$!")
fi
if [ "$teleop" = true ]; then
    setsid /usr/bin/python3 -m my_dice_rl.env.real.spacemouse >"$run_log/spacemouse.log" 2>&1 &
    pids+=("$!")
    setsid /usr/bin/python3 -m my_dice_rl.env.real.teleop --ros-args --params-file "$project_root/cfg/teleop.yaml" >"$run_log/teleop.log" 2>&1 &
    pids+=("$!")
fi
echo "执行进程日志：$run_log"
"$project_root/.venv-dp/bin/python" -m my_dice_rl.script.rl_collect_gui "${gui_args[@]}"
