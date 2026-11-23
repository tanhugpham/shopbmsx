from __future__ import annotations

from threading import Thread

import httpx
from http.server import ThreadingHTTPServer

from shop_integration.web import SyncHandler, apply_runtime_guards, request_authorized


def _server() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), SyncHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


def test_health_is_public_and_read_only() -> None:
    apply_runtime_guards()
    server, base = _server()
    try:
        response = httpx.get(f"{base}/health", timeout=5)
        root = httpx.get(f"{base}/", timeout=5)
    finally:
        server.shutdown()
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["dry_run"] is True
    assert payload["enable_purchase"] is False
    assert root.json()["status"] == "ok"
    assert "sxn_live_" not in response.text


def test_sync_without_token_is_forbidden(monkeypatch) -> None:
    monkeypatch.delenv("SYNC_TOKEN", raising=False)
    server, base = _server()
    try:
        response = httpx.post(f"{base}/sync-sources", timeout=5)
    finally:
        server.shutdown()
    assert response.status_code == 403
    assert "SYNC_TOKEN" in response.text


def test_sync_with_token_runs_dry_command(monkeypatch) -> None:
    monkeypatch.setenv("SYNC_TOKEN", "token-value-for-tests")
    monkeypatch.setenv("DRY_RUN", "false")
    called: list[int] = []

    def fake_run() -> tuple[int, str]:
        called.append(1)
        return 0, "Dry-run: khong ghi Google Sheets."

    monkeypatch.setattr("shop_integration.web.run_sync_sources", fake_run)
    server, base = _server()
    try:
        denied = httpx.post(f"{base}/sync-sources", timeout=5)
        ok = httpx.post(
            f"{base}/sync-sources",
            headers={"X-Sync-Token": "token-value-for-tests"},
            timeout=5,
        )
    finally:
        server.shutdown()
    assert denied.status_code == 403
    assert ok.status_code == 200
    assert "Dry-run" in ok.text
    assert called == [1]


def test_request_authorized_accepts_bearer(monkeypatch) -> None:
    monkeypatch.setenv("SYNC_TOKEN", "token-value-for-tests")
    assert request_authorized({"Authorization": "Bearer token-value-for-tests"}) is True
    assert request_authorized({"X-Sync-Token": "wrong"}) is False


def test_unknown_path_is_404() -> None:
    server, base = _server()
    try:
        response = httpx.get(f"{base}/orders", timeout=5)
    finally:
        server.shutdown()
    assert response.status_code == 404
