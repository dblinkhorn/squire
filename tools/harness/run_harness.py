#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import socket
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = ROOT / ".agent" / "runs"
CURRENT_RUN_ID_PATH = RUNS_ROOT / ".current_run_id"
COMPOSE_FILE = ROOT / "docker-compose.o11y.local.yml"
DEFAULT_MODE = "deterministic"
DEFAULT_ENV = "dev"
DEFAULT_FIXED_NOW = "2026-02-15T12:00:00+00:00"
STACK_STARTUP_TIMEOUT_SECONDS = 120
DETERMINISTIC_TIMEOUT_SECONDS = 600
INTEGRATION_TIMEOUT_SECONDS = 480
INTEGRATION_RETRIES = 2
O11Y_PORT_DEFAULTS = {
    "SQUIRE_O11Y_ALLOY_READY_PORT": 12345,
    "SQUIRE_O11Y_OTLP_GRPC_PORT": 4317,
    "SQUIRE_O11Y_OTLP_HTTP_PORT": 4318,
    "SQUIRE_O11Y_LOKI_PORT": 3100,
    "SQUIRE_O11Y_TEMPO_PORT": 3200,
    "SQUIRE_O11Y_PROM_PORT": 9090,
}


def _o11y_port(env_key: str, default: int, source: dict[str, str] | None = None) -> int:
    env = source or os.environ
    raw = env.get(env_key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if value <= 0:
        return default
    return value


def _o11y_endpoints(source: dict[str, str] | None = None) -> dict[str, str]:
    alloy_ready_port = _o11y_port("SQUIRE_O11Y_ALLOY_READY_PORT", 12345, source)
    loki_port = _o11y_port("SQUIRE_O11Y_LOKI_PORT", 3100, source)
    prom_port = _o11y_port("SQUIRE_O11Y_PROM_PORT", 9090, source)
    tempo_port = _o11y_port("SQUIRE_O11Y_TEMPO_PORT", 3200, source)
    otlp_http_port = _o11y_port("SQUIRE_O11Y_OTLP_HTTP_PORT", 4318, source)
    return {
        "alloy_ready": f"http://127.0.0.1:{alloy_ready_port}/-/ready",
        "loki_ready": f"http://127.0.0.1:{loki_port}/ready",
        "prom_ready": f"http://127.0.0.1:{prom_port}/-/ready",
        "tempo_ready": f"http://127.0.0.1:{tempo_port}/ready",
        "loki_api": f"http://127.0.0.1:{loki_port}/loki/api/v1",
        "prom_api": f"http://127.0.0.1:{prom_port}/api/v1",
        "tempo_api": f"http://127.0.0.1:{tempo_port}",
        "otlp_http": f"http://127.0.0.1:{otlp_http_port}",
        "otlp_http_port": str(otlp_http_port),
    }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_run_id() -> str:
    prefix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run_{prefix}_{uuid4().hex[:10]}"


def _run(
    cmd: list[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
    stdout: Any = None,
    stderr: Any = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        timeout=timeout,
        text=True,
        stdout=stdout,
        stderr=stderr,
        check=False,
    )


def _run_shell(
    cmd: str,
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
    stdout: Any = None,
    stderr: Any = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        timeout=timeout,
        text=True,
        shell=True,
        stdout=stdout,
        stderr=stderr,
        check=False,
    )


def _git_sha() -> str:
    result = _run(["git", "rev-parse", "HEAD"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode == 0:
        return (result.stdout or "").strip() or "unknown"
    return "unknown"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _summary_path(run_dir: Path) -> Path:
    return run_dir / "summary.json"


def _write_summary(run_dir: Path, summary: dict[str, Any]) -> None:
    _write_json(_summary_path(run_dir), summary)


def _load_summary(run_dir: Path) -> dict[str, Any]:
    summary = _read_json(_summary_path(run_dir))
    return summary if isinstance(summary, dict) else {}


def _set_summary_fields(run_dir: Path, **fields: Any) -> dict[str, Any]:
    summary = _load_summary(run_dir)
    summary.update(fields)
    artifacts = summary.get("artifacts")
    if not isinstance(artifacts, dict):
        summary["artifacts"] = {}
    _write_summary(run_dir, summary)
    return summary


def _summary_artifacts(summary: dict[str, Any]) -> dict[str, Any]:
    raw = summary.get("artifacts")
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def _write_current_run_id(run_id: str) -> None:
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    CURRENT_RUN_ID_PATH.write_text(f"{run_id}\n", encoding="utf-8")


def _read_current_run_id() -> str:
    if not CURRENT_RUN_ID_PATH.exists():
        raise RuntimeError("No active harness run id found. Run harness-bootstrap first.")
    run_id = CURRENT_RUN_ID_PATH.read_text(encoding="utf-8").strip()
    if not run_id:
        raise RuntimeError("Current harness run id is empty.")
    return run_id


def _run_dir(run_id: str) -> Path:
    return RUNS_ROOT / run_id


def _ensure_run_id(value: str | None) -> str:
    if value and value.strip():
        return value.strip()
    env_run_id = os.getenv("SQUIRE_RUN_ID", "").strip()
    if env_run_id:
        return env_run_id
    return _generate_run_id()


def _resolve_run_id(value: str | None) -> str:
    if value and value.strip():
        return value.strip()
    return _read_current_run_id()


def _mode(value: str | None) -> str:
    candidate = (value or os.getenv("SQUIRE_HARNESS_MODE", "") or DEFAULT_MODE).strip().lower()
    if candidate not in {"deterministic", "integration-smoke"}:
        return DEFAULT_MODE
    return candidate


def _env_name(value: str | None) -> str:
    return (value or os.getenv("SQUIRE_ENV", "") or DEFAULT_ENV).strip().lower() or DEFAULT_ENV


def _fixed_now(value: str | None) -> str:
    return (value or os.getenv("SQUIRE_HARNESS_NOW", "") or DEFAULT_FIXED_NOW).strip() or DEFAULT_FIXED_NOW


def _port_is_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as exc:
            if exc.errno in {1, 13}:  # operation not permitted in restricted sandboxes
                return True
            return False
    return True


def _default_o11y_project(run_id: str) -> str:
    digest = hashlib.sha1(f"{ROOT}:{run_id}".encode("utf-8")).hexdigest()[:12]
    return f"squire-{digest}"


def _derive_o11y_isolation(run_id: str) -> dict[str, str]:
    explicit_project = os.getenv("SQUIRE_O11Y_PROJECT", "").strip()
    project = explicit_project or _default_o11y_project(run_id)
    explicit_ports: dict[str, int] = {}
    for key in O11Y_PORT_DEFAULTS:
        raw = os.getenv(key, "").strip()
        if not raw:
            continue
        try:
            candidate = int(raw)
        except ValueError:
            continue
        if candidate > 0:
            explicit_ports[key] = candidate

    digest = hashlib.sha1(f"{ROOT}:{run_id}".encode("utf-8")).hexdigest()
    seed = int(digest[:8], 16)
    max_offsets = 2000

    for index in range(max_offsets):
        offset = (seed + index) % max_offsets
        candidate_ports: dict[str, int] = {}
        for key, base in O11Y_PORT_DEFAULTS.items():
            if key in explicit_ports:
                candidate_ports[key] = explicit_ports[key]
            else:
                candidate_ports[key] = base + offset

        unique_ports = set(candidate_ports.values())
        if len(unique_ports) != len(candidate_ports):
            continue
        if all(_port_is_available(port) for port in unique_ports):
            result = {"SQUIRE_O11Y_PROJECT": project}
            result.update({key: str(value) for key, value in candidate_ports.items()})
            return result

    raise RuntimeError(
        "Unable to allocate a free local observability port bundle. "
        "Set explicit SQUIRE_O11Y_*_PORT overrides and retry."
    )


def _bootstrap(run_id: str, mode: str, env_name: str, fixed_now: str) -> Path:
    run_dir = _run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    isolation = _derive_o11y_isolation(run_id)
    run_env_path = run_dir / "run.env"
    run_env_lines = [
        f"SQUIRE_RUN_ID={run_id}",
        f"SQUIRE_ENV={env_name}",
        f"SQUIRE_HARNESS_MODE={mode}",
        f"SQUIRE_HARNESS_NOW={fixed_now}",
    ]
    run_env_lines.extend(f"{key}={value}" for key, value in isolation.items())
    run_env_path.write_text(
        "\n".join(run_env_lines) + "\n",
        encoding="utf-8",
    )

    started_at = _utc_now_iso()
    summary = {
        "run_id": run_id,
        "git_sha": _git_sha(),
        "mode": mode,
        "started_at": started_at,
        "finished_at": started_at,
        "duration_ms": 0,
        "status": "running",
        "artifacts": {
            "summary.json": "summary.json",
            "run.env": "run.env",
        },
    }
    _write_summary(run_dir, summary)
    _write_current_run_id(run_id)
    return run_dir


def _parse_run_env(run_dir: Path) -> dict[str, str]:
    env_path = run_dir / "run.env"
    if not env_path.exists():
        raise RuntimeError(f"Missing run env file: {env_path}")

    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        token = line.strip()
        if not token or token.startswith("#") or "=" not in token:
            continue
        key, value = token.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _try_load_run_env(run_id: str) -> dict[str, str]:
    run_dir = _run_dir(run_id)
    env_path = run_dir / "run.env"
    if not env_path.exists():
        return {}
    values = _parse_run_env(run_dir)
    values.setdefault("SQUIRE_RUN_ID", run_id)
    return values


def _optional_run_values(run_id: str | None) -> dict[str, str]:
    candidate = (run_id or os.getenv("SQUIRE_RUN_ID", "")).strip()
    if not candidate and CURRENT_RUN_ID_PATH.exists():
        try:
            candidate = _read_current_run_id()
        except RuntimeError:
            candidate = ""
    if not candidate:
        return {}
    return _try_load_run_env(candidate) or {"SQUIRE_RUN_ID": candidate}


def _compose_env(run_values: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    if run_values:
        env.update(run_values)

    project = env.get("SQUIRE_O11Y_PROJECT", "").strip()
    if project:
        env["COMPOSE_PROJECT_NAME"] = project

    explicit_platform = env.get("SQUIRE_O11Y_DOCKER_PLATFORM", "").strip()
    if explicit_platform:
        env["DOCKER_DEFAULT_PLATFORM"] = explicit_platform
    else:
        # Prefer Docker's native platform selection for local host stability.
        env.pop("DOCKER_DEFAULT_PLATFORM", None)
    return env


def _wait_for_health(url: str, timeout_seconds: int) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=2) as response:  # noqa: S310
                if 200 <= response.status < 300:
                    return True
        except (URLError, OSError, RuntimeError):
            time.sleep(1)
            continue
        time.sleep(1)
    return False


def _harness_up(run_values: dict[str, str] | None = None) -> None:
    result = _run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"],
        env=_compose_env(run_values),
    )
    if result.returncode != 0:
        raise RuntimeError("Failed to start local observability stack.")

    endpoints = _o11y_endpoints(run_values)
    checks = [
        endpoints["alloy_ready"],
        endpoints["loki_ready"],
        endpoints["prom_ready"],
        endpoints["tempo_ready"],
    ]
    for url in checks:
        if not _wait_for_health(url, STACK_STARTUP_TIMEOUT_SECONDS):
            raise RuntimeError(f"Timed out waiting for service health: {url}")

    otlp_ready = False
    otlp_http_port = int(endpoints["otlp_http_port"])
    deadline = time.time() + STACK_STARTUP_TIMEOUT_SECONDS
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", otlp_http_port), timeout=2):
                otlp_ready = True
                break
        except OSError:
            time.sleep(1)
    if not otlp_ready:
        raise RuntimeError(f"Timed out waiting for Alloy OTLP HTTP listener on 127.0.0.1:{otlp_http_port}")


def _harness_down(run_values: dict[str, str] | None = None) -> None:
    _run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "down", "-v", "--remove-orphans"],
        env=_compose_env(run_values),
    )


def _as_run_env(base: dict[str, str], run_values: dict[str, str]) -> dict[str, str]:
    merged = dict(base)
    merged.update(run_values)
    return merged


def _emit_deterministic_telemetry(run_values: dict[str, str], log_path: Path) -> subprocess.CompletedProcess[str]:
    del log_path
    endpoint = run_values.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip() or _o11y_endpoints(run_values)["otlp_http"]
    env = _as_run_env(
        os.environ.copy(),
        {
            **run_values,
            "OTEL_ENABLED": "true",
            "OTEL_EXPORTER_OTLP_ENDPOINT": endpoint,
        },
    )

    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "emit-telemetry",
        "--run-id",
        run_values["SQUIRE_RUN_ID"],
        "--environment",
        run_values.get("SQUIRE_ENV", DEFAULT_ENV),
        "--otlp-endpoint",
        endpoint,
    ]
    return _run(
        cmd,
        env=env,
        timeout=120,
    )


