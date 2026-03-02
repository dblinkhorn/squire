import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    from squire_core.config_utils import MatchingConfig, load_matching_config  # noqa: E402
except ModuleNotFoundError as exc:
    if exc.name == "yaml":
        raise unittest.SkipTest("PyYAML is not installed in the test environment.") from exc
    raise


class MatchingConfigTests(unittest.TestCase):
    def test_defaults(self) -> None:
        matching = load_matching_config(
            {
                "llm": {"provider": "openai", "model": "gpt-5-mini"},
                "matching": {"semantic_model": "text-embedding-3-small"},
            }
        )
        self.assertEqual(
            matching,
            MatchingConfig(
                lexical_weight=1.0,
                recency_weight=0.15,
                affinity_weight=0.25,
                semantic_weight=0.15,
                semantic_provider="openai",
                semantic_model="text-embedding-3-small",
                candidate_multiplier=4,
                max_candidate_pool=20,
                affinity_recent_ids_per_thread=20,
                affinity_ttl_days=7,
                affinity_max_boost=0.15,
                auto_min_score=0.55,
                auto_min_margin=0.20,
                candidate_limit=5,
                semantic_text_schema_version=1,
            ),
        )

    def test_overrides(self) -> None:
        config = {
            "llm": {"provider": "openai", "model": "gpt-5-mini"},
            "decision": {"candidate_limit": 2},
            "matching": {
                "semantic_weight": 0.35,
                "semantic_provider": "openai",
                "semantic_model": "text-embedding-3-large",
                "candidate_multiplier": 6,
                "candidate_limit": 4,
            },
        }
        matching = load_matching_config(config)
        self.assertEqual(matching.semantic_weight, 0.35)
        self.assertEqual(matching.semantic_provider, "openai")
        self.assertEqual(matching.semantic_model, "text-embedding-3-large")
        self.assertEqual(matching.candidate_multiplier, 6)
        self.assertEqual(matching.candidate_limit, 4)


if __name__ == "__main__":
    unittest.main()
