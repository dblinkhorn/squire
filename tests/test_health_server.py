import json
import time
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from squire_core.transport.discord import flow


def test_parse_health_port_defaults_and_disable() -> None:
    assert flow._parse_health_port(None) == 8080
    assert flow._parse_health_port("") == 8080
    assert flow._parse_health_port("9090") == 9090
    assert flow._parse_health_port("0") is None


def test_parse_health_port_rejects_invalid_values() -> None:
    with pytest.raises(ValueError):
        flow._parse_health_port("abc")

    with pytest.raises(ValueError):
        flow._parse_health_port("70000")


def test_health_endpoint_reports_ok() -> None:
    server = flow._HealthServer("127.0.0.1", 0)
    server.start()
    try:
        url = f"http://127.0.0.1:{server.port}/health"
        for _ in range(25):
            try:
                with urlopen(url, timeout=1.0) as response:
                    assert response.status == 200
                    assert response.headers.get("Content-Type", "").startswith("application/json")
                    assert json.loads(response.read().decode("utf-8")) == {"status": "ok"}
                    return
            except OSError:
                time.sleep(0.02)
        pytest.fail("health endpoint did not become ready in time")
    finally:
        server.stop()


def test_health_endpoint_unknown_path_is_404() -> None:
    server = flow._HealthServer("127.0.0.1", 0)
    server.start()
    try:
        url = f"http://127.0.0.1:{server.port}/not-health"
        for _ in range(25):
            try:
                with pytest.raises(HTTPError) as exc:
                    urlopen(url, timeout=1.0)
                assert exc.value.code == 404
                return
            except OSError:
                time.sleep(0.02)
        pytest.fail("health endpoint did not become ready in time")
    finally:
        server.stop()