def _run_integration_smoke(
    run_values: dict[str, str],
    *,
    timeout_seconds: int,
    retries: int,
    log_path: Path,
) -> tuple[str, str]:
    smoke_command = os.getenv("SQUIRE_SMOKE_COMMAND", "").strip()
    if not smoke_command:
        return ("blocked", "SQUIRE_SMOKE_COMMAND is not set.")

    attempts = retries + 1
    env = _as_run_env(os.environ.copy(), run_values)
    with log_path.open("a", encoding="utf-8") as handle:
        for attempt in range(1, attempts + 1):
            handle.write(f"# integration_smoke attempt {attempt}/{attempts}\n")
            handle.flush()
            result = _run_shell(
                smoke_command,
                env=env,
                timeout=timeout_seconds,
                stdout=handle,
                stderr=subprocess.STDOUT,
            )
            if result.returncode == 0:
                return ("pass", f"integration smoke passed on attempt {attempt}")
        return ("blocked", "integration smoke failed after retry budget")


def _run_deterministic(run_dir: Path, run_values: dict[str, str], mode: str) -> tuple[str, str]:
    run_env = _as_run_env(os.environ.copy(), run_values)
    log_path = run_dir / "squire.log.jsonl"

    test_result = _run(
        [sys.executable, "-m", "pytest", "-q"],
        env=run_env,
        timeout=DETERMINISTIC_TIMEOUT_SECONDS,
    )
    if test_result.returncode != 0:
        return ("failed", "pytest failed")

    telemetry_result = _emit_deterministic_telemetry(run_values, log_path)
    if telemetry_result.returncode != 0:
        return ("failed", "telemetry emission failed")

    if mode == "integration-smoke":
        smoke_status, smoke_note = _run_integration_smoke(
            run_values,
            timeout_seconds=INTEGRATION_TIMEOUT_SECONDS,
            retries=INTEGRATION_RETRIES,
            log_path=log_path,
        )
        if smoke_status != "pass":
            return (smoke_status, smoke_note)

    return ("pass", "deterministic checks completed")


