"""Hợp đồng cho bot Telegram sau này.

Không có phương thức tạo đơn, báo giá tự động, hoặc lấy tài khoản đã giao.
ENABLE_TELEGRAM không bật được bộ điều hợp vì chưa có hiện thực mạng.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict

from shop_integration.config import Settings
from shop_integration.errors import TelegramDisabledError
from shop_integration.models import Product, SyncReport


class OutboundMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    chat_id: str
    text: str


class CatalogQuery(Protocol):
    def search_products(self, text: str) -> list[Product]: ...


class SyncCommand(Protocol):
    def sync_catalog(self) -> SyncReport: ...


class ReportQuery(Protocol):
    def latest_report(self) -> str: ...


class TelegramNotifier(Protocol):
    def send(self, message: OutboundMessage) -> None: ...


class TelegramDisabledNotifier:
    """Điểm gắn duy nhất hiện có. Mọi lần gửi đều bị từ chối."""

    def send(self, message: OutboundMessage) -> None:
        raise TelegramDisabledError(
            "Bộ điều hợp Telegram chưa được kích hoạt. ENABLE_TELEGRAM không mở kết nối mạng."
        )


def build_notifier(settings: Settings) -> TelegramNotifier:
    del settings
    return TelegramDisabledNotifier()
