#!/usr/bin/env bash
# Start only collection processes. Robot bringup/controller activation stays explicit.
set -eo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root/.."
demo=false
cameras=true
teleop=true
gui_args=()
for arg in "$@"; do
    case "$arg" in
        --demo) demo=true; gui_args+=("$arg");;
        --no-cameras) cameras=false;;
        --no-teleop) teleop=false;;
        *) gui_args+=("$arg");;
    esac
done
if [ "$demo" = true ]; then
    exec /usr/bin/python3 -m my_dice_rl.script.collect_gui "${gui_args[@]}"
fi
source /opt/ros/humble/setup.bash
robot_setup="${DICE_ROBOT_SETUP:-/home/lhq/rl_franka/ros_ws/install/setup.bash}"
if [ ! -f "$robot_setup" ]; then
    echo "找不到 Franka ROS 工作空间，请设置 DICE_ROBOT_SETUP 指向 install/setup.bash" >&2
    exit 1
fi
source "$robot_setup"
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
    setsid /usr/bin/python3 /opt/ros/humble/bin/ros2 launch "$project_root/script/cameras.launch.py" >"$run_log/cameras.log" 2>&1 &
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
