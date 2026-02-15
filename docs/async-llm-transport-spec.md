# Async LLM Transport and Heartbeat Stability Spec

## Problem

Squire's Discord runtime handles messages in an asyncio event loop, but current LLM/network calls use blocking `urllib` operations in the same thread. Slow network/model responses can block the loop long enough to miss Discord heartbeats, causing reconnect churn and degraded UX.

Primary blocking call sites:
- `squire_core/llm/openai_provider.py` (`urllib.request.urlopen`)
- invoked during message handling in `squire_core/discord_bot.py`

## Goals

- Eliminate event-loop blocking from OpenAI HTTP calls.
- Stop heartbeat-blocked warnings/reconnects caused by local blocking I/O.
- Preserve current request/response behavior and output semantics.
- Improve tail latency stability under network variance.

## Non-Goals

- Changing prompting logic or routing policy.
- Redesigning capture/decision flow.
- Replacing providers beyond transport-layer behavior.

## Scope

Phase 1 (stability-first):
- move blocking provider calls off-loop (`asyncio.to_thread` bridge)
- add explicit HTTP timeouts for interpret and embed endpoints
- add per-stage latency logs

Phase 2 (proper async transport):
- replace blocking HTTP client usage with async-native HTTP client
- make provider interface await-native where needed

## Design

### Phase 1: Off-Loop Bridge

- Keep current provider API behavior, but call blocking methods via `await asyncio.to_thread(...)` from async paths.
- Add bounded timeouts for all outbound requests.
- Maintain same JSON/schema validation logic.

Expected result:
- event loop stays responsive even when OpenAI calls are slow
- heartbeat stability improves without broad refactor risk

### Phase 2: Async-Native Provider

- Introduce async provider methods (`async interpret`, `async embed`) and await them directly.
- Use connection pooling and async timeouts/retries.
- Keep payload/response contract compatible with existing logic.

Expected result:
- cleaner async model, lower overhead, better extensibility

## Telemetry

Add structured timing logs by `raw_event_id`:
- `classify_ms`
- `candidate_query_ms`
- `matching_retrieval_ms`
- `decision_ms`
- `extract_ms`
- `apply_ms`
- `index_refresh_ms`

Add network error tags:
- timeout vs connection reset vs HTTP status failure

## Acceptance Criteria

- No event-loop heartbeat blocked warnings caused by LLM transport during normal message handling.
- Discord gateway reconnect frequency drops materially under equivalent workload.
- Functional behavior unchanged for successful runs.
- Timeouts produce actionable user-facing error responses and structured logs.

## Rollout

1. Implement Phase 1 and deploy.
2. Monitor heartbeat warnings/reconnects for one release window.
3. If stable, schedule Phase 2 async-native provider migration.

## Risks

- Too-aggressive timeouts can increase transient failures.
- Thread offloading improves loop health but does not reduce raw model latency.
- Phase 2 requires careful interface migration across call sites.
