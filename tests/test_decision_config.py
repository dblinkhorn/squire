import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    from squire_core.config_utils import DecisionConfig, load_decision_config  # noqa: E402
except ModuleNotFoundError as exc:
    if exc.name == "yaml":
        raise unittest.SkipTest("PyYAML is not installed in the test environment.") from exc
    raise


class DecisionConfigTests(unittest.TestCase):
    def test_defaults(self) -> None:
        config = {}
        decision = load_decision_config(config)
        self.assertEqual(
            decision,
            DecisionConfig(
                auto_apply_threshold=0.85,
                confirm_threshold=0.65,
                candidate_limit=3,
                candidate_score_threshold=0.2,
                auto_min_score=0.55,
                auto_min_margin=0.20,
            ),
        )

    def test_overrides(self) -> None:
        config = {
            "decision": {
                "auto_apply_threshold": 0.9,
                "confirm_threshold": 0.7,
                "candidate_limit": 4,
                "candidate_score_threshold": 0.35,
            }
        }
        decision = load_decision_config(config)
        self.assertEqual(decision.auto_apply_threshold, 0.9)
        self.assertEqual(decision.confirm_threshold, 0.7)
        self.assertEqual(decision.candidate_limit, 4)
        self.assertEqual(decision.candidate_score_threshold, 0.35)
        self.assertEqual(decision.auto_min_score, 0.55)
        self.assertEqual(decision.auto_min_margin, 0.20)

    def test_matching_gate_values_are_loaded_from_matching_block(self) -> None:
        config = {
            "matching": {
                "auto_min_score": 0.7,
                "auto_min_margin": 0.25,
            }
        }
        decision = load_decision_config(config)
        self.assertEqual(decision.auto_min_score, 0.7)
        self.assertEqual(decision.auto_min_margin, 0.25)


if __name__ == "__main__":
    unittest.main()
