from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from squire_core.config_utils import load_config, normalize_archive_config
from squire_core.operation_apply import apply_operations


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply a DerivedEvent to canonical objects")
    parser.add_argument("derived_json", help="Path to a DerivedEvent JSON file")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument(
        "--schema-path",
        default="config/schemas/canonical_object_v1.json",
        help="Path to canonical schema",
    )
    parser.add_argument(
        "--derived-schema",
        default="",
        help="Path to derived schema (optional)",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    config = normalize_archive_config(config)
    objects_root = config.get("paths", {}).get("objects_root", "objects")

    derived_path = Path(args.derived_json)
    derived = json.loads(derived_path.read_text(encoding="utf-8"))

    derived_schema = Path(args.derived_schema) if args.derived_schema else None
    try:
        result = apply_operations(
            derived,
            objects_root=objects_root,
            canonical_schema_path=Path(args.schema_path),
            derived_schema_path=derived_schema,
        )
    except ValueError as exc:
        print(f"Apply failed: {exc}")
        print("Derived event did not include required fields. Ask for clarification or re-run interpretation.")
        return 2

    for path in result.written_paths:
        print(f"Wrote: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
