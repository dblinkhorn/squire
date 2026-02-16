# Natural-Language Command Routing Spec

## Problem

Squire currently treats any non-`!` DM as capture-first input. This causes command-like natural language (for example, "show me all my notes") to flow through LLM classification/extraction and sometimes fail in apply, while adding avoidable latency.

Observed failure mode:

- command-like message enters capture path
- retrieval/decision/extraction runs
- extracted fields may be incomplete for canonical create
- apply fails (for example missing `title`)

## Goals

- Route obvious natural-language command intents to existing command handlers before LLM capture.
- Reduce latency for query-style requests by avoiding unnecessary LLM calls.
- Prevent accidental note creation attempts for command-like input.
- Ask concise clarification when a command intent is likely but parameters are ambiguous.

## Non-Goals

- Replacing explicit `!` commands.
- Expanding command surface beyond existing handlers.
- Introducing broad LLM agent-style command planning.

## Scope

Applies only to inbound DM content that does not start with `!` and is not reserved confirmation text (for example `DELETE`).

## Command Intents (v1)

Candidate command intents for routing:

- `status` -> `!status`
- `weekly` -> `!weekly`
- `recent` -> `!recent [N]`
- `find` -> `!find <query>`
- `show` -> `!show <number>`

Mutation commands (`!append`, `!done`, `!fix`, `!confirm`, `!cancel`, `!clear-archive`) are excluded from NL routing in v1 to avoid destructive ambiguity.

## Routing Strategy

Use deterministic routing first; no LLM required in v1.

### 1) Fast Intent Detection

Given normalized content:

- Match known phrase patterns/synonyms per intent.
- Extract command arguments where possible:
  - numeric tail for recent/show (`N`, `#N`, `item N`)
  - quoted or residual text for find query

### 2) Confidence Bands

- High confidence:
  - execute mapped command immediately via existing command handlers.
- Medium confidence:
  - ask clarification question with 2-3 options (for example "Did you mean `!recent` or `!find`?").
- Low confidence:
  - continue existing capture pipeline unchanged.

### 3) Precedence Rules

Before command routing:

- preserve existing explicit command behavior for `!...`
- preserve `DELETE` archive-clear confirmation handling

After command routing:

- if a route executes, do not invoke capture classification/decision/extraction
- if clarification is sent, do not invoke capture classification/decision/extraction

## Example Phrase Map (Seed)

`status`:

- "status"
- "daily digest"
- "what's due today"

`weekly`:

- "weekly review"
- "weekly status"

`recent`:

- "show my notes"
- "show all my notes"
- "recent notes"
- "last N notes"

`find`:

- "find X"
- "search for X"
- "look up X"

`show`:

- "show 2"
- "open 3"
- "show item 1"

## Clarification Behavior

When medium confidence:

- reply with one short question
- provide concrete choices tied to commands
- do not create or update any canonical objects
- use clarification only when input is clearly command-like but ambiguous

Example:

- Input: "show my dentist note"
- Clarification: "Did you mean search (`!find dentist`) or recent list (`!recent`)?"

When input is not confidently command-like:

- do not clarify
- fall through to existing LLM capture/update path

## Telemetry and Logging

Add route-stage logging with no raw message content by default:

- `nl_route_evaluated`:
  - `raw_event_id`
  - `route_result` (`executed`, `clarified`, `fallthrough`)
  - `intent` (if any)
  - `confidence_band`
  - `mapped_command` (if executed)
- `nl_route_clarified`:
  - candidate intents offered

Optional debug mode may include truncated/redacted content for troubleshooting.

## Config Additions

Add an optional `nl_command_routing` block:

```yaml
nl_command_routing:
  enabled: true
  clarify_on_ambiguous: true
  allow_nl_mutations: false
  max_recent_limit: 25
```

Defaults:

- enabled true
- clarification enabled
- mutations disabled

## Failure Handling

- If routed command execution fails, return existing command error response.
- If routing parser errors, log warning and fall through to capture path.
- Never execute destructive behavior from NL routing in v1.

## Implementation Plan

1. Add a routing helper in Discord bot path before capture pipeline.
2. Implement deterministic parser + intent mapping for v1 intents.
3. Reuse existing command handler paths (single source of truth).
4. Add clarification responses for medium confidence.
5. Add tests for route execution, ambiguity clarification, and fallthrough.
6. Add telemetry logs for route outcomes.

## Acceptance Criteria

- "show me all my notes" routes to `!recent` using the configured default recent limit and does not invoke capture LLM calls.
- Ambiguous command-like input triggers clarification and does not create/update objects.
- Non-command capture messages continue existing behavior unchanged.
- Explicit `!` commands behave exactly as before.
- Existing command tests still pass; new routing tests cover major phrase classes.
- Routed `!recent`/`!find` responses include concise command tips footer (including `!recent N` up to 50).

## Rollout

- Start with read-only query commands only.
- Track route metrics and false-positive rate for one release window.
- Expand phrase map based on real traffic.

## Resolved Decisions

1. "show me all my notes" maps to `!recent` with default limit.
2. Clarify only when input is command-like but ambiguous; otherwise fall through to normal LLM path.
3. Keep v1 NL routing read-only; defer NL mutation shortcuts (for example `done` patterns) to a later phase.
4. Include concise command tips in surfaced outputs; `!recent` tip explicitly mentions `!recent N` supports up to 50.
