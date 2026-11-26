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
from shop_integration.combined_quote import (
    MENU_HEADERS,
    TAB_MANUAL,
    TAB_MENU,
    CombinedQuoteWriter,
    manual_quote_rows,
    shop_quote_rows,
)
from shop_integration.config import Settings
from shop_integration.errors import MenuLayoutError
from shop_integration.logging_utils import redact
from shop_integration.menu_expand import (
    MenuExpansionPlan,
    auto_expand_menu,
    auto_expand_enabled,
    menu_expansion_shift,
)
from shop_integration.menu_layout import (
    LayoutLockedGateway,
    MenuCapacity,
    MenuLayoutGuard,
    load_menu_baseline,
    menu_structure_diff,
    read_menu_baseline,
)
from shop_integration.menu_order import (
    apply_menu_order,
    may_group_similar,
    plan_menu_order,
)
from shop_integration.pricing import policy_from_settings
from shop_integration.product_service import ProductService
from shop_integration.sheet_io import cell_range
from shop_integration.text_normalize import contains_contact_handle, public_description
from shop_integration.time_utils import format_timestamp, now_vietnam
from shop_integration.workspace import open_sheets

INTERVAL_SECONDS = 300
_DESCRIPTION_COLUMNS = {
    # MENU do CombinedQuoteWriter làm sạch trong giới hạn A:H/selector. Không quét lại
    # toàn tab ở đây để tránh ghi C36 hoặc các dòng ngoài vùng chọn.
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
    """Mặc định không ghi. Chỉ bật khi đặt ENABLE_SCHEDULED_MENU_LIVE=true."""

    return _flag(os.environ.get("ENABLE_SCHEDULED_MENU_LIVE"), default=False)


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
    blocked = _guard_live_write(settings, notes, errors, live=live)
    if blocked:
        live = False

    if not blocked:
        try:
            products, parse_notes = _load_shop_products(settings, shop_client)
            notes.extend(parse_notes)
        except Exception as exc:
            errors.append(f"Nguồn shop lỗi: {exc}")
            notes.append("Giữ BAO_GIA hiện có vì nguồn shop lỗi. MENU vẫn đọc hai sheet.")

    shop_rows = shop_quote_rows(products, policy) if not blocked else []
    own_gateway = False
    if gateway is None:
        gateway = _open_gateway(settings)
        own_gateway = gateway is not None
    leftover = 0
    cells_written = 0
    menu_count = 0
    applied = False
    spreadsheet_id = settings.spreadsheet_id
    if not spreadsheet_id:
        errors.append("Thiếu GOOGLE_SPREADSHEET_ID.")
    elif gateway is None:
        errors.append("Thiếu kết nối Google Sheets.")
    elif blocked:
        notes.append("Không ghi MENU vì cổng an toàn chưa đạt.")
    elif _require_durable_backup(settings, gateway, notes, errors, live=live):
        live = False
        notes.append("Không ghi MENU vì backup bền chưa được kiểm tra.")
    else:
        layout_guard: MenuLayoutGuard | None = None
        layout_blocked = False
        try:
            layout_guard = _menu_layout_guard(
                gateway,
                spreadsheet_id,
                started_text,
                notes,
            )
        except MenuLayoutError as exc:
            errors.append(str(exc))
            layout_blocked = True
        if not layout_blocked:
            expanded = _expand_menu_capacity(
                gateway,
                spreadsheet_id,
                len(shop_rows),
                live=live,
                notes=notes,
                errors=errors,
            )
            if expanded is not None and expanded.rows > 0 and live:
                try:
                    layout_guard = _menu_layout_guard(
                        gateway,
                        spreadsheet_id,
                        started_text,
                        notes,
                    )
                except MenuLayoutError as exc:
                    errors.append(str(exc))
                    layout_blocked = True
        if not layout_blocked:
            guarded = (
                LayoutLockedGateway(gateway, layout_guard) if layout_guard else gateway
            )
            try:
                result = CombinedQuoteWriter(
                    guarded,
                    spreadsheet_id,
                    layout_guard=layout_guard,
                ).update(
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
                    guarded,
                    spreadsheet_id,
                    live=live,
                    tabs=("SAN_PHAM", "BIEN_THE", "BAO_GIA", "BAO_GIA_TONG_HOP"),
                )
                cells_written += extra_cells
                cells_written += _group_similar_menu_rows(
                    guarded,
                    spreadsheet_id,
                    live=live,
                    notes=notes,
                    errors=errors,
                )
            except Exception as exc:
                errors.append(f"Ghi MENU lỗi: {exc}")
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


def _guard_live_write(
    settings: Settings,
    notes: list[str],
    errors: list[str],
    *,
    live: bool,
) -> bool:
    """Chặn ghi khi file khóa nội dung hỏng. Backup bền kiểm tra sau khi có gateway."""

    del live
    from shop_integration.content_overrides import OverrideStoreError, load_override_store

    try:
        store = load_override_store()
    except OverrideStoreError as exc:
        errors.append(str(exc))
        return True
    notes.append(f"Content override: {len(store.items)} mục, file đã đọc lại.")
    if not settings.spreadsheet_id.strip():
        return False
    return False


def _require_durable_backup(
    settings: Settings,
    gateway: Any,
    notes: list[str],
    errors: list[str],
    *,
    live: bool,
) -> bool:
    from shop_integration.menu_region_backup import durable_backup_required, store_region_backup

    if not live or not durable_backup_required():
        return False
    try:
        notes.append(
            store_region_backup(
                gateway,
                settings.spreadsheet_id,
                secrets=settings.secret_values(),
            )
        )
    except Exception as exc:
        errors.append(f"Backup bền chưa READY. Không ghi MENU. {exc}")
        return True
    return False


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


def _open_gateway(settings: Settings):
    return open_sheets(settings)


def _menu_layout_guard(
    gateway: Any,
    spreadsheet_id: str,
    captured_at: str,
    notes: list[str],
) -> MenuLayoutGuard | None:
    """Đọc cấu trúc MENU một lần. Không đọc được thì vẫn áp whitelist tĩnh A:H."""

    try:
        baseline = read_menu_baseline(
            gateway,
            spreadsheet_id,
            MENU_HEADERS,
            captured_at=captured_at,
        )
    except MenuLayoutError as exc:
        notes.append(f"Không đọc được cấu trúc MENU: {exc} Vẫn áp whitelist A:H.")
        return None
    except Exception as exc:  # gateway lỗi tạm thời không được chặn toàn bộ sync
        notes.append(
            f"Không đọc được cấu trúc MENU ({type(exc).__name__}). Vẫn áp whitelist A:H."
        )
        return None
    notes.append(
        f"MENU layout: {baseline.merge_count} vùng gộp, "
        f"{len(baseline.formulas)} công thức, "
        f"{len(baseline.data_validations)} dropdown, "
        f"{len(baseline.protected_ranges)} vùng bảo vệ, "
        f"{baseline.data_rows} dòng dữ liệu."
    )
    baseline_path = os.environ.get("MENU_BASELINE_FILE", "").strip()
    if baseline_path:
        saved = load_menu_baseline(baseline_path)
        differences = menu_structure_diff(saved, baseline)
        shift_rows = _baseline_shift_rows(saved, baseline) if differences else None
        if shift_rows:
            notes.append(
                f"MENU đã tự mở rộng {shift_rows} dòng so với baseline "
                f"'{baseline_path}'. Chấp nhận vì đúng phép dịch dòng của tool."
            )
        elif differences:
            shown = "; ".join(differences[:5])
            more = f" (+{len(differences) - 5} khác biệt nữa)" if len(differences) > 5 else ""
            raise MenuLayoutError(
                f"MENU lệch baseline '{baseline_path}': {shown}{more}. Không ghi ô nào."
            )
        else:
            notes.append(f"MENU khớp baseline '{baseline_path}'.")
    return MenuLayoutGuard.from_baseline(baseline)


def _baseline_shift_rows(saved: Any, current: Any) -> int | None:
    """Số dòng tự mở rộng nếu ``current`` chỉ là ``saved`` dịch xuống. Không thì ``None``."""

    try:
        return menu_expansion_shift(saved, current)
    except Exception:
        return None


def _menu_zone_target(gateway: Any, spreadsheet_id: str, shop_count: int) -> int:
    """Số ô vùng sản phẩm cần có: sản phẩm API cộng số dòng NASTELEID nhập tay."""

    total = max(int(shop_count), 0)
    reader = getattr(gateway, "get_values", None)
    if not callable(reader):
        return total
    try:
        grid = reader(spreadsheet_id, f"'{TAB_MANUAL}'!A1:ZZ")
    except Exception:
        return total
    if not isinstance(grid, list) or not grid:
        return total
    try:
        rows, _note = manual_quote_rows([row for row in grid if isinstance(row, list)])
    except Exception:
        return total
    del _note
    return total + len(rows)


def _expand_menu_capacity(
    gateway: Any,
    spreadsheet_id: str,
    shop_count: int,
    *,
    live: bool,
    notes: list[str],
    errors: list[str],
) -> MenuExpansionPlan | None:
    """Chèn dòng MENU khi số sản phẩm vượt quá vùng chọn hiện tại. Không ghi giá."""

    if not auto_expand_enabled():
        return None
    target = _menu_zone_target(gateway, spreadsheet_id, shop_count)
    try:
        plan = auto_expand_menu(
            gateway,
            spreadsheet_id,
            target,
            headers=MENU_HEADERS,
            notes=notes,
            apply=live,
        )
    except Exception as exc:  # mở rộng lỗi thì vẫn ghi phần sản phẩm vừa đủ chỗ
        errors.append(f"Tự mở rộng MENU lỗi: {exc}")
        return None
    if plan is not None and live:
        logger.info(
            "MENU auto-expand rows=%s insert_at_row=%s products=%s zone_before=%s",
            plan.rows,
            plan.insert_at_row,
            plan.product_count,
            plan.zone_rows_before,
        )
    return plan


def _group_similar_menu_rows(
    gateway: Any,
    spreadsheet_id: str,
    *,
    live: bool,
    notes: list[str],
    errors: list[str],
) -> int:
    """Gom sản phẩm cùng loại về cạnh nhau. Chỉ đổi chỗ dòng A:H, không đổi dữ liệu."""

    if not may_group_similar():
        notes.append("Bỏ gom cụm sản phẩm cùng loại vì MENU_GROUP_SIMILAR=false.")
        return 0
    try:
        baseline = read_menu_baseline(gateway, spreadsheet_id, MENU_HEADERS)
    except MenuLayoutError as exc:
        notes.append(f"Chưa gom cụm được vì chưa đọc được cấu trúc MENU: {exc}")
        return 0
    except Exception as exc:  # gateway lỗi tạm thời thì bỏ qua, không chặn sync
        notes.append(
            f"Chưa gom cụm được vì lỗi đọc cấu trúc MENU ({type(exc).__name__})."
        )
        return 0
    capacity = MenuCapacity.from_baseline(baseline)
    try:
        grid = gateway.get_values(spreadsheet_id, f"'{TAB_MENU}'!A1:H")
    except Exception as exc:
        notes.append(
            f"Chưa gom cụm được vì không đọc được A:H của MENU ({type(exc).__name__})."
        )
        return 0
    if not grid:
        notes.append("Chưa gom cụm được vì MENU chưa có dữ liệu.")
        return 0
    plan = plan_menu_order(grid, capacity=capacity)
    notes.extend(plan.notes)
    if plan.identity:
        notes.append(plan.summary())
        return 0
    if not live:
        notes.append(f"Chạy thử chưa ghi: {plan.summary()}")
        return 0
    try:
        written = apply_menu_order(gateway, spreadsheet_id, plan, grid=grid)
    except MenuLayoutError as exc:
        errors.append(str(exc))
        return 0
    except Exception as exc:
        errors.append(f"Gom cụm sản phẩm cùng loại lỗi: {exc}")
        return 0
    notes.append(plan.summary())
    logger.info(
        "MENU group products rows=%s moved=%s clusters=%s cells=%s",
        plan.size,
        plan.moved,
        len(plan.clusters),
        written,
    )
    return written


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
        start = 1
        for row_number, raw in enumerate(grid[start:], start=start + 1):
            for column in columns:
                current = raw[column] if column < len(raw) else ""
                if not isinstance(current, str) or not current.strip():
                    continue
                cleaned = public_description(current)
                if cleaned != current:
                    payload.append((cell_range(tab, column, row_number), [[cleaned]]))
                remaining = cleaned if live else current
                if contains_contact_handle(remaining):
                    leftover += 1
    if live and payload:
        gateway.batch_update_values(spreadsheet_id, payload, value_input_option="RAW")
    return leftover, len(payload) if live else 0


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
