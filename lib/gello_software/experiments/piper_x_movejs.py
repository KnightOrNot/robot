"""通过正在运行的 GELLO ZMQ 服务和 move_js 将 PiPER-X 分步移到目标。"""

from __future__ import annotations

import argparse
import time
from collections.abc import Sequence

import numpy as np
from gello.zmq_core.robot_node import ZMQClientRobot


class _StrictZMQClientRobot(ZMQClientRobot):
    """把服务端错误响应转换为异常，防止回零流程静默继续。"""

    def command_joint_state(self, joint_state: np.ndarray) -> None:
        """发送七维目标，并检查服务端是否拒绝该命令。"""

        result = super().command_joint_state(joint_state)
        if isinstance(result, dict) and "error" in result:
            raise RuntimeError(f"PiPER-X server rejected safe-zero command: {result['error']}")


def _parser() -> argparse.ArgumentParser:
    """创建安全回零辅助命令的参数解析器。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hostname", default="127.0.0.1")
    parser.add_argument("--robot-port", type=int, default=6001)
    parser.add_argument("--step-rad", type=float, default=0.02)
    parser.add_argument("--period", type=float, default=0.02)
    parser.add_argument("--tolerance-deg", type=float, default=1.0)
    parser.add_argument("--joints", type=float, nargs=6, default=(0.0,) * 6)
    parser.add_argument("--gripper", type=float)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """使用现有 JS 会话分步定位，并根据真实反馈验收结果。"""

    args = _parser().parse_args(argv)
    if args.step_rad <= 0 or args.period <= 0 or args.tolerance_deg <= 0:
        raise SystemExit("step, period and tolerance must be positive")

    robot = _StrictZMQClientRobot(port=args.robot_port, host=args.hostname)
    current = np.asarray(robot.get_joint_state(), dtype=np.float64)
    if current.shape != (7,) or not np.all(np.isfinite(current)):
        raise RuntimeError(f"invalid PiPER-X state: {current}")

    target = np.asarray(args.joints, dtype=np.float64)
    if not np.all(np.isfinite(target)):
        raise SystemExit("joint targets must be finite")
    target_gripper = current[6] if args.gripper is None else args.gripper
    if not np.isfinite(target_gripper) or not 0.0 <= target_gripper <= 1.0:
        raise SystemExit("gripper must be within [0, 1]")

    # 保持同一 JS 会话，将六轴从当前反馈等比例小步移到目标。
    delta = target - current[:6]
    largest = float(np.max(np.abs(delta)))
    steps = max(1, int(np.ceil(largest / args.step_rad)))
    for fraction in np.linspace(0.0, 1.0, steps + 1, dtype=np.float64)[1:]:
        command = current.copy()
        command[:6] = current[:6] + delta * fraction
        command[6] = target_gripper
        robot.command_joint_state(command)
        time.sleep(args.period)

    # 给控制器留出收敛时间；反复发送目标，同时读取真实反馈进行验收。
    deadline = time.monotonic() + 5.0
    final = current
    final_command = np.concatenate((target, np.array([target_gripper])))
    while time.monotonic() < deadline:
        robot.command_joint_state(final_command)
        time.sleep(args.period)
        final = np.asarray(robot.get_joint_state(), dtype=np.float64)
        errors_deg = np.abs(np.rad2deg(final[:6] - target))
        if float(np.max(errors_deg)) <= args.tolerance_deg:
            print(
                "PiPER-X JS target reached; joints_deg="
                + np.array2string(np.rad2deg(final[:6]), precision=3)
            )
            return 0

    errors_deg = np.abs(np.rad2deg(final[:6] - target))
    worst = int(np.argmax(errors_deg))
    error_deg = float(errors_deg[worst])
    raise RuntimeError(
        f"JS positioning failed: J{worst + 1} remains {error_deg:.3f}deg from target"
    )


if __name__ == "__main__":
    raise SystemExit(main())
