# LLM-Assisted Querying

## Overview

Squire uses the user’s LLM provider to transform natural language queries into structured, validated requests. Squire then executes the query locally against the derived SQLite index and optionally asks the LLM to format or summarize the results before responding.

## Flow

1) User issues a natural-language query.
2) LLM proposes a structured query payload (JSON).
3) Squire validates the payload against a schema.
4) Squire executes the query locally.
5) Squire optionally sends results back to the LLM with a response-format prompt.
6) Squire replies to the user with IDs and actionable context.

## Reliability Rules

- Schema validation is strict (no extra keys).
- If the payload is invalid, Squire retries once with explicit error details.
- If confidence is low, Squire asks a clarification question instead of executing.
- Returned responses must include IDs and never invent missing data.

## Update/Append Decision (Planned)

For natural-language captures that might refer to existing records, Squire uses a retrieve-then-decide pattern:

1) Retrieve: run a local search to fetch the top candidate objects (IDs, titles, short snippets).
2) Decide: provide only those candidates to the LLM and ask it to propose create vs append vs update, with a confidence score.
3) Gate: auto-apply only if a single candidate is a clear match and confidence is high; otherwise request confirmation.

This keeps LLM context small, reduces error risk, and preserves auditability.

Detailed design and rollout for matching reliability is documented in `docs/matching-spec.md`.

## Confidence Thresholds

Squire uses configured thresholds to decide whether to execute or clarify:

- execute_threshold (default 0.8): execute query and respond normally.
- confirm_threshold (default 0.6): execute query but ask “does this look right?”
- below confirm_threshold: ask for clarification instead of executing.

## Response Formatting

When enabled, Squire sends query results to the LLM with a fixed response template. The template enforces:

- a short summary
- a list of top results with IDs
- optional next actions

This keeps responses consistent and predictable for the user.
