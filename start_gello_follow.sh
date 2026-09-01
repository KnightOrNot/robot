#!/usr/bin/env bash

set -Eeuo pipefail

projects_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
agilex_dir="$projects_dir/agilexrobotics"
gello_dir="$projects_dir/gello_software"
gello_python="$gello_dir/.venv/bin/python"
gello_port="/dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FTBM4Z46-if00-port0"
can_interface="can0"
can_bitrate="1000000"
server_host="127.0.0.1"
server_port="6001"
hz="50"
joint_signs=(1 1 -1 -1 1 1)
assume_yes=false
server_pid=""
server_log=""
follow_server_ready=false
cleanup_running=false
lock_file="/tmp/start_gello_follow.lock"

usage() {
    # 打印启动脚本支持的参数。
    cat <<'EOF'
用法：./start_gello_follow.sh [选项]

选项：
  --gello-port PATH   GELLO Dynamixel 串口
  --can-interface IF  CAN 接口，默认 can0
  --can-bitrate RATE  CAN 波特率，默认 1000000
  --host HOST         ag-gello-server 地址，默认 127.0.0.1
  --port PORT         ag-gello-server 端口，默认 6001
  --hz HZ             GELLO 跟随和 PiPER-X JS 命令频率，默认 50
  --yes               不询问运动确认
  -h, --help          显示帮助
EOF
}

cleanup() {
    # 避免 INT 和 EXIT 连续触发时重复执行硬件退出流程。
    if [[ "$cleanup_running" == true ]]; then
        return
    fi
    cleanup_running=true
    trap - EXIT INT TERM

    # 跟随期间退出时，先保持服务端存活，通过当前 JS 通道小步回零。
    if [[ "$follow_server_ready" == true ]] \
        && [[ -n "$server_pid" ]] \
        && kill -0 "$server_pid" 2>/dev/null; then
        echo
        echo "[安全退出] 通过 GELLO JS 通道将 PiPER-X 移回零位……"
        if "$gello_python" "$gello_dir/experiments/piper_x_movejs.py" \
            --hostname "$server_host" \
            --robot-port "$server_port"; then
            echo "[安全退出] PiPER-X 已返回零位。"
        else
            echo "警告：安全回零失败。" >&2
        fi
    fi

    # 回零后只关闭服务端；不执行 reset、普通 J 模式切换或额外使能。
    if [[ -n "$server_pid" ]] && kill -0 "$server_pid" 2>/dev/null; then
        kill -TERM "$server_pid" 2>/dev/null || true
        wait "$server_pid" 2>/dev/null || true
    fi

    if [[ -n "$server_log" && -f "$server_log" ]]; then
        rm -f -- "$server_log"
    fi
}

trap cleanup EXIT INT TERM

while (( $# > 0 )); do
    case "$1" in
        --gello-port)
            gello_port="${2:?--gello-port 缺少路径}"
            shift 2
            ;;
        --can-interface)
            can_interface="${2:?--can-interface 缺少接口名}"
            shift 2
            ;;
        --can-bitrate)
            can_bitrate="${2:?--can-bitrate 缺少波特率}"
            shift 2
            ;;
        --host)
            server_host="${2:?--host 缺少地址}"
            shift 2
            ;;
        --port)
            server_port="${2:?--port 缺少端口}"
            shift 2
            ;;
        --hz)
            hz="${2:?--hz 缺少数值}"
            shift 2
            ;;
        --yes)
            assume_yes=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "错误：未知参数 $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ ! -d "$agilex_dir" || ! -d "$gello_dir" ]]; then
    echo "错误：agilexrobotics 或 gello_software 项目目录不存在。" >&2
    exit 1
fi
if [[ ! -x "$gello_python" ]]; then
    echo "错误：找不到 $gello_python，请先安装 gello_software 环境。" >&2
    exit 1
fi
if ! "$gello_python" -c '
import math
import sys

try:
    value = float(sys.argv[1])
except ValueError:
    raise SystemExit(1)
raise SystemExit(0 if math.isfinite(value) and value > 0 else 1)
' "$hz"; then
    echo "错误：--hz 必须是正有限数值。" >&2
    exit 2
fi
if [[ ! -e "$gello_port" ]]; then
    echo "错误：GELLO 串口未连接：$gello_port" >&2
    exit 1
fi
if [[ ! -r "$gello_port" || ! -w "$gello_port" ]]; then
    echo "错误：当前用户没有 GELLO 串口读写权限：$gello_port" >&2
    echo "请执行：sudo usermod -aG dialout \"$USER\"" >&2
    echo "然后注销并重新登录（或重启），再运行本脚本。" >&2
    exit 1
fi

# 同一时间只允许一个自动跟随流程持有 GELLO 串口，避免两个读取线程互相
# 抢占状态包并持续触发 Dynamixel COMM_RX_TIMEOUT (-3001)。
exec 9>"$lock_file"
if ! flock -n 9; then
    echo "错误：另一个 start_gello_follow.sh 仍在运行，请先结束它。" >&2
    exit 1
fi

echo "========== [1/6] 配置 PiPER-X CAN 接口 =========="
"$agilex_dir/scripts/config_can.sh" "$can_interface" "$can_bitrate"

