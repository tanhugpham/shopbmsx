"""HTTP nhỏ cho Render Web Service miễn phí. Không tắt DRY_RUN. Không tạo đơn."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from shop_integration.config import PROJECT_ROOT, Settings
from shop_integration.logging_utils import configure_logging, redact
from shop_integration.menu_sync import (
    run_menu_sync,
    scheduled_menu_live,
    scheduler_status,
    start_menu_scheduler,
)


def apply_runtime_guards() -> None:
    os.environ["DRY_RUN"] = "true"
    os.environ["ENABLE_PURCHASE"] = "false"
    os.environ["ENABLE_TELEGRAM"] = "false"
    os.environ.setdefault("PYTHONPATH", "src")


def materialize_google_credentials() -> None:
    raw = os.environ.get("GOOGLE_CREDENTIALS_JSON", "").strip()
    if not raw:
        return
    path = Path(os.environ.get("GOOGLE_CREDENTIALS_FILE", "./GOOGLE_CREDENTIALS_FILE.json"))
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path.write_text(raw, encoding="utf-8")


def sync_token() -> str:
    return os.environ.get("SYNC_TOKEN", "").strip()


def request_authorized(headers: dict[str, str]) -> bool:
    expected = sync_token()
    if not expected:
        return False
    lowered = {key.lower(): value.strip() for key, value in headers.items()}
    header = lowered.get("x-sync-token", "")
    auth = lowered.get("authorization", "")
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    return header == expected or bearer == expected


def health_body() -> bytes:
    payload = {
        "status": "ok",
        "service": "shopxoainon",
        "dry_run": True,
        "enable_purchase": False,
        "sync_token_configured": bool(sync_token()),
        "scheduled_sync": scheduler_status(),
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def run_sync_sources() -> tuple[int, str]:
    apply_runtime_guards()
    materialize_google_credentials()
    completed = subprocess.run(
        [sys.executable, "-m", "shop_integration", "sync-sources"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        env=dict(os.environ),
        timeout=180,
        check=False,
    )
    text = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    secrets = Settings().secret_values()
    token = sync_token()
    if token:
        secrets = [*secrets, token]
    return completed.returncode, redact(text, secrets)


class SyncHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        path = urlparse(self.path).path
        sys.stderr.write("%s %s\n" % (self.command, path))

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def _dispatch(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path in {"/", "/health"}:
            self._send(200, "application/json; charset=utf-8", health_body())
            return
        if path == "/sync-sources":
            if not request_authorized(dict(self.headers)):
                self._send(403, "text/plain; charset=utf-8", b"Thieu SYNC_TOKEN.\n")
                return
            try:
                code, text = run_sync_sources()
            except subprocess.TimeoutExpired:
                self._send(504, "text/plain; charset=utf-8", b"Dong bo qua thoi gian. Xem log Render.\n")
                return
            status = 200 if code == 0 else 500
            body = (text + "\n") if text else f"exit {code}\n"
            self._send(status, "text/plain; charset=utf-8", body.encode("utf-8"))
            return
        if path == "/sync-menu":
            if not request_authorized(dict(self.headers)):
                self._send(403, "text/plain; charset=utf-8", b"Thieu SYNC_TOKEN.\n")
                return
            try:
                apply_runtime_guards()
                materialize_google_credentials()
                result = run_menu_sync(Settings(), live=scheduled_menu_live())
            except Exception:
                self._send(500, "text/plain; charset=utf-8", b"menu-sync loi. Xem log Render.\n")
                return
            status = 200 if result.status in {"ok", "locked", "partial"} else 500
            body = (result.text + "\n").encode("utf-8")
            self._send(status, "text/plain; charset=utf-8", body)
            return
        self._send(404, "text/plain; charset=utf-8", b"Not found.\n")

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def serve() -> None:
    apply_runtime_guards()
    materialize_google_credentials()
    settings = Settings()
    configure_logging(settings.secret_values())
    start_menu_scheduler()
    port = int(os.environ.get("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), SyncHandler)
    sys.stderr.write(f"shopxoainon web listening on {port}\n")
    server.serve_forever()


if __name__ == "__main__":
    serve()
