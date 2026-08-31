"""通过 GELLO 主手和 ZMQ 服务控制 PiPER-X 实时跟随。"""

from __future__ import annotations

import argparse
import time
from collections.abc import Sequence
from typing import Any

import numpy as np
from gello.agents.gello_agent import GelloAgent
from gello.env import RobotEnv
from gello.zmq_core.robot_node import ZMQClientRobot


class ArmOnlyGelloAgent:
    """映射六轴方向，并保留第七维归一化夹爪命令。"""

    def __init__(
        self,
        agent: GelloAgent,
        *,
        relative_alignment: bool = True,
        joint_signs: Sequence[int] = (1, 1, -1, -1, 1, 1),
    ) -> None:
        self._agent = agent
        self._relative_alignment = relative_alignment
        self._joint_signs = np.asarray(joint_signs, dtype=np.float64)
        if self._joint_signs.shape != (6,) or not np.all(
            np.abs(self._joint_signs) == 1
        ):
            raise ValueError("joint signs must contain six values, each +1 or -1")
        self._leader_origin: np.ndarray | None = None
        self._follower_origin: np.ndarray | None = None

    def _read_leader_state(self, observations: dict[str, Any]) -> np.ndarray:
        joints = np.asarray(self._agent.act(observations), dtype=np.float64)
        if joints.shape != (7,):
            raise RuntimeError(
                "the configured GELLO leader must return 6 arm joints and 1 gripper "
                f"joint; received shape {joints.shape}"
            )
        if not np.all(np.isfinite(joints)):
            raise RuntimeError("GELLO returned a non-finite joint value")
        return joints.copy()

    def align_to(
        self, follower_joints: np.ndarray, observations: dict[str, Any]
    ) -> None:
        leader_state = self._read_leader_state(observations)
        self._leader_origin = leader_state
        self._follower_origin = follower_joints.copy()

    def act(self, observations: dict[str, Any]) -> np.ndarray:
        if self._leader_origin is None or self._follower_origin is None:
            raise RuntimeError("GELLO leader must be aligned before control starts")
        leader_state = self._read_leader_state(observations)
        if not self._relative_alignment:
            arm = leader_state[:6] * self._joint_signs
        else:
            leader_delta = leader_state[:6] - self._leader_origin[:6]
            arm = self._follower_origin[:6] + leader_delta * self._joint_signs
        # 第七维保持 GELLO 的原始值；follower 按 0=全闭、1=全开正向映射宽度。
        return np.concatenate((arm, leader_state[6:7]))

    def close(self) -> None:
        """关闭底层 GELLO 读取线程并释放串口。"""

        self._agent.close()


class StrictZMQClientRobot(ZMQClientRobot):
    """Turn error replies from the follower server into local exceptions."""

    def command_joint_state(self, joint_state: np.ndarray) -> None:
        result = super().command_joint_state(joint_state)
        if isinstance(result, dict) and "error" in result:
            raise RuntimeError(f"PiPER-X server rejected command: {result['error']}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Use a seven-axis GELLO leader to control PiPER-X and gripper"
    )
    parser.add_argument("--gello-port", required=True)
    parser.add_argument("--hostname", default="127.0.0.1")
    parser.add_argument("--robot-port", type=int, default=6001)
    parser.add_argument("--hz", type=float, default=50.0)
    parser.add_argument(
        "--start-joints",
        type=float,
        nargs=6,
        required=True,
        metavar=("J1", "J2", "J3", "J4", "J5", "J6"),
        help="current PiPER-X joint feedback in radians",
    )
    parser.add_argument(
        "--joint-signs",
        type=int,
        nargs=6,
        default=(1, 1, -1, -1, 1, 1),
        metavar=("S1", "S2", "S3", "S4", "S5", "S6"),
        help="leader-to-follower direction signs; defaults invert J3 and J4",
    )
    parser.add_argument(
        "--max-start-error-rad",
        type=float,
        default=0.35,
        help="refuse control if leader/follower differ by more than this",
    )
    parser.add_argument(
        "--transition-step-rad",
        type=float,
        default=0.02,
        help="maximum joint change during startup alignment",
    )
    parser.add_argument(
        "--max-command-step-rad",
        type=float,
        default=1.0,
        help="maximum six-axis command-vector step during teleoperation",
    )
    parser.add_argument(
        "--absolute-leader",
        action="store_true",
        help="disable startup-relative alignment and use absolute GELLO angles",
    )
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if args.hz <= 0:
        raise SystemExit("--hz must be positive")
    if args.max_start_error_rad <= 0:
        raise SystemExit("--max-start-error-rad must be positive")
    if not 0 < args.transition_step_rad <= 0.05:
        raise SystemExit("--transition-step-rad must be in (0, 0.05]")
    if not 0 < args.max_command_step_rad <= 1.0:
        raise SystemExit("--max-command-step-rad must be in (0, 1.0]")
    if not np.all(np.isfinite(args.start_joints)):
        raise SystemExit("--start-joints must contain finite values")
    if len(args.joint_signs) != 6 or any(
        sign not in (-1, 1) for sign in args.joint_signs
    ):
        raise SystemExit("--joint-signs must contain six values, each +1 or -1")


