"""读取并打印 GELLO 六个关节及夹爪的当前位置。"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from collections.abc import Sequence

import numpy as np

from gello.agents.gello_agent import PORT_CONFIG_MAP, DynamixelRobotConfig
from gello.dynamixel.driver import DynamixelDriver


def _parser() -> argparse.ArgumentParser:
    """创建 GELLO 关节读取命令的参数解析器。"""

    parser = argparse.ArgumentParser(
        description="Read the current GELLO J1-J6 and gripper positions"
    )
    parser.add_argument(
        "--gello-port",
        required=True,
        help="GELLO Dynamixel serial port configured in PORT_CONFIG_MAP",
    )
    parser.add_argument(
        "--baudrate",
        type=int,
        default=57600,
        help="Dynamixel baud rate; defaults to 57600",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print one machine-readable JSON object",
    )
    return parser


def _map_joint_state(
    raw_joints_rad: Sequence[float], config: DynamixelRobotConfig
) -> np.ndarray:
    """把 Dynamixel 原始弧度映射为 J1～J6 和归一化夹爪位置。"""

    offsets = np.asarray(config.joint_offsets, dtype=np.float64)
    signs = np.asarray(config.joint_signs, dtype=np.float64)
    if config.gripper_config is not None:
        offsets = np.append(offsets, 0.0)
        signs = np.append(signs, 1.0)

    positions = (np.asarray(raw_joints_rad, dtype=np.float64) - offsets) * signs
    if config.gripper_config is not None:
        _, open_deg, closed_deg = config.gripper_config
        open_rad, closed_rad = np.deg2rad((open_deg, closed_deg))
        positions[-1] = np.clip(
            (positions[-1] - open_rad) / (closed_rad - open_rad), 0.0, 1.0
        )
    return positions


def main(argv: Sequence[str] | None = None) -> int:
    """读取一次 GELLO 状态，打印结果并释放串口。"""

    args = _parser().parse_args(argv)
    config = PORT_CONFIG_MAP.get(args.gello_port)
    if config is None:
        known_ports = "\n  ".join(PORT_CONFIG_MAP)
        raise SystemExit(
            f"unknown GELLO port: {args.gello_port}\n"
            f"configured ports:\n  {known_ports}"
        )
    if args.baudrate <= 0:
        raise SystemExit("--baudrate must be positive")

    motor_ids = tuple(config.joint_ids)
    if config.gripper_config is not None:
        motor_ids += (config.gripper_config[0],)

    # JSON 模式下把驱动初始化信息送到 stderr，确保 stdout 只有 JSON 数据。
    output = sys.stderr if args.json else sys.stdout
    with contextlib.redirect_stdout(output):
        driver = DynamixelDriver(
            motor_ids,
            port=args.gello_port,
            baudrate=args.baudrate,
            use_fake_fallback=False,
        )
        try:
            positions = _map_joint_state(driver.get_joints(), config)
        finally:
            driver.close()

    if args.json:
        print(
            json.dumps(
                {
                    "joints_rad": positions[:6].tolist(),
                    "gripper": (
                        float(positions[6])
                        if config.gripper_config is not None
                        else None
                    ),
                }
            )
        )
        return 0

    for index, value_rad in enumerate(positions[:6], start=1):
        print(f"J{index}: {value_rad:+.6f} rad ({np.rad2deg(value_rad):+.3f} deg)")
    if config.gripper_config is not None:
        print(f"gripper: {positions[6]:.6f} (0=全开, 1=全闭)")
    else:
        print("gripper: unavailable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
