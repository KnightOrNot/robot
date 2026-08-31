import sys
import unittest
from pathlib import Path

import numpy as np

EXPERIMENTS_DIR = Path(__file__).resolve().parents[1] / "experiments"
sys.path.insert(0, str(EXPERIMENTS_DIR))

from piper_x_follow_record import _sample_from_cycle


class PiperXFollowRecordTest(unittest.TestCase):
    def test_sample_contains_final_action_and_actual_observation(self) -> None:
        command = np.arange(7, dtype=np.float64) / 10
        observations = {
            "joint_positions": np.arange(7, dtype=np.float64) / 20,
            "joint_velocities": np.arange(7, dtype=np.float64) / 30,
            "ee_pos_quat": np.array([0.1, 0.2, 0.3, 0.0, 0.0, 0.0, 1.0]),
            "gripper_position": np.array(0.4),
        }

        sample = _sample_from_cycle(
            command=command,
            observations=observations,
            command_time_ns=100,
            observation_time_ns=120,
            wall_time_ns=200,
            control_period_ns=10,
        )

        self.assertEqual(sample["action"], command.tolist())
        self.assertEqual(
            sample["joint_positions"], observations["joint_positions"].tolist()
        )
        self.assertEqual(sample["command_time_ns"], 100)
        self.assertEqual(sample["observation_time_ns"], 120)
        self.assertEqual(sample["gripper_position"], 0.4)

    def test_sample_rejects_invalid_observation_shape(self) -> None:
        observations = {
            "joint_positions": np.zeros(6),
            "joint_velocities": np.zeros(7),
            "ee_pos_quat": np.zeros(7),
            "gripper_position": np.array(0.0),
        }

        with self.assertRaisesRegex(RuntimeError, "joint_positions"):
            _sample_from_cycle(
                command=np.zeros(7),
                observations=observations,
                command_time_ns=100,
                observation_time_ns=120,
                wall_time_ns=200,
                control_period_ns=10,
            )


if __name__ == "__main__":
    unittest.main()