echo "========== [2/6] 检查 GELLO 串口和 PiPER-X CAN 通信 =========="
if [[ ! -e "/sys/class/net/$can_interface" ]]; then
    echo "错误: CAN 接口不存在: $can_interface" >&2
    exit 1
fi
arm_status_json="$(
    cd "$agilex_dir"
    uv run ag status --channel "$can_interface" --wait 1.0
)"
printf '%s\n' "$arm_status_json"
if ! printf '%s' "$arm_status_json" | "$gello_python" -c '
import json
import math
import sys

state = json.load(sys.stdin)
joints = state.get("joint_angles_rad")
fps = state.get("receive_fps")
valid = (
    isinstance(fps, (int, float))
    and math.isfinite(fps)
    and fps > 0
    and isinstance(joints, list)
    and len(joints) == 6
    and all(isinstance(value, (int, float)) and math.isfinite(value) for value in joints)
    and state.get("arm_status") is not None
)
raise SystemExit(0 if valid else 1)
'; then
    echo "错误：PiPER-X 未返回完整实时反馈，禁止进入运动流程。" >&2
    echo "请检查机械臂电源、急停、CAN 接线和终端电阻。" >&2
    exit 1
fi

echo "========== [3/6] 读取 GELLO 当前关节和夹爪 =========="
gello_json="$("$gello_python" "$gello_dir/experiments/read_gello_joints.py" \
    --gello-port "$gello_port" --json)"
mapfile -t gello_values < <(
    printf '%s' "$gello_json" | "$gello_python" -c '
import json
import sys

state = json.load(sys.stdin)
for value in state["joints_rad"]:
    print(f"{value:.12g}")
print("{:.12g}".format(state["gripper"]))
'
)
if (( ${#gello_values[@]} != 7 )); then
    echo "错误: GELLO 状态不是预期的 7 维数据。" >&2
    exit 1
fi

arm_target=()
for index in {0..5}; do
    arm_target+=("$(
        "$gello_python" -c \
            'import sys; print(float(sys.argv[1]) * int(sys.argv[2]))' \
            "${gello_values[$index]}" "${joint_signs[$index]}"
    )")
done
gripper_width="$("$gello_python" -c \
    'import sys; print(float(sys.argv[1]) * 0.1)' "${gello_values[6]}")"

printf 'GELLO J1..J6 (rad): %s\n' "${gello_values[*]:0:6}"
printf '方向映射后 PiPER-X 目标 (rad): %s\n' "${arm_target[*]}"
printf 'GELLO gripper: %s; iPER-X 夹爪目标宽度：%s m\n' \
    "${gello_values[6]}" "$gripper_width"

if [[ "$assume_yes" != true ]]; then
    echo "警告：下一步将先执行 zero，再运动到上述绝对位置。"
    echo "请确认机械臂运动范围内无人且无障碍物。"
    read -r -p "输入 yes 继续：" answer
    if [[ "$answer" != "yes" ]]; then
        echo "已取消。"
        exit 0
    fi
fi

echo "========== [4/6] 启动 PiPER-X 服务端并使用 JS 分步回零…… =========="
server_log="$(mktemp --tmpdir ag-gello-server.XXXXXX.log)"
(
    cd "$agilex_dir"
    # 服务端使用独立 session，避免终端 Ctrl+C 在安全回零前同时杀掉它。
    exec setsid uv run ag-gello-server \
        --channel "$can_interface" \
        --host "$server_host" \
        --port "$server_port" \
        --hz "$hz" 9>&-
) >"$server_log" 2>&1 &
server_pid=$!

sleep 2
if ! kill -0 "$server_pid" 2>/dev/null; then
    echo "错误：ag-gello-server 启动失败：" >&2
    cat "$server_log" >&2
    exit 1
fi

cat "$server_log"
follow_server_ready=true
"$gello_python" "$gello_dir/experiments/piper_x_movejs.py" \
    --hostname "$server_host" \
    --robot-port "$server_port"

echo "========== [5/6] 使用同一 JS 会话对齐 PiPER-X 六轴和夹爪…… =========="
"$gello_python" "$gello_dir/experiments/piper_x_movejs.py" \
    --hostname "$server_host" \
    --robot-port "$server_port" \
    --joints "${arm_target[@]}" \
    --gripper "${gello_values[6]}"

echo "========== [6/6] 启动 GELLO 跟随客户端…… =========="
echo "GELLO 跟随已启动；按 Ctrl+C 停止客户端和服务端。"
(
    cd "$gello_dir"
    # --start-joints 只用于选择 GELLO Dynamixel 多圈角度的正确分支，
    # 必须传入方向映射前的 GELLO 角度。客户端会在 absolute-leader
    # 模式下再乘 joint-signs，得到与上方 arm_target 一致的目标。
    exec "$gello_python" experiments/piper_x_follow.py \
        --gello-port "$gello_port" \
        --hostname "$server_host" \
        --robot-port "$server_port" \
        --hz "$hz" \
        --start-joints "${gello_values[@]:0:6}" \
        --joint-signs "${joint_signs[@]}" \
        --absolute-leader 9>&-
)