def _align_to_leader(
    env: RobotEnv,
    agent: ArmOnlyGelloAgent,
    *,
    max_start_error_rad: float,
    transition_step_rad: float,
) -> dict[str, Any]:
    observations = env.get_obs()
    current = np.asarray(observations["joint_positions"], dtype=np.float64)
    agent.align_to(current, observations)
    target = agent.act(observations)
    if current.shape != (7,):
        raise RuntimeError(
            "PiPER-X server must return 6 arm joints and 1 gripper joint; "
            f"received shape {current.shape}"
        )

    # 启动姿态安全检查只比较六个机械臂关节；夹爪在 0～1 内独立映射。
    errors = np.abs(target[:6] - current[:6])
    worst_joint = int(np.argmax(errors))
    if errors[worst_joint] > max_start_error_rad:
        raise RuntimeError(
            f"startup refused: joint {worst_joint + 1} differs by "
            f"{errors[worst_joint]:.3f}rad (limit {max_start_error_rad:.3f}rad); "
            "move the GELLO leader to match the PiPER-X pose"
        )

    steps = max(1, int(np.ceil(float(np.max(errors)) / transition_step_rad)))
    # 启动对齐只插值 J1～J6；夹爪从第一条命令起直接同步 GELLO 当前值。
    arm_commands = np.linspace(
        current[:6], target[:6], steps + 1, dtype=np.float64
    )[1:]
    for arm_command in arm_commands:
        command = np.concatenate((arm_command, target[6:7]))
        observations = env.step(command)
    return observations


def _limit_arm_command_step(
    command: np.ndarray,
    current: np.ndarray,
    max_step_rad: float,
) -> np.ndarray:
    """只缩放 J1～J6 的单步变化，夹爪第七维始终直接透传。"""

    limited = command.copy()
    arm_delta = command[:6] - current[:6]
    largest_delta = float(np.max(np.abs(arm_delta)))
    if largest_delta > max_step_rad:
        limited[:6] = current[:6] + arm_delta * (max_step_rad / largest_delta)
    limited[6] = command[6]
    return limited


def run(args: argparse.Namespace) -> None:
    _validate_args(args)
    start_arm = np.asarray(args.start_joints, dtype=np.float64)
    # DynamixelRobot expects a value for the configured gripper while computing
    # wraparound offsets. The value is discarded by ArmOnlyGelloAgent.
    gello_start = np.concatenate((start_arm, np.array([0.0])))

    robot = StrictZMQClientRobot(port=args.robot_port, host=args.hostname)
    env = RobotEnv(robot, control_rate_hz=args.hz)
    leader = ArmOnlyGelloAgent(
        GelloAgent(port=args.gello_port, start_joints=gello_start),
        relative_alignment=not args.absolute_leader,
        joint_signs=args.joint_signs,
    )

    try:
        if robot.num_dofs() != 7:
            raise RuntimeError(
                "PiPER-X follower server must expose 6 arm joints and 1 gripper"
            )
        observations = _align_to_leader(
            env,
            leader,
            max_start_error_rad=args.max_start_error_rad,
            transition_step_rad=args.transition_step_rad,
        )
        print("GELLO and PiPER-X aligned; teleoperation started (Ctrl-C to stop)")
        while True:
            command = leader.act(observations)
            current = np.asarray(observations["joint_positions"], dtype=np.float64)
            command = _limit_arm_command_step(
                command,
                current,
                args.max_command_step_rad,
            )
            observations = env.step(command)
    except KeyboardInterrupt:
        print("\nTeleoperation stopped")
    finally:
        leader.close()
        robot.close()
        # The follower server owns CAN and deliberately keeps the arm enabled.
        time.sleep(0.05)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
