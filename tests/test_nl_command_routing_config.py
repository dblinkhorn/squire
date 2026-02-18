import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    from squire_core.config_utils import NLCommandRoutingConfig, load_nl_command_routing_config  # noqa: E402
except ModuleNotFoundError as exc:
    if exc.name == "yaml":
        raise unittest.SkipTest("PyYAML is not installed in the test environment.") from exc
    raise


class NLCommandRoutingConfigTests(unittest.TestCase):
    def test_defaults(self) -> None:
        routing = load_nl_command_routing_config({})
        self.assertEqual(
            routing,
            NLCommandRoutingConfig(
                enabled=True,
                clarify_on_ambiguous=True,
                allow_nl_mutations=True,
                plan_trace_enabled=True,
                read_auto_min_confidence=0.85,
                mutation_confirm_min_confidence=0.75,
                max_recent_limit=25,
            ),
        )

    def test_overrides(self) -> None:
        config = {
            "nl_command_routing": {
                "enabled": "false",
                "clarify_on_ambiguous": False,
                "allow_nl_mutations": "0",
                "plan_trace_enabled": 0,
                "read_auto_min_confidence": 0.92,
                "mutation_confirm_min_confidence": 0.81,
                "max_recent_limit": 40,
            }
        }
        routing = load_nl_command_routing_config(config)
        self.assertFalse(routing.enabled)
        self.assertFalse(routing.clarify_on_ambiguous)
        self.assertFalse(routing.allow_nl_mutations)
        self.assertFalse(routing.plan_trace_enabled)
        self.assertEqual(routing.read_auto_min_confidence, 0.92)
        self.assertEqual(routing.mutation_confirm_min_confidence, 0.81)
        self.assertEqual(routing.max_recent_limit, 40)

    def test_clamps_confidence_and_limits(self) -> None:
        config = {
            "nl_command_routing": {
                "read_auto_min_confidence": 2.0,
                "mutation_confirm_min_confidence": -1.0,
                "max_recent_limit": 1000,
            }
        }
        routing = load_nl_command_routing_config(config)
        self.assertEqual(routing.read_auto_min_confidence, 1.0)
        self.assertEqual(routing.mutation_confirm_min_confidence, 0.0)
        self.assertEqual(routing.max_recent_limit, 50)


if __name__ == "__main__":
    unittest.main()
