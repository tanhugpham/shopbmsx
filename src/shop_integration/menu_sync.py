"""Đồng bộ MENU định kỳ từ BAO_GIA và NASTELEID. Không tạo đơn.

BAO_GIA được ghi từ API Shop Xoài Non. NASTELEID chỉ được đọc. MENU ghép hai sheet đó.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from shop_integration.api_client import ShopApiClient
from shop_integration.combined_quote import CombinedQuoteWriter, shop_quote_rows
from shop_integration.config import Settings
from shop_integration.google_auth import WORKSPACE_SCOPES, load_service_account_credentials
from shop_integration.logging_utils import redact
from shop_integration.pricing import policy_from_settings
from shop_integration.product_service import ProductService
from shop_integration.sheets_service import GoogleSheetsGateway
from shop_integration.text_normalize import contains_contact_handle, public_description
from shop_integration.time_utils import format_timestamp, now_vietnam

INTERVAL_SECONDS = 300
_DESCRIPTION_COLUMNS = {
    "MENU": ("DESCRIPTION",),
    "SAN_PHAM": ("Mô tả ngắn",),
    "BIEN_THE": ("Mô tả", "Mô tả gốc"),
    "BAO_GIA": ("Mô tả",),
    "BAO_GIA_TONG_HOP": ("Mô tả",),
    "NASTELEID": ("Mô tả",),
}

_lock = threading.Lock()
_scheduler_started = False
_last_status: dict[str, Any] = {
    "enabled": False,
    "interval_seconds": INTERVAL_SECONDS,
    "last_started_at": "",
    "last_finished_at": "",
    "last_status": "idle",
    "last_menu_count": 0,
    "last_cells": 0,
    "last_error": "",
}

logger = logging.getLogger("shop_integration")


@dataclass
class MenuSyncResult:
    started_at: str
    finished_at: str
    status: str
    live: bool
    shop_count: int = 0
    nastele_count: int = 0
    menu_count: int = 0
    cells_written: int = 0
    leftover_handles: int = 0
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        parts = [
            f"menu-sync status={self.status}",
            f"live={str(self.live).lower()}",
            f"shop={self.shop_count}",
            f"nastele={self.nastele_count}",
            f"menu={self.menu_count}",
            f"cells={self.cells_written}",
            f"tiensky_leftover={self.leftover_handles}",
            f"started={self.started_at}",
            f"finished={self.finished_at}",
        ]
        parts.extend(self.notes)
        parts.extend(self.errors)
        return " ".join(part for part in parts if part)


def scheduled_sync_enabled() -> bool:
    return _flag(os.environ.get("ENABLE_SCHEDULED_SYNC", "true"), default=True)


def scheduled_menu_live() -> bool:
    return _flag(os.environ.get("ENABLE_SCHEDULED_MENU_LIVE", "true"), default=True)


def scheduled_sync_seconds() -> int:
    raw = os.environ.get("SCHEDULED_SYNC_SECONDS", str(INTERVAL_SECONDS))
    try:
        value = int(raw)
    except ValueError:
        return INTERVAL_SECONDS
    return max(60, min(value, 3600))


def scheduler_status() -> dict[str, Any]:
    payload = dict(_last_status)
    payload["enabled"] = scheduled_sync_enabled()
    payload["interval_seconds"] = scheduled_sync_seconds()
    payload["started"] = _scheduler_started
    payload["live"] = scheduled_menu_live()
    payload["busy"] = _lock.locked()
    return payload


def start_menu_scheduler() -> None:
    """Một thread daemon trong process web. Restart process không tạo thread thứ hai."""

    global _scheduler_started
    if _scheduler_started or not scheduled_sync_enabled():
        return
    _scheduler_started = True
    _last_status["enabled"] = True
    _last_status["interval_seconds"] = scheduled_sync_seconds()
    thread = threading.Thread(target=_scheduler_loop, name="menu-sync", daemon=True)
    thread.start()
    logger.info(
        "menu-sync scheduler started interval=%ss live=%s",
        scheduled_sync_seconds(),
        str(scheduled_menu_live()).lower(),
    )


def run_menu_sync(
    settings: Settings,
    *,
    live: bool,
    gateway: Any | None = None,
    shop_client: Any | None = None,
) -> MenuSyncResult:
    started = now_vietnam()
    started_text = format_timestamp(started)
    if not _lock.acquire(blocking=False):
        result = MenuSyncResult(
            started_at=started_text,
            finished_at=format_timestamp(now_vietnam()),
            status="locked",
            live=live,
            notes=["Bỏ qua vì lần sync trước chưa xong."],
        )
        return result
    try:
        return _run_menu_sync(settings, live=live, gateway=gateway, shop_client=shop_client, started_text=started_text)
    finally:
        _lock.release()


def _run_menu_sync(
    settings: Settings,
    *,
    live: bool,
    gateway: Any | None,
    shop_client: Any | None,
    started_text: str,
) -> MenuSyncResult:
    notes: list[str] = []
    errors: list[str] = []
    products: list[Any] = []
    manual_count = 0
    policy = policy_from_settings(settings)
    notes.append("NASTELEID chỉ nhập tay. Không ghi sheet này từ API.")

    try:
        products, parse_notes = _load_shop_products(settings, shop_client)
        notes.extend(parse_notes)
    except Exception as exc:
        errors.append(f"Nguồn shop lỗi: {exc}")
        notes.append("Giữ BAO_GIA hiện có vì nguồn shop lỗi. MENU vẫn đọc hai sheet.")

    shop_rows = shop_quote_rows(products, policy)
    own_gateway = False
    if gateway is None:
        gateway = _open_gateway(settings)
        own_gateway = gateway is not None
    leftover = 0
    cells_written = 0
    menu_count = 0
    applied = False
    spreadsheet_id = settings.last_spreadsheet_id.strip()
    if not spreadsheet_id:
        errors.append("Thiếu LAST_SPREADSHEET_ID.")
    elif gateway is None:
        errors.append("Thiếu kết nối Google Sheets.")
    else:
        try:
            result = CombinedQuoteWriter(gateway, spreadsheet_id).update(
                shop_rows,
                dry_run=not live,
                expire_missing=True,
            )
            applied = result.applied
            menu_count = result.menu_count
            manual_count = result.manual_count
            cells_written += len(result.ranges)
            notes.extend(item for item in result.warnings if item)
            leftover, extra_cells = _sanitize_description_tabs(
                gateway,
                spreadsheet_id,
                live=live,
            )
            cells_written += extra_cells
            catalog_id = settings.google_spreadsheet_id.strip()
            if catalog_id:
                _left, extra = _sanitize_description_tabs(
                    gateway,
                    catalog_id,
                    live=live,
                    tabs=("SAN_PHAM", "BIEN_THE"),
                )
                leftover += _left
                cells_written += extra
        except Exception as exc:
            errors.append(f"Ghi MENU lỗi: {exc}")
        finally:
            if own_gateway:
                close = getattr(gateway, "close", None)
                if callable(close):
                    close()

    finished_text = format_timestamp(now_vietnam())
    status = "error" if errors and not applied and cells_written == 0 else "ok"
    if errors and (applied or cells_written):
        status = "partial"
    payload = MenuSyncResult(
        started_at=started_text,
        finished_at=finished_text,
        status=status,
        live=live,
        shop_count=len(shop_rows),
        nastele_count=manual_count,
        menu_count=menu_count,
        cells_written=cells_written,
        leftover_handles=leftover,
        notes=notes,
        errors=errors,
    )
    _remember(payload)
    logger.info(redact(payload.text, settings.secret_values()))
    return payload


def _load_shop_products(settings: Settings, shop_client: Any | None) -> tuple[list[Any], list[str]]:
    notes: list[str] = []
    if shop_client is not None:
        raw_items = shop_client.list_all_products()
        products, warnings = ProductService().parse_many(raw_items)
        notes.extend(item.message for item in warnings)
        return products, notes
    with ShopApiClient(settings) as client:
        raw_items = client.list_all_products()
        notes.extend(client.notices)
        products, warnings = ProductService().parse_many(raw_items)
        notes.extend(item.message for item in warnings)
        return products, notes


def _open_gateway(settings: Settings) -> GoogleSheetsGateway | None:
    if not settings.google_credentials_file:
        return None
    credentials = load_service_account_credentials(settings.google_credentials_file, WORKSPACE_SCOPES)
    return GoogleSheetsGateway(credentials)


def _sanitize_description_tabs(
    gateway: Any,
    spreadsheet_id: str,
    *,
    live: bool,
    tabs: tuple[str, ...] | None = None,
) -> tuple[int, int]:
    try:
        titles = set(gateway.list_sheet_titles(spreadsheet_id))
    except Exception:
        return 0, 0
    leftover = 0
    payload: list[tuple[str, list[list[Any]]]] = []
    wanted = tabs or tuple(_DESCRIPTION_COLUMNS)
    for tab in wanted:
        if tab not in titles or tab not in _DESCRIPTION_COLUMNS:
            continue
        try:
            grid = gateway.get_values(spreadsheet_id, f"'{tab}'!A1:ZZ")
        except Exception:
            continue
        if not grid:
            continue
        headers = [str(cell or "").strip() for cell in grid[0]]
        columns = [
            headers.index(name)
            for name in _DESCRIPTION_COLUMNS[tab]
            if name in headers
        ]
        if tab == "MENU":
            columns = [2]
            start = 1
            for index, row in enumerate(grid):
                cells = [str(cell or "").strip().upper() for cell in row[:8]]
                if cells[:2] == ["ID", "PRODUCT"]:
                    start = index + 1
                    break
        else:
            start = 1
        for row_number, raw in enumerate(grid[start:], start=start + 1):
            for column in columns:
                current = raw[column] if column < len(raw) else ""
                if not isinstance(current, str) or not current.strip():
                    continue
                cleaned = public_description(current)
                if cleaned != current:
                    payload.append((_a1(tab, column, row_number), [[cleaned]]))
                remaining = cleaned if live else current
                if contains_contact_handle(remaining):
                    leftover += 1
    if live and payload:
        gateway.batch_update_values(spreadsheet_id, payload, value_input_option="RAW")
    return leftover, len(payload) if live else 0


def _a1(tab: str, column: int, row: int) -> str:
    number = column + 1
    name = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        name = chr(65 + remainder) + name
    return f"'{tab}'!{name}{row}"


def _remember(result: MenuSyncResult) -> None:
    _last_status.update(
        {
            "last_started_at": result.started_at,
            "last_finished_at": result.finished_at,
            "last_status": result.status,
            "last_menu_count": result.menu_count,
            "last_cells": result.cells_written,
            "last_error": "; ".join(result.errors),
        }
    )


def _scheduler_loop() -> None:
    interval = scheduled_sync_seconds()
    time.sleep(min(15, interval))
    while True:
        try:
            settings = Settings()
            run_menu_sync(settings, live=scheduled_menu_live())
        except Exception as exc:
            _last_status["last_status"] = "error"
            _last_status["last_error"] = str(exc)
            logger.info("menu-sync scheduler error=%s", type(exc).__name__)
        time.sleep(scheduled_sync_seconds())


def _flag(value: str | None, *, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}
