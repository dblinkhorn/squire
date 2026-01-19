from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from squire_core.indexer import rebuild_index
from squire_core.config_utils import load_config, normalize_archive_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild the SQLite index")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config.yaml",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    config = normalize_archive_config(config)
    objects_root = config.get("paths", {}).get("objects_root", "objects")
    index_db = config.get("paths", {}).get("index_db", "index/sb.sqlite")

    rebuild_index(objects_root=objects_root, db_path=index_db)
    print(f"Rebuilt index at: {index_db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
