from __future__ import annotations

from pathlib import Path

from squire_core.transport import mutations


def test_extract_target_ids_from_derived_returns_ids_in_order() -> None:
    ids = mutations.extract_target_ids_from_derived(
        {
            "proposed_operations": [
                {"target_id": "A_1"},
                {"target_id": "A_2"},
                {"target_id": "  "},
                {"no_target": "x"},
            ]
        }
    )

    assert ids == ["A_1", "A_2"]


def test_format_apply_success_message_formats_multiple_titles(monkeypatch) -> None:
    def _fake_load_frontmatter(path: str | Path) -> dict[str, object]:
        name = Path(path).name
        if name == "a.md":
            return {"title": "First"}
        if name == "b.md":
            return {"title": "Second"}
        return {}

    monkeypatch.setattr(mutations, "load_frontmatter", _fake_load_frontmatter)

    message = mutations.format_apply_success_message(
        written_paths=[Path("/tmp/a.md"), Path("/tmp/b.md")],
    )

    assert message == '✅ Applied updates to 2 notes:\n- "First"\n- "Second"'
