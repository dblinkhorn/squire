"""Health endpoint helpers shared by runtime entrypoints."""

from __future__ import annotations

import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_HEALTH_HOST = "0.0.0.0"
DEFAULT_HEALTH_PORT = 8080


class HealthRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        if path not in ("/health", "/health/"):
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        payload = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        # Keep health probe noise out of default stderr logging.
        logging.debug("health_http %s", format % args)


class HealthServer:
    def __init__(self, host: str, port: int) -> None:
        self._server = ThreadingHTTPServer((host, port), HealthRequestHandler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="squire-health-server",
            daemon=True,
        )

    @property
    def port(self) -> int:
        return int(self._server.server_port)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)


def parse_health_port(value: str | None) -> int | None:
    if value is None:
        return DEFAULT_HEALTH_PORT

    trimmed = value.strip()
    if not trimmed:
        return DEFAULT_HEALTH_PORT
    if not trimmed.isdigit():
        raise ValueError("HEALTH_PORT must be an integer between 0 and 65535.")

    parsed = int(trimmed)
    if parsed > 65535:
        raise ValueError("HEALTH_PORT must be between 0 and 65535.")
    if parsed == 0:
        return None
    return parsed


def start_health_server() -> HealthServer | None:
    host = os.getenv("HEALTH_HOST", DEFAULT_HEALTH_HOST).strip() or DEFAULT_HEALTH_HOST
    try:
        port = parse_health_port(os.getenv("HEALTH_PORT"))
    except ValueError as exc:
        logging.error("health_server_disabled reason=invalid_port error=%s", exc)
        return None

    if port is None:
        logging.info("health_server_disabled reason=port_zero")
        return None

    try:
        server = HealthServer(host, port)
    except OSError as exc:
        logging.error("health_server_start_failed host=%s port=%s error=%s", host, port, exc)
        return None

    server.start()
    logging.info("health_server_started host=%s port=%s", host, server.port)
    return server