def _read_window(run_dir: Path) -> tuple[str, str]:
    summary = _load_summary(run_dir)
    start = str(summary.get("started_at") or _utc_now_iso())
    end = _utc_now_iso()
    return (start, end)


def _inspect(run_dir: Path, run_values: dict[str, str]) -> subprocess.CompletedProcess[str]:
    start, end = _read_window(run_dir)
    endpoints = _o11y_endpoints(run_values)
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "harness" / "query_o11y.py"),
        "all",
        "--run-id",
        run_values["SQUIRE_RUN_ID"],
        "--start",
        start,
        "--end",
        end,
        "--loki-endpoint",
        endpoints["loki_api"],
        "--prom-endpoint",
        endpoints["prom_api"],
        "--tempo-endpoint",
        endpoints["tempo_api"],
        "--out-dir",
        str(run_dir),
    ]
    return _run(cmd, timeout=120)


def _validate(run_dir: Path, mode: str) -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "harness" / "assert_o11y.py"),
        "--run-dir",
        str(run_dir),
        "--mode",
        mode,
    ]
    return _run(cmd, timeout=120)


def _is_docs_only_change() -> bool:
    result = _run(["git", "status", "--porcelain"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        return False

    changed = []
    for line in (result.stdout or "").splitlines():
        token = line.strip()
        if not token:
            continue
        if len(token) < 4:
            continue
        changed.append(token[3:])

    if not changed:
        return False

    docs_prefixes = (
        "docs/",
        ".agent/",
    )
    docs_files = {
        "README.md",
        "AGENTS.md",
    }
    for path in changed:
        if path in docs_files:
            continue
        if path.startswith(docs_prefixes):
            continue
        return False
    return True


def _dependency_manifests_changed() -> bool:
    result = _run(["git", "status", "--porcelain"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        return False
    candidates = {
        "pyproject.toml",
        "uv.lock",
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-dev.in",
    }
    for line in (result.stdout or "").splitlines():
        token = line.strip()
        if len(token) < 4:
            continue
        path = token[3:]
        if path in candidates:
            return True
    return False


def _run_dependency_sync() -> tuple[str, str]:
    if not _dependency_manifests_changed():
        return ("pass", "dependency manifests unchanged")

    if shutil.which("uv"):
        result = _run(["uv", "sync"], timeout=900)
        if result.returncode == 0:
            return ("pass", "uv sync completed")
        return ("failed", "uv sync failed")

    result = _run([sys.executable, "-m", "pip", "install", "-e", ".[dev]"], timeout=900)
    if result.returncode == 0:
        return ("pass", "pip install -e .[dev] completed")
    return ("failed", "pip install -e .[dev] failed")


def _finalize_summary(run_dir: Path, status: str) -> None:
    summary = _load_summary(run_dir)
    started_raw = summary.get("started_at")
    finished_at = _utc_now_iso()
    duration_ms = 0
    if isinstance(started_raw, str):
        try:
            started = datetime.fromisoformat(started_raw)
            finished = datetime.fromisoformat(finished_at)
            duration_ms = max(0, int((finished - started).total_seconds() * 1000))
        except ValueError:
            duration_ms = 0

    artifacts = _summary_artifacts(summary)
    artifacts["summary.json"] = "summary.json"
    _set_summary_fields(
        run_dir,
        finished_at=finished_at,
        duration_ms=duration_ms,
        status=status,
        artifacts=artifacts,
    )


def command_bootstrap(args: argparse.Namespace) -> int:
    run_id = _ensure_run_id(args.run_id)
    mode = _mode(args.mode)
    env_name = _env_name(args.environment)
    fixed_now = _fixed_now(args.fixed_now)
    _bootstrap(run_id, mode, env_name, fixed_now)
    print(run_id)
    return 0


def command_up(args: argparse.Namespace) -> int:
    run_values = _optional_run_values(args.run_id)
    _harness_up(run_values if run_values else None)
    return 0


def command_run(args: argparse.Namespace) -> int:
    run_id = _resolve_run_id(args.run_id)
    run_dir = _run_dir(run_id)
    run_values = _parse_run_env(run_dir)
    mode = _mode(args.mode or run_values.get("SQUIRE_HARNESS_MODE"))

    artifacts = _summary_artifacts(_load_summary(run_dir))
    artifacts["squire.log.jsonl"] = "squire.log.jsonl"
    _set_summary_fields(run_dir, mode=mode, artifacts=artifacts)

    status, note = _run_deterministic(run_dir, run_values, mode)
    _set_summary_fields(run_dir, status=status, run_note=note)
    return 0 if status == "pass" else 1


def command_inspect(args: argparse.Namespace) -> int:
    run_id = _resolve_run_id(args.run_id)
    run_dir = _run_dir(run_id)
    run_values = _parse_run_env(run_dir)

    result = _inspect(run_dir, run_values)
    artifacts = _summary_artifacts(_load_summary(run_dir))
    artifacts.update(
        {
            "loki.json": "loki.json",
            "prom.json": "prom.json",
            "tempo.json": "tempo.json",
        }
    )
    _set_summary_fields(run_dir, artifacts=artifacts)
    return result.returncode


def command_validate(args: argparse.Namespace) -> int:
    run_id = _resolve_run_id(args.run_id)
    run_dir = _run_dir(run_id)
    run_values = _parse_run_env(run_dir)
    mode = _mode(args.mode or run_values.get("SQUIRE_HARNESS_MODE"))

    result = _validate(run_dir, mode)
    assertions = _read_json(run_dir / "assertions.json")
    status = str(assertions.get("status") or "failed")
    if result.returncode != 0 and status not in {"failed", "blocked"}:
        status = "failed"
    _finalize_summary(run_dir, status)

    artifacts = _summary_artifacts(_load_summary(run_dir))
    artifacts["assertions.json"] = "assertions.json"
    _set_summary_fields(run_dir, artifacts=artifacts)

    return result.returncode


def command_down(args: argparse.Namespace) -> int:
    run_values = _optional_run_values(args.run_id)
    _harness_down(run_values if run_values else None)
    return 0


def _run_harness_lifecycle(run_id: str, mode: str, env_name: str, fixed_now: str) -> tuple[int, Path]:
    run_dir = _bootstrap(run_id, mode, env_name, fixed_now)
    run_values = _parse_run_env(run_dir)
    status = "failed"
    code = 1

    try:
        _harness_up(run_values)
        run_status, note = _run_deterministic(run_dir, run_values, mode)
        artifacts = _summary_artifacts(_load_summary(run_dir))
        artifacts["squire.log.jsonl"] = "squire.log.jsonl"
        _set_summary_fields(run_dir, status=run_status, run_note=note, artifacts=artifacts)
        if run_status != "pass":
            status = run_status
            _finalize_summary(run_dir, status)
            return (1, run_dir)

        inspect_result = _inspect(run_dir, run_values)
        artifacts = _summary_artifacts(_load_summary(run_dir))
        artifacts.update(
            {
                "loki.json": "loki.json",
                "prom.json": "prom.json",
                "tempo.json": "tempo.json",
            }
        )
        _set_summary_fields(run_dir, artifacts=artifacts)
        if inspect_result.returncode != 0:
            status = "blocked"
            _finalize_summary(run_dir, status)
            return (1, run_dir)

        validate_result = _validate(run_dir, mode)
        artifacts = _summary_artifacts(_load_summary(run_dir))
        artifacts["assertions.json"] = "assertions.json"
        _set_summary_fields(run_dir, artifacts=artifacts)
        assertions = _read_json(run_dir / "assertions.json")
        status = str(assertions.get("status") or "failed")
        _finalize_summary(run_dir, status)

        if validate_result.returncode == 0 and status == "pass":
            code = 0
        elif status == "blocked":
            code = 2
        else:
            code = 1
        return (code, run_dir)
    finally:
        _harness_down(run_values)


def command_harness(args: argparse.Namespace) -> int:
    run_id = _ensure_run_id(args.run_id)
    mode = _mode(args.mode)
    env_name = _env_name(args.environment)
    fixed_now = _fixed_now(args.fixed_now)
    code, run_dir = _run_harness_lifecycle(run_id, mode, env_name, fixed_now)
    print(run_dir.name)
    return code


def command_verify_session(args: argparse.Namespace) -> int:
    dependency_status, dependency_note = _run_dependency_sync()

    run_id = _ensure_run_id(args.run_id)
    mode = "deterministic"
    env_name = _env_name(args.environment)
    fixed_now = _fixed_now(args.fixed_now)

    deterministic_code, run_dir = _run_harness_lifecycle(run_id, mode, env_name, fixed_now)
    deterministic_summary = _load_summary(run_dir)
    deterministic_status = str(deterministic_summary.get("status") or "failed")

    docs_only = _is_docs_only_change()
    if docs_only:
        integration_status = "pass"
        integration_note = "integration smoke skipped for docs-only change"
    else:
        run_values = _parse_run_env(run_dir)
        smoke_status, smoke_note = _run_integration_smoke(
            run_values,
            timeout_seconds=INTEGRATION_TIMEOUT_SECONDS,
            retries=INTEGRATION_RETRIES,
            log_path=run_dir / "squire.log.jsonl",
        )
        integration_status = smoke_status
        integration_note = smoke_note

    statuses = [dependency_status, deterministic_status, integration_status]
    if "failed" in statuses:
        overall_status = "failed"
    elif "blocked" in statuses:
        overall_status = "blocked"
    else:
        overall_status = "pass"

    session_gate = {
        "run_id": run_id,
        "git_sha": _git_sha(),
        "timestamp": _utc_now_iso(),
        "checks": {
            "dependency_sync": {
                "status": dependency_status,
                "notes": dependency_note,
            },
            "deterministic": {
                "status": deterministic_status,
                "notes": f"harness exit code {deterministic_code}",
            },
            "integration_smoke": {
                "status": integration_status,
                "notes": integration_note,
            },
        },
        "status": overall_status,
    }
    _write_json(run_dir / "session_gate.json", session_gate)

    summary = _load_summary(run_dir)
    artifacts = _summary_artifacts(summary)
    artifacts["session_gate.json"] = "session_gate.json"
    _set_summary_fields(run_dir, artifacts=artifacts)

    print(run_id)
    return 0 if overall_status == "pass" else 1


def command_emit_telemetry(args: argparse.Namespace) -> int:
    try:
        from opentelemetry import metrics as otel_metrics
        from opentelemetry import trace as otel_trace
    except ModuleNotFoundError as exc:
        print(
            "Missing dependency for telemetry emission: opentelemetry. "
            "Use .venv/bin/python or install dependencies (pip install -e '.[dev]').",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc

    from squire_core.observability import (
        JsonLogFormatter,
        ObservabilityConfig,
        configure_logging,
        increment_counter,
        initialize_observability,
        log_event,
        observe_stage,
        set_run_id,
        set_runtime_environment,
    )

    run_values = _try_load_run_env(args.run_id)
    endpoints = _o11y_endpoints(run_values if run_values else None)
    resolved_otlp_endpoint = (
        (args.otlp_endpoint or "").strip()
        or run_values.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
        or endpoints["otlp_http"]
    )

    set_run_id(args.run_id)
    set_runtime_environment(args.environment)
    configure_logging("DEBUG")

    run_dir = _run_dir(args.run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "squire.log.jsonl"
    root_logger = logging.getLogger()
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(JsonLogFormatter())
    root_logger.addHandler(file_handler)

    config = ObservabilityConfig(
        enabled=True,
        service_name="squire-core",
        environment=args.environment,
        log_level="INFO",
        otlp_endpoint=resolved_otlp_endpoint,
        otlp_headers=os.getenv("OTEL_EXPORTER_OTLP_HEADERS"),
    )
    initialize_observability(config)
    try:
        stages = [
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
        increment_counter("squire_messages_total", entrypoint="harness")
        increment_counter("squire_decision_outcomes_total", outcome="create")
        increment_counter("squire_pending_actions_total", status="none")

        for stage in stages:
            with observe_stage(stage, harness_mode="deterministic"):
                time.sleep(0.01)

        log_event(
            20,
            "harness_telemetry_emitted",
            "harness_telemetry_emitted",
            pipeline_stage="harness",
            decision_action="create",
            retrieval_mode="deterministic",
            object_type="admin",
        )

        tracer_provider = otel_trace.get_tracer_provider()
        if hasattr(tracer_provider, "force_flush"):
            tracer_provider.force_flush()  # type: ignore[call-arg]

        meter_provider = otel_metrics.get_meter_provider()
        if hasattr(meter_provider, "force_flush"):
            meter_provider.force_flush()  # type: ignore[call-arg]

        time.sleep(2)
    finally:
        root_logger.removeHandler(file_handler)
        file_handler.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Squire harness lifecycle runner")
    sub = parser.add_subparsers(dest="command", required=True)

    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--run-id", help="Harness run id")
    shared.add_argument("--mode", choices=["deterministic", "integration-smoke"], help="Harness mode")
    shared.add_argument("--environment", help="Runtime environment label")
    shared.add_argument("--fixed-now", help="Fixed harness timestamp for deterministic mode")

    sub.add_parser("bootstrap", parents=[shared])
    sub.add_parser("up", parents=[shared])
    sub.add_parser("run", parents=[shared])
    sub.add_parser("inspect", parents=[shared])
    sub.add_parser("validate", parents=[shared])
    sub.add_parser("down", parents=[shared])
    sub.add_parser("harness", parents=[shared])
    sub.add_parser("verify-session", parents=[shared])

    emit = sub.add_parser("emit-telemetry")
    emit.add_argument("--run-id", required=True)
    emit.add_argument("--environment", default=DEFAULT_ENV)
    emit.add_argument("--otlp-endpoint")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    handlers = {
        "bootstrap": command_bootstrap,
        "up": command_up,
        "run": command_run,
        "inspect": command_inspect,
        "validate": command_validate,
        "down": command_down,
        "harness": command_harness,
        "verify-session": command_verify_session,
        "emit-telemetry": command_emit_telemetry,
    }
    handler = handlers[args.command]
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
