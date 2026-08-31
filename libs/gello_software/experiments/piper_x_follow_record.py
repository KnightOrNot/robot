"""Follow GELLO with PiPER-X while recording lightweight raw JSONL episodes."""

from __future__ import annotations

import argparse
import os
import select
import sys
import termios
import time
import tty
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Self

import numpy as np
from gello.agents.gello_agent import GelloAgent
from gello.data_utils.raw_episode_recorder import RawEpisodeRecorder, RecordingError
from gello.env import RobotEnv
from gello.zmq_core.robot_node import ZMQClientRobot
from piper_x_follow import (
    ArmOnlyGelloAgent,
    StrictZMQClientRobot,
    _align_to_leader,
    _limit_arm_command_step,
)


class RecordingKeyboard:
    """Read single-key recording commands while preserving Ctrl-C signal handling."""

    def __init__(self) -> None:
        self._fd: int | None = None
        self._original: list[Any] | None = None

    def __enter__(self) -> Self:
        if sys.stdin.isatty():
            self._fd = sys.stdin.fileno()
            self._original = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        else:
            print("警告：标准输入不是终端，R/S/D 记录按键不可用。", file=sys.stderr)
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        if self._fd is not None and self._original is not None:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._original)

    def poll(self) -> str | None:
        if self._fd is None:
            return None
        readable, _, _ = select.select([self._fd], [], [], 0.0)
        if not readable:
            return None
        key = os.read(self._fd, 1).decode(errors="ignore").lower()
        return key if key in {"r", "s", "d", "p", "h"} else None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
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
    )
    parser.add_argument(
        "--joint-signs",
        type=int,
        nargs=6,
        default=(1, 1, -1, -1, 1, 1),
        metavar=("S1", "S2", "S3", "S4", "S5", "S6"),
    )
    parser.add_argument("--max-start-error-rad", type=float, default=0.35)
    parser.add_argument("--transition-step-rad", type=float, default=0.02)
    parser.add_argument("--max-command-step-rad", type=float, default=1.0)
    parser.add_argument("--absolute-leader", action="store_true")
    parser.add_argument("--raw-data-root", type=Path, required=True)
    parser.add_argument("--task", default="PiPER-X GELLO teleoperation")
    parser.add_argument("--record-queue-size", type=int, default=500)
    parser.add_argument(
        "--start-recording",
        action="store_true",
        help="start episode 0 immediately after alignment instead of waiting for R",
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
    if args.record_queue_size <= 0:
        raise SystemExit("--record-queue-size must be positive")
    if not args.task.strip():
        raise SystemExit("--task must not be empty")
    if not np.all(np.isfinite(args.start_joints)):
        raise SystemExit("--start-joints must contain finite values")
    if len(args.joint_signs) != 6 or any(
        sign not in (-1, 1) for sign in args.joint_signs
    ):
        raise SystemExit("--joint-signs must contain six values, each +1 or -1")


def _finite_vector(observations: dict[str, Any], key: str, size: int) -> list[float]:
    value = np.asarray(observations[key], dtype=np.float64)
    if value.shape != (size,) or not np.all(np.isfinite(value)):
        raise RecordingError(
            f"observation {key!r} must be a finite {size}-vector, got {value}"
        )
    return value.tolist()


def _sample_from_cycle(
    *,
    command: np.ndarray,
    observations: dict[str, Any],
    command_time_ns: int,
    observation_time_ns: int,
    wall_time_ns: int,
    control_period_ns: int,
) -> dict[str, Any]:
    action = np.asarray(command, dtype=np.float64)
    if action.shape != (7,) or not np.all(np.isfinite(action)):
        raise RecordingError(f"action must be a finite 7-vector, got {action}")
    gripper = float(np.asarray(observations["gripper_position"], dtype=np.float64))
    if not np.isfinite(gripper):
        raise RecordingError("gripper_position must be finite")
    return {
        "command_time_ns": command_time_ns,
        "observation_time_ns": observation_time_ns,
        "wall_time_ns": wall_time_ns,
        "control_period_ns": control_period_ns,
        "action": action.tolist(),
        "joint_positions": _finite_vector(observations, "joint_positions", 7),
        "joint_velocities": _finite_vector(observations, "joint_velocities", 7),
        "ee_pos_quat": _finite_vector(observations, "ee_pos_quat", 7),
        "gripper_position": gripper,
    }


def _handle_key(key: str | None, recorder: RawEpisodeRecorder) -> None:
    if key is None:
        return
    if key == "r":
        if recorder.is_recording:
            print("\n[记录] 当前 episode 已在记录。")
        else:
            path = recorder.start_episode()
            print(f"\n[记录] 开始 episode {recorder.episode_index:06d}: {path}")
    elif key == "s":
        if recorder.is_recording:
            path = recorder.save_episode()
            print(f"\n[记录] episode 已保存: {path}")
        else:
            print("\n[记录] 当前没有正在记录的 episode。")
    elif key == "d":
        if recorder.is_recording:
            recorder.discard_episode()
            print("\n[记录] 当前 episode 已丢弃。")
        else:
            print("\n[记录] 当前没有正在记录的 episode。")
    elif key == "p":
        state = "正在记录" if recorder.is_recording else "等待开始"
        print(f"\n[记录] 状态：{state}；下一个 episode={recorder.episode_index:06d}")
    elif key == "h":
        print("\n[记录] R=开始，S=保存，D=丢弃，P=状态，H=帮助，Ctrl+C=退出并安全回零")


def run(args: argparse.Namespace) -> None:
    _validate_args(args)
    start_arm = np.asarray(args.start_joints, dtype=np.float64)
    gello_start = np.concatenate((start_arm, np.array([0.0])))

    robot: ZMQClientRobot = StrictZMQClientRobot(
        port=args.robot_port, host=args.hostname
    )
    env = RobotEnv(robot, control_rate_hz=args.hz)
    leader = ArmOnlyGelloAgent(
        GelloAgent(port=args.gello_port, start_joints=gello_start),
        relative_alignment=not args.absolute_leader,
        joint_signs=args.joint_signs,
    )
    recorder: RawEpisodeRecorder | None = None

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
        recorder = RawEpisodeRecorder(
            args.raw_data_root,
            control_hz=args.hz,
            joint_signs=list(args.joint_signs),
            task=args.task,
            queue_size=args.record_queue_size,
        )
        print(f"原始数据 session: {recorder.session_dir}")
        print("GELLO 跟随已启动；R=开始，S=保存，D=丢弃，P=状态，H=帮助，Ctrl+C=退出")
        if args.start_recording:
            path = recorder.start_episode()
            print(f"[记录] 开始 episode {recorder.episode_index:06d}: {path}")

        previous_command_time_ns: int | None = None
        with RecordingKeyboard() as keyboard:
            while True:
                _handle_key(keyboard.poll(), recorder)
                command = leader.act(observations)
                current = np.asarray(observations["joint_positions"], dtype=np.float64)
                command = _limit_arm_command_step(
                    command, current, args.max_command_step_rad
                )
                command_time_ns = time.monotonic_ns()
                wall_time_ns = time.time_ns()
                observations = env.step(command)
                observation_time_ns = time.monotonic_ns()
                control_period_ns = (
                    0
                    if previous_command_time_ns is None
                    else command_time_ns - previous_command_time_ns
                )
                previous_command_time_ns = command_time_ns
                if recorder.is_recording:
                    recorder.add_sample(
                        _sample_from_cycle(
                            command=command,
                            observations=observations,
                            command_time_ns=command_time_ns,
                            observation_time_ns=observation_time_ns,
                            wall_time_ns=wall_time_ns,
                            control_period_ns=control_period_ns,
                        )
                    )
    except KeyboardInterrupt:
        print("\nGELLO 跟随已停止")
    finally:
        try:
            if recorder is not None and recorder.is_recording:
                partial = recorder.close_interrupted()
                print(f"[记录] 未完成的 episode 已保留为: {partial}")
        except RecordingError as exc:
            print(f"[记录] 刷新未完成 episode 失败：{exc}", file=sys.stderr)
        finally:
            leader.close()
            robot.close()
            time.sleep(0.05)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
