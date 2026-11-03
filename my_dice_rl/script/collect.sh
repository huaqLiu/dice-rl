#!/usr/bin/env bash
# Start only collection processes. Robot bringup/controller activation stays explicit.
set -eo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
invocation_dir="$PWD"
cd "$project_root/.."
demo=false
cameras=true
teleop=true
drag=false
gui_args=()
collection_config="$project_root/cfg/collect.yaml"
camera_config=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --demo) demo=true; gui_args+=("$1"); shift;;
        --no-cameras) cameras=false; shift;;
        --no-teleop) teleop=false; shift;;
        --drag) drag=true; teleop=false; gui_args+=("$1"); shift;;
        --config|--camera-config)
            option="$1"
            if [ "$#" -lt 2 ]; then echo "$option 需要路径" >&2; exit 2; fi
            selected="$2"
            if [[ "$selected" != /* ]]; then selected="$invocation_dir/$selected"; fi
            selected="$(realpath -- "$selected")"
            if [ "$option" = --config ]; then
                collection_config="$selected"; gui_args+=("--config" "$selected")
            else camera_config="$selected"; fi
            shift 2;;
        *) gui_args+=("$1"); shift;;
    esac
done
if [ "$demo" = true ]; then
    exec /usr/bin/python3 -m my_dice_rl.script.collect_gui "${gui_args[@]}"
fi
source /opt/ros/humble/setup.bash
robot_setup="${DICE_ROBOT_SETUP:-$project_root/ros_ws/install/setup.bash}"
if [ ! -f "$robot_setup" ]; then
    echo "找不到 Franka ROS 工作空间，请设置 DICE_ROBOT_SETUP 指向 install/setup.bash" >&2
    exit 1
fi
source "$robot_setup"
control_config="$project_root/cfg/teleop.yaml"
if [ "$drag" = true ]; then control_config="$collection_config"; fi
/usr/bin/python3 "$project_root/script/check_controller_setup.py" "$control_config"
mkdir -p "$project_root/log_dir/collection"
run_log="$(mktemp -d "$project_root/log_dir/collection/run_XXXXXXXX")"
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
    if [ -z "$camera_config" ]; then
        camera_config="$(/usr/bin/python3 - "$collection_config" "$project_root" <<'PY_CAMERA'
import sys, pathlib, yaml
config = yaml.safe_load(pathlib.Path(sys.argv[1]).read_text())
print((pathlib.Path(sys.argv[2]) / config.get('camera_config', 'cfg/cameras.yaml')).resolve())
PY_CAMERA
)"
    fi
    setsid /usr/bin/python3 /opt/ros/humble/bin/ros2 launch "$project_root/script/cameras.launch.py" "config:=$camera_config" >"$run_log/cameras.log" 2>&1 &
    pids+=("$!")
fi
if [ "$teleop" = true ]; then
    setsid /usr/bin/python3 -m my_dice_rl.env.real.spacemouse >"$run_log/spacemouse.log" 2>&1 &
    pids+=("$!")
    setsid /usr/bin/python3 -m my_dice_rl.env.real.teleop --ros-args --params-file "$project_root/cfg/teleop.yaml" >"$run_log/teleop.log" 2>&1 &
    pids+=("$!")
fi
echo "采集进程日志：$run_log"
/usr/bin/python3 -m my_dice_rl.script.collect_gui "${gui_args[@]}"
