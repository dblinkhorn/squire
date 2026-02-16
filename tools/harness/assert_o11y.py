#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REQUIRED_STAGES = [
    "discord.message.receive",
    "event.raw.write",
    "classify",
    "candidate.retrieve",
    "decision.route",
    "interpret.extract",
    "operation.apply",
    "response.send",
    "matching.trace.write",
]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_log_lines(loki_payload: dict[str, Any]) -> list[dict[str, Any]]:
    queries = loki_payload.get("queries")
    if not isinstance(queries, dict):
        return []

    logs_query = queries.get("logs_by_run_id")
    if not isinstance(logs_query, dict):
        return []

    response = logs_query.get("response")
    if not isinstance(response, dict):
        return []

    data = response.get("data")
    if not isinstance(data, dict):
        return []

    result = data.get("result")
    if not isinstance(result, list):
        return []

    lines: list[dict[str, Any]] = []
    for stream in result:
        if not isinstance(stream, dict):
            continue
        values = stream.get("values")
        if not isinstance(values, list):
            continue
        for row in values:
            if not isinstance(row, list) or len(row) < 2:
                continue
            message = row[1]
            if not isinstance(message, str):
                continue
            try:
                parsed = json.loads(message)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                lines.append(parsed)
    return lines


def _extract_error_count(loki_payload: dict[str, Any]) -> float | None:
    queries = loki_payload.get("queries")
    if not isinstance(queries, dict):
        return None
    error_query = queries.get("error_count")
    if not isinstance(error_query, dict) or not error_query.get("ok"):
        return None

    response = error_query.get("response")
    if not isinstance(response, dict):
        return None
    data = response.get("data")
    if not isinstance(data, dict):
        return None
    result = data.get("result")
    if not isinstance(result, list) or not result:
        return 0.0
    sample = result[0]
    if not isinstance(sample, dict):
        return None
    value = sample.get("value")
    if not isinstance(value, list) or len(value) < 2:
        return None
    try:
        return float(value[1])
    except (TypeError, ValueError):
        return None


def _extract_prom_scalar(prom_payload: dict[str, Any], key: str) -> float | None:
    queries = prom_payload.get("queries")
    if not isinstance(queries, dict):
        return None
    query = queries.get(key)
    if not isinstance(query, dict) or not query.get("ok"):
        return None

    response = query.get("response")
    if not isinstance(response, dict):
        return None
    data = response.get("data")
    if not isinstance(data, dict):
        return None
    result = data.get("result")
    if not isinstance(result, list) or not result:
        return 0.0

    sample = result[0]
    if not isinstance(sample, dict):
        return None
    value = sample.get("value")
    if isinstance(value, list) and len(value) >= 2:
        try:
            return float(value[1])
        except (TypeError, ValueError):
            return None
    return None


def _tempo_trace_count(tempo_payload: dict[str, Any]) -> tuple[int | None, str | None]:
    attempts = tempo_payload.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        return (None, "tempo query attempts missing")

    max_count: int | None = None
    saw_success = False
    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        if not attempt.get("ok"):
            continue
        saw_success = True
        response = attempt.get("response")
        if not isinstance(response, dict):
            continue

        if isinstance(response.get("traces"), list):
            count = len(response["traces"])
            if max_count is None or count > max_count:
                max_count = count
            continue

        data = response.get("data")
        if isinstance(data, list):
            count = len(data)
            if max_count is None or count > max_count:
                max_count = count
            continue
        if isinstance(data, dict):
            traces = data.get("traces")
            if isinstance(traces, list):
                count = len(traces)
                if max_count is None or count > max_count:
                    max_count = count
                continue
            if isinstance(data.get("result"), list):
                count = len(data["result"])
                if max_count is None or count > max_count:
                    max_count = count
                continue

    if max_count is not None:
        return (max_count, None)
    if saw_success:
        return (None, "tempo response format was not recognized")

    last_error = None
    for attempt in reversed(attempts):
        if isinstance(attempt, dict):
            last_error = attempt.get("error")
            if last_error:
                break
    return (None, str(last_error or "tempo query failed"))


def _build_check(check_id: str, description: str, status: str, evidence_refs: list[str]) -> dict[str, Any]:
    return {
        "id": check_id,
        "description": description,
        "status": status,
        "evidence_refs": evidence_refs,
    }


