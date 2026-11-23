"""Giao diện Telegram cho một giai đoạn sau. Không có client mạng."""

from shop_integration.telegram_adapter.interfaces import (
    CatalogQuery,
    OutboundMessage,
    ReportQuery,
    SyncCommand,
    TelegramDisabledNotifier,
    TelegramNotifier,
    build_notifier,
)

__all__ = [
    "CatalogQuery",
    "OutboundMessage",
    "ReportQuery",
    "SyncCommand",
    "TelegramDisabledNotifier",
    "TelegramNotifier",
    "build_notifier",
]
