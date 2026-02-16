#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen


DEFAULT_LOKI_ENDPOINT = f"http://127.0.0.1:{os.getenv('SQUIRE_O11Y_LOKI_PORT', '3100')}/loki/api/v1"
DEFAULT_PROM_ENDPOINT = f"http://127.0.0.1:{os.getenv('SQUIRE_O11Y_PROM_PORT', '9090')}/api/v1"
DEFAULT_TEMPO_ENDPOINT = f"http://127.0.0.1:{os.getenv('SQUIRE_O11Y_TEMPO_PORT', '3200')}"
DEFAULT_LOKI_QUERY_RETRIES = 5
DEFAULT_LOKI_QUERY_DELAY_SECONDS = 2
DEFAULT_TEMPO_QUERY_RETRIES = 5
DEFAULT_TEMPO_QUERY_DELAY_SECONDS = 2


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _to_ns(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000_000)


def _http_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    query = urlencode({k: str(v) for k, v in params.items() if v is not None})
    full_url = f"{url}?{query}" if query else url
    with urlopen(full_url, timeout=15) as response:  # noqa: S310
        payload = json.loads(response.read().decode("utf-8"))
        return {
            "ok": True,
            "url": full_url,
            "status": response.status,
            "response": payload,
        }


def _capture_query(url: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        return _http_json(url, params)
    except Exception as exc:  # pragma: no cover - network failure path
        return {
            "ok": False,
            "url": url,
            "params": params,
            "error": str(exc),
        }


def _trace_count(payload: dict[str, Any]) -> int | None:
    if not isinstance(payload, dict):
        return None
    response = payload.get("response")
    if not isinstance(response, dict):
        return None

    if isinstance(response.get("traces"), list):
        return len(response["traces"])

    data = response.get("data")
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        traces = data.get("traces")
        if isinstance(traces, list):
            return len(traces)
        result = data.get("result")
        if isinstance(result, list):
            return len(result)
    return None


def _loki_stream_count(payload: dict[str, Any]) -> int | None:
    if not isinstance(payload, dict):
        return None
    response = payload.get("response")
    if not isinstance(response, dict):
        return None
    data = response.get("data")
    if not isinstance(data, dict):
        return None
    result = data.get("result")
    if isinstance(result, list):
        return len(result)
    return None


def query_loki(run_id: str, start: str, end: str, endpoint: str) -> dict[str, Any]:
    start_dt = _parse_iso(start)
    end_dt = _parse_iso(end)
    start_ns = _to_ns(start_dt)
    end_ns = _to_ns(end_dt)
    duration_seconds = max(60, int((end_dt - start_dt).total_seconds()))

    logs_query = f'{{job="squire-core"}} | json | run_id="{run_id}"'
    error_query = f'sum(count_over_time({{job="squire-core"}} | json | run_id="{run_id}" | level="ERROR" [{duration_seconds}s]))'

    logs_payload: dict[str, Any] = {}
    error_payload: dict[str, Any] = {}
    rounds = DEFAULT_LOKI_QUERY_RETRIES
    for round_number in range(1, rounds + 1):
        logs_payload = _capture_query(
            f"{endpoint}/query_range",
            {
                "query": logs_query,
                "start": start_ns,
                "end": _to_ns(datetime.now(timezone.utc)),
                "direction": "forward",
                "limit": 5000,
            },
        )
        logs_payload["round"] = round_number
        error_payload = _capture_query(
            f"{endpoint}/query",
            {
                "query": error_query,
                "time": datetime.now(timezone.utc).timestamp(),
            },
        )
        error_payload["round"] = round_number
        if (logs_payload.get("ok") and (_loki_stream_count(logs_payload) or 0) > 0) or round_number >= rounds:
            break
        time.sleep(DEFAULT_LOKI_QUERY_DELAY_SECONDS)

    result = {
        "run_id": run_id,
        "window": {"start": start, "end": datetime.now(timezone.utc).isoformat()},
        "queries": {
            "logs_by_run_id": logs_payload,
            "error_count": error_payload,
        },
    }
    return result


def query_prom(run_id: str, start: str, end: str, endpoint: str) -> dict[str, Any]:
    del run_id

    start_dt = _parse_iso(start)
    end_dt = _parse_iso(end)
    duration_minutes = max(1, int((end_dt - start_dt).total_seconds() / 60))

    failure_query = f"sum(increase(squire_pipeline_failures_total[{duration_minutes}m]))"
    p95_query = "histogram_quantile(0.95, sum by (le, stage) (rate(squire_stage_duration_seconds_bucket[5m])))"

    result = {
        "window": {"start": start, "end": end},
        "queries": {
            "pipeline_failures_increase": _capture_query(
                f"{endpoint}/query",
                {
                    "query": failure_query,
                    "time": end_dt.timestamp(),
                },
            ),
            "stage_p95": _capture_query(
                f"{endpoint}/query",
                {
                    "query": p95_query,
                    "time": end_dt.timestamp(),
                },
            ),
        },
    }
    return result


def query_tempo(run_id: str, start: str, end: str, endpoint: str) -> dict[str, Any]:
    del start, end

    attempts = [
        (
            f"{endpoint}/api/search",
            {
                "query": f'{{run_id="{run_id}"}}',
                "limit": 100,
            },
        ),
        (
            f"{endpoint}/api/search",
            {
                "tags": f"run_id={run_id}",
                "limit": 100,
            },
        ),
        (
            f"{endpoint}/api/search",
            {
                "tags": f"service.name=squire-core run_id={run_id}",
                "limit": 100,
            },
        ),
    ]

    captured = []
    rounds = DEFAULT_TEMPO_QUERY_RETRIES
    for round_number in range(1, rounds + 1):
        for url, params in attempts:
            query_result = _capture_query(url, params)
            query_result["round"] = round_number
            captured.append(query_result)
            if query_result.get("ok") and (_trace_count(query_result) or 0) > 0:
                return {
                    "run_id": run_id,
                    "attempts": captured,
                }
        if round_number < rounds:
            time.sleep(DEFAULT_TEMPO_QUERY_DELAY_SECONDS)

    return {
        "run_id": run_id,
        "attempts": captured,
    }


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _emit(payload: dict[str, Any], output: str | None) -> None:
    if output:
        _write(Path(output), payload)
        return
    print(json.dumps(payload, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Query local observability APIs for a harness run")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--run-id", required=True)
        p.add_argument("--start", required=True, help="ISO start timestamp")
        p.add_argument("--end", required=True, help="ISO end timestamp")
        p.add_argument("--loki-endpoint", default=DEFAULT_LOKI_ENDPOINT)
        p.add_argument("--prom-endpoint", default=DEFAULT_PROM_ENDPOINT)
        p.add_argument("--tempo-endpoint", default=DEFAULT_TEMPO_ENDPOINT)

    loki = sub.add_parser("loki")
    add_common(loki)
    loki.add_argument("--out")

    prom = sub.add_parser("prom")
    add_common(prom)
    prom.add_argument("--out")

    tempo = sub.add_parser("tempo")
    add_common(tempo)
    tempo.add_argument("--out")

    all_cmd = sub.add_parser("all")
    add_common(all_cmd)
    all_cmd.add_argument("--out-dir")

    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.command == "loki":
        payload = query_loki(args.run_id, args.start, args.end, args.loki_endpoint)
        _emit(payload, args.out)
        return 0

    if args.command == "prom":
        payload = query_prom(args.run_id, args.start, args.end, args.prom_endpoint)
        _emit(payload, args.out)
        return 0

    if args.command == "tempo":
        payload = query_tempo(args.run_id, args.start, args.end, args.tempo_endpoint)
        _emit(payload, args.out)
        return 0

    out_dir = Path(args.out_dir) if args.out_dir else None
    loki_payload = query_loki(args.run_id, args.start, args.end, args.loki_endpoint)
    prom_payload = query_prom(args.run_id, args.start, args.end, args.prom_endpoint)
    tempo_payload = query_tempo(args.run_id, args.start, args.end, args.tempo_endpoint)

    combined = {
        "run_id": args.run_id,
        "window": {"start": args.start, "end": args.end},
        "loki": loki_payload,
        "prom": prom_payload,
        "tempo": tempo_payload,
    }

    if out_dir:
        _write(out_dir / "loki.json", loki_payload)
        _write(out_dir / "prom.json", prom_payload)
        _write(out_dir / "tempo.json", tempo_payload)
        _write(out_dir / "o11y.json", combined)
    else:
        print(json.dumps(combined, indent=2, sort_keys=True))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
