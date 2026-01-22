# Surfacing Strategy

## Push (Scheduled)

The v1 daily digest includes overdue and due-today admin items (plus any due soon within the configured window),
one stuck item if present, and one to three suggested next actions (rule-based; optional LLM summarization).
People with `next_contact` due are surfaced alongside the digest. A weekly review is a nice-to-have that focuses
on what changed, open loops, and suggested focus.

## Pull (Interactive)

Pull surfacing is driven by contextual queries via commands, always includes IDs, and is designed for repair loops.

## Design Rules

Outputs should be small and predictable. Rules come first and LLMs come second. The system never invents data and always keeps outputs actionable.

LLM-assisted surfacing uses structured inputs and fixed response formats to keep outputs consistent and auditable. Responses always include object IDs.

## Configuration Defaults

Surfacing rules are configurable in `config.yaml`. Default behavior is:

- Admin: due today/overdue plus due-soon items and up to a small set of open items.
- Projects: included in weekly review and when status is blocked or stale.
- Ideas: included in a weekly review.
- People: surfaced when `next_contact` is due.
