from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from squire_core.canonical_store import CanonicalObject, write_canonical_object
from squire_core.id_utils import generate_ulid


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a minimal admin item")
    parser.add_argument("title", help="Short title for the admin item")
    parser.add_argument("next_action", help="Next action to move it forward")
    parser.add_argument("--tags", default="", help="Comma-separated tags")
    parser.add_argument("--objects-root", default="objects", help="Objects root directory")
    parser.add_argument(
        "--schema-path",
        default="config/schemas/canonical_object_v1.json",
        help="Path to canonical schema",
    )
    args = parser.parse_args()

    now = _now_iso()
    canonical = CanonicalObject(
        frontmatter={
            "id": generate_ulid(),
            "type": "admin",
            "title": args.title,
            "status": "open",
            "next_action": args.next_action,
            "created_at": now,
            "updated_at": now,
            "archived": False,
            "tags": [t.strip() for t in args.tags.split(",") if t.strip()],
        },
        body=args.next_action,
    )

    output_path = write_canonical_object(
        canonical,
        objects_root=Path(args.objects_root),
        schema_path=Path(args.schema_path),
    )
    print(f"Wrote admin item: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
