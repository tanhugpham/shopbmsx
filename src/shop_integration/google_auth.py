"""Service account cho file Google có sẵn. Không xin quyền Drive."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from shop_integration.config import resolve_credentials_path
from shop_integration.errors import ConfigError

SPREADSHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
DOCUMENTS_SCOPE = "https://www.googleapis.com/auth/documents"
WORKSPACE_SCOPES = [SPREADSHEETS_SCOPE, DOCUMENTS_SCOPE]


def load_service_account_credentials(path: str, scopes: list[str]) -> Any:
    file_path = resolve_credentials_path(path)
    if not file_path.is_file():
        raise ConfigError(
            "Không tìm thấy tệp thông tin xác thực Google "
            f"tại '{file_path}'. Kiểm tra GOOGLE_CREDENTIALS_FILE trong .env. "
            "Đường dẫn tương đối được tính từ thư mục gốc dự án."
        )
    try:
        info = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ConfigError(
            "Tệp thông tin xác thực Google không phải JSON hợp lệ. Nội dung tệp không được ghi vào nhật ký."
        ) from None
    if not isinstance(info, dict) or info.get("type") != "service_account":
        raise ConfigError("Chỉ hỗ trợ JSON service account (type=service_account).")
    from google.oauth2 import service_account

    try:
        return service_account.Credentials.from_service_account_info(info, scopes=list(scopes))
    except Exception:
        raise ConfigError(
            "Không đọc được service account. Kiểm tra tệp JSON và không gửi tệp này cho người khác."
        ) from None


def google_status_code(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    response = getattr(exc, "resp", None)
    if status is None and response is not None:
        status = getattr(response, "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def translate_google_error(exc: Exception) -> Exception:
    from shop_integration.errors import GoogleWorkspaceError

    if isinstance(exc, GoogleWorkspaceError):
        return exc
    status = google_status_code(exc)
    if _is_timeout(exc):
        return GoogleWorkspaceError("Hết thời gian chờ khi gọi Google.")
    if status == 404:
        return GoogleWorkspaceError(
            "Không tìm thấy Google Spreadsheet hoặc Document. Kiểm tra ID và quyền chia sẻ."
        )
    if status == 403:
        return GoogleWorkspaceError(
            "Service account chưa được chia sẻ quyền sửa file Google tương ứng."
        )
    if status == 429:
        return GoogleWorkspaceError("Google đang giới hạn tần suất (HTTP 429).")
    if status == 408 or (status is not None and 500 <= status <= 599):
        return GoogleWorkspaceError(f"Google tạm thời không phản hồi (HTTP {status}).")
    return GoogleWorkspaceError(
        "Lỗi Google Workspace. Kiểm tra thông tin xác thực và quyền truy cập."
    )


def call_google(
    operation: Callable[[], Any],
    *,
    retry: bool,
    attempts: int = 3,
    sleep: Callable[[float], None] | None = None,
) -> Any:
    """Đọc được thử lại. Ghi không thử lại để tránh nhân đôi nội dung."""

    pause = sleep or time.sleep
    total = attempts if retry else 1
    last_error: Exception | None = None
    for attempt in range(1, total + 1):
        try:
            return operation()
        except Exception as exc:
            last_error = exc
            if not retry or attempt >= total or not _is_retryable(exc):
                raise translate_google_error(exc) from None
            pause(min(0.2 * attempt, 1.0))
    raise translate_google_error(last_error or RuntimeError("Google không trả về kết quả.")) from None


def _is_timeout(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    return isinstance(exc, TimeoutError) or "timeout" in name


def _is_retryable(exc: Exception) -> bool:
    if _is_timeout(exc):
        return True
    status = google_status_code(exc)
    if status == 429 or status == 408:
        return True
    return status is not None and 500 <= status <= 599
