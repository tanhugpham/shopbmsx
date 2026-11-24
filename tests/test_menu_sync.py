from __future__ import annotations

from shop_integration.combined_quote import QUOTE_HEADERS, TAB_MENU
from shop_integration.menu_sync import (
    INTERVAL_SECONDS,
    run_menu_sync,
    scheduled_sync_seconds,
    start_menu_scheduler,
)
from shop_integration.menu_sync import _lock
from tests.conftest import make_settings
from tests.test_combined_quote import _menu_book, _menu_storefront


class BoomShop:
    def list_all_products(self) -> list:
        raise RuntimeError("shop down")


class EmptyShop:
    def list_all_products(self) -> list:
        return []


def test_interval_is_five_minutes() -> None:
    assert INTERVAL_SECONDS == 300
    assert scheduled_sync_seconds() == 300


def test_overlapping_sync_is_skipped() -> None:
    assert _lock.acquire(blocking=False)
    try:
        result = run_menu_sync(make_settings(last_spreadsheet_id="last-id"), live=False)
        assert result.status == "locked"
        assert result.cells_written == 0
    finally:
        _lock.release()


def test_shop_error_sanitizes_existing_menu_and_keeps_headers() -> None:
    grid = _menu_storefront()
    grid[10][2] = "Inbox admin @tiensky123 để nhận mã."
    gateway = _menu_book(grid)
    result = run_menu_sync(
        make_settings(last_spreadsheet_id="last-id", google_spreadsheet_id=""),
        live=True,
        gateway=gateway,
        shop_client=BoomShop(),
    )
    assert result.status in {"ok", "partial"}
    assert "shop" in " ".join(result.errors).lower() or "shop" in " ".join(result.notes).lower()
    menu = gateway.values[TAB_MENU]
    assert menu[6][0] == "🤖 SHOP XOÀI NON"
    assert menu[9][0] == "🤖 AI ACCOUNTS"
    assert menu[10][0] == "AI-01"
    assert menu[10][2] == "Inbox admin @admin để nhận mã."
    assert menu[10][6] == 1
    assert menu[10][7] == "IN STOCK"
    assert result.leftover_handles == 0


def test_retry_does_not_duplicate_menu_rows() -> None:
    gateway = _menu_book()
    settings = make_settings(last_spreadsheet_id="last-id", google_spreadsheet_id="")
    first = run_menu_sync(settings, live=True, gateway=gateway, shop_client=EmptyShop())
    before = [row[:] for row in gateway.values[TAB_MENU]]
    second = run_menu_sync(settings, live=True, gateway=gateway, shop_client=EmptyShop())
    assert first.status == "ok"
    assert second.status == "ok"
    assert gateway.values[TAB_MENU] == before
    ids = [row[0] if row else "" for row in gateway.values[TAB_MENU]]
    assert ids.count("SXN-01") == 1


class StockShop:
    def list_all_products(self) -> list:
        return [
            {
                "id": "local_cg",
                "title": "ChatGPT",
                "in_stock": True,
                "variants": [
                    {
                        "id": "v1",
                        "title": "Plus",
                        "prices": {"vnd": 168000},
                        "in_stock": True,
                        "description": "API mô tả",
                        "available_quantity": 9,
                    }
                ],
            }
        ]


def test_scheduled_menu_sync_updates_menu_from_sheets() -> None:
    gateway = _menu_book()
    result = run_menu_sync(
        make_settings(last_spreadsheet_id="last-id", google_spreadsheet_id=""),
        live=True,
        gateway=gateway,
        shop_client=StockShop(),
    )
    assert result.status == "ok"
    assert result.nastele_count == 0
    row = gateway.values[TAB_MENU][7]
    assert row[0] == "SXN-01"
    assert row[1] == "ChatGPT — Plus"
    assert row[2] == "API mô tả"
    assert row[5] == 203000
    assert row[6] == 9
    assert row[7] == "IN STOCK"
    assert gateway.values["NASTELEID"] == [list(QUOTE_HEADERS)]
    assert gateway.values["BAO_GIA"][1][1] == "ChatGPT — Plus"
    assert gateway.values["BAO_GIA"][1][5] == 203000
    assert gateway.values["BAO_GIA"][1][6] == 9
    claude = gateway.values[TAB_MENU][10]
    assert claude[5] == 1
    assert claude[1] == "Claude"
    assert claude[6] == 0
    assert claude[7] == "SOLD OUT"


def test_disabled_scheduler_does_not_start(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_SCHEDULED_SYNC", "false")
    from shop_integration import menu_sync

    menu_sync._scheduler_started = False
    start_menu_scheduler()
    assert menu_sync._scheduler_started is False


def test_scheduler_start_is_idempotent() -> None:
    from shop_integration import menu_sync

    menu_sync._scheduler_started = True
    start_menu_scheduler()
    assert menu_sync._scheduler_started is True
    menu_sync._scheduler_started = False