def _window(summary: dict[str, Any], loki: dict[str, Any], prom: dict[str, Any]) -> dict[str, str]:
    start = None
    end = None

    if isinstance(loki.get("window"), dict):
        start = loki["window"].get("start")
        end = loki["window"].get("end")
    if (not start or not end) and isinstance(prom.get("window"), dict):
        start = prom["window"].get("start")
        end = prom["window"].get("end")
    if not start:
        start = summary.get("started_at")
    if not end:
        end = summary.get("finished_at")

    now = datetime.now(timezone.utc).isoformat()
    if not isinstance(start, str):
        start = now
    if not isinstance(end, str):
        end = now
    return {"start": start, "end": end}


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate harness observability assertions")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--mode", default="deterministic", choices=["deterministic", "integration-smoke"])
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    summary = _read_json(run_dir / "summary.json")
    loki = _read_json(run_dir / "loki.json")
    prom = _read_json(run_dir / "prom.json")
    tempo = _read_json(run_dir / "tempo.json")

    run_id = str(summary.get("run_id") or "unknown")
    checks: list[dict[str, Any]] = []

    log_lines = _parse_log_lines(loki)
    if log_lines:
        parsed_error_count = sum(1 for line in log_lines if str(line.get("level", "")).upper() == "ERROR")
        query_error_count = _extract_error_count(loki)
        effective_error_count = float(parsed_error_count)
        if query_error_count is not None:
            effective_error_count = max(effective_error_count, query_error_count)

        checks.append(
            _build_check(
                "logs.no_error",
                "Zero ERROR logs for the harness run",
                "pass" if effective_error_count == 0 else "failed",
                ["loki.json:queries.logs_by_run_id", "loki.json:queries.error_count"],
            )
        )
    else:
        checks.append(
            _build_check(
                "logs.no_error",
                "Zero ERROR logs for the harness run",
                "blocked",
                ["loki.json:queries.logs_by_run_id"],
            )
        )

    failures_increase = _extract_prom_scalar(prom, "pipeline_failures_increase")
    if failures_increase is None:
        checks.append(
            _build_check(
                "metrics.pipeline_failures_zero",
                "No pipeline failure increase in run window",
                "blocked",
                ["prom.json:queries.pipeline_failures_increase"],
            )
        )
    else:
        checks.append(
            _build_check(
                "metrics.pipeline_failures_zero",
                "No pipeline failure increase in run window",
                "pass" if failures_increase <= 0 else "failed",
                ["prom.json:queries.pipeline_failures_increase"],
            )
        )

    stages_seen = {
        str(line.get("pipeline_stage"))
        for line in log_lines
        if str(line.get("event")) == "stage_complete" and line.get("pipeline_stage") is not None
    }
    missing_stages = sorted(stage for stage in REQUIRED_STAGES if stage not in stages_seen)
    if not log_lines:
        status = "blocked"
    else:
        status = "pass" if not missing_stages else "failed"

    checks.append(
        _build_check(
            "logs.required_stages",
            "Required pipeline stages emitted stage_complete logs",
            status,
            ["loki.json:queries.logs_by_run_id"],
        )
    )

    trace_count, trace_error = _tempo_trace_count(tempo)
    if trace_count is None:
        checks.append(
            _build_check(
                "traces.present",
                "Trace search returns entries for the run_id",
                "blocked",
                [f"tempo.json:error={trace_error or 'unknown'}"],
            )
        )
    else:
        checks.append(
            _build_check(
                "traces.present",
                "Trace search returns entries for the run_id",
                "pass" if trace_count > 0 else "failed",
                ["tempo.json:attempts"],
            )
        )

    statuses = [str(check.get("status")) for check in checks]
    if "failed" in statuses:
        overall_status = "failed"
        exit_code = 1
    elif "blocked" in statuses:
        overall_status = "blocked"
        exit_code = 2
    else:
        overall_status = "pass"
        exit_code = 0

    assertions = {
        "run_id": run_id,
        "window": _window(summary, loki, prom),
        "mode": args.mode,
        "checks": checks,
        "status": overall_status,
    }
    (run_dir / "assertions.json").write_text(
        json.dumps(assertions, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
