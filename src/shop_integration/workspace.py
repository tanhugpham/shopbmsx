"""Một chỗ mở Google client cho Sheets và Docs.

Dùng chung service account, scope và gateway. Không xin thêm quyền Drive.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shop_integration.config import Settings
from shop_integration.docs_service import GoogleDocsGateway
from shop_integration.errors import ConfigError
from shop_integration.google_auth import WORKSPACE_SCOPES, load_service_account_credentials
from shop_integration.sheets_service import GoogleSheetsGateway


@dataclass(frozen=True)
class Workspace:
    credentials: Any
    sheets: GoogleSheetsGateway | None = None
    docs: GoogleDocsGateway | None = None


def open_workspace(
    settings: Settings,
    *,
    sheets: bool = True,
    docs: bool = False,
) -> Workspace:
    """Mở đúng client cần dùng. Thiếu file credentials thì báo lỗi cấu hình."""

    if not settings.google_credentials_file:
        raise ConfigError("Thiếu GOOGLE_CREDENTIALS_FILE.")
    credentials = load_service_account_credentials(
        settings.google_credentials_file,
        WORKSPACE_SCOPES,
    )
    return Workspace(
        credentials=credentials,
        sheets=GoogleSheetsGateway(credentials) if sheets else None,
        docs=GoogleDocsGateway(credentials) if docs else None,
    )


def open_sheets(settings: Settings) -> GoogleSheetsGateway | None:
    """Gateway chỉ đọc/ghi Sheets. Trả None khi chưa cấu hình credentials."""

    if not settings.google_credentials_file:
        return None
    return open_workspace(settings, sheets=True).sheets
