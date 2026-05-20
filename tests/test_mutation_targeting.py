from __future__ import annotations

from pathlib import Path

from squire_core.transport.mutation_targeting import ground_mutation_target


def _write_object(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_ground_mutation_target_matches_loose_active_title(tmp_path: Path) -> None:
    objects_root = tmp_path / "objects"
    _write_object(
        objects_root / "admin" / "A_1.md",
        """---
id: A_1
type: admin
title: On call shift reminder
status: open
updated_at: "2026-05-18T00:00:00Z"
---
""",
    )

    result = ground_mutation_target(
        content="change on-call shift reminder to thursday",
        intent="fix",
        objects_root=objects_root,
    )

    assert result.outcome == "grounded"
    assert result.target is not None
    assert result.target.object_id == "A_1"


def test_ground_mutation_target_ignores_done_notes_for_append(tmp_path: Path) -> None:
    objects_root = tmp_path / "objects"
    _write_object(
        objects_root / "admin" / "A_1.md",
        """---
id: A_1
type: admin
title: On call shift reminder
status: done
updated_at: "2026-05-18T00:00:00Z"
---
""",
    )

    result = ground_mutation_target(
        content="append on-call shift note",
        intent="append",
        objects_root=objects_root,
    )

    assert result.outcome == "no_pool"
    assert result.target is None


def test_ground_mutation_target_uses_done_notes_for_reopen(tmp_path: Path) -> None:
    objects_root = tmp_path / "objects"
    _write_object(
        objects_root / "admin" / "A_1.md",
        """---
id: A_1
type: admin
title: On call shift reminder
status: done
updated_at: "2026-05-18T00:00:00Z"
---
""",
    )

    result = ground_mutation_target(
        content="reopen the on-call shift reminder",
        intent="reopen",
        objects_root=objects_root,
    )

    assert result.outcome == "grounded"
    assert result.target is not None
    assert result.target.object_id == "A_1"


def test_ground_mutation_target_rejects_unrelated_active_notes(tmp_path: Path) -> None:
    objects_root = tmp_path / "objects"
    _write_object(
        objects_root / "admin" / "A_1.md",
        """---
id: A_1
type: admin
title: Call Toyota to schedule truck maintenance
status: open
updated_at: "2026-05-18T00:00:00Z"
---
""",
    )

    result = ground_mutation_target(
        content="put in for on-call shift swap tomorrow",
        intent="append",
        objects_root=objects_root,
    )

    assert result.outcome == "score_below_threshold"
    assert result.target is None


def test_ground_mutation_target_uses_recent_affinity_with_title_overlap(tmp_path: Path) -> None:
    objects_root = tmp_path / "objects"
    _write_object(
        objects_root / "admin" / "A_1.md",
        """---
id: A_1
type: admin
title: Dentist appointment
status: open
due_at: "2026-03-24T14:00:00-07:00"
updated_at: "2026-03-23T00:00:00Z"
---
""",
    )

    result = ground_mutation_target(
        content="actually the dentist appt is at 1",
        intent="fix",
        objects_root=objects_root,
        recent_ids=["A_1"],
        required_object_type="admin",
        require_due_anchor=True,
    )

    assert result.outcome == "grounded"
    assert result.target is not None
    assert result.target.object_id == "A_1"
    assert result.target.recent_score > 0


def test_ground_mutation_target_rejects_recent_affinity_without_title_overlap(tmp_path: Path) -> None:
    objects_root = tmp_path / "objects"
    _write_object(
        objects_root / "admin" / "A_1.md",
        """---
id: A_1
type: admin
title: Dentist appointment
status: open
due_at: "2026-03-24T14:00:00-07:00"
updated_at: "2026-03-23T00:00:00Z"
---
""",
    )

    result = ground_mutation_target(
        content="actually it's at 1",
        intent="fix",
        objects_root=objects_root,
        recent_ids=["A_1"],
        required_object_type="admin",
        require_due_anchor=True,
    )

    assert result.target is None


def test_ground_mutation_target_prefers_clear_text_match_over_recent_affinity(tmp_path: Path) -> None:
    objects_root = tmp_path / "objects"
    _write_object(
        objects_root / "admin" / "A_RECENT.md",
        """---
id: A_RECENT
type: admin
title: Put in for on-call shift swap
status: open
updated_at: "2026-05-20T06:19:00Z"
---
""",
    )
    _write_object(
        objects_root / "admin" / "A_PARTNER.md",
        """---
id: A_PARTNER
type: admin
title: Join partner onboarding call
status: open
due_at: "2026-05-20T09:00:00-07:00"
updated_at: "2026-05-20T06:20:00Z"
---
""",
    )

    result = ground_mutation_target(
        content="change partner onboarding call to 1",
        intent="fix",
        objects_root=objects_root,
        recent_ids=["A_RECENT"],
        object_type_hint="admin",
    )

    assert result.outcome == "grounded"
    assert result.target is not None
    assert result.target.object_id == "A_PARTNER"
