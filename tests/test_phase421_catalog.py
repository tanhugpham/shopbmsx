"""Phase 4.2.1: catalog Nastele rỗng không được xóa dữ liệu cũ hay tạo so giá giả."""

from __future__ import annotations

import argparse
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from shop_integration.backup_service import assess_live_sync_readiness
from shop_integration.cli import build_parser, command_backup_readiness
from shop_integration.errors import ConfigError, PurchaseDisabledError, ResponseValidationError
from shop_integration.suppliers.models import (
    NASTELE_CATALOG_EMPTY,
    SUPPLIER_NASTELE,
    SUPPLIER_SHOP,
    NormalizedOffer,
    ProviderResult,
)
from shop_integration.suppliers.nastele_client import NasteleClient, assert_nastele_read, interpret_product_list
from shop_integration.suppliers.nastele_sheet import NasteleSheetWriter, TAB_PRODUCTS
from shop_integration.suppliers.providers import NasteleProvider, safe_fetch
from shop_integration.suppliers.selection import classify_offer, is_selectable
from shop_integration.suppliers.source_sync import (
    COMPARE_HEADERS,
    MISSING_THIS_READ,
    TAB_COMPARE,
    SourceSheetWriter,
    SourceSyncService,
)
from tests.conftest import make_settings
from tests.test_nastele_client import _client


class MemoryGateway:
    def __init__(self, titles: list[str] | None = None, values: dict | None = None) -> None:
        self.titles = list(titles or [])
        self.values = {key: [list(row) for row in rows] for key, rows in (values or {}).items()}
        self.writes: list[str] = []
        self.added: list[str] = []
        self.methods: list[str] = []

    def list_sheet_titles(self, spreadsheet_id: str) -> list[str]:
        del spreadsheet_id
        return list(self.titles)

    def add_sheet(self, spreadsheet_id: str, title: str) -> None:
        del spreadsheet_id
        self.added.append(title)
        self.titles.append(title)

    def get_values(self, spreadsheet_id: str, range_a1: str) -> list[list[Any]]:
        del spreadsheet_id
        tab = range_a1.split("!", 1)[0].strip("'")
        return [list(row) for row in self.values.get(tab, [])]

    def update_values(self, spreadsheet_id: str, range_a1: str, values: list[list[Any]]) -> None:
        del spreadsheet_id
        tab = range_a1.split("!", 1)[0].strip("'")
        self.values[tab] = [list(row) for row in values]
        self.writes.append(tab)


def _shop_offer(
    product_id: str = "local_1",
    *,
    available: bool | None = True,
    cost: int | None = 10000,
    quantity: int | None = 4,
    presence: str = "value",
) -> NormalizedOffer:
    return NormalizedOffer(
        supplier_id=SUPPLIER_SHOP,
        supplier_name="Shop Xoài Non",
        source_product_id=product_id,
        source_variant_id=product_id,
        source_product_name=f"Sản phẩm {product_id}",
        normalized_product_name=f"Sản phẩm {product_id}",
        description="",
        product_attributes={},
        cost_price_vnd=cost,
        available=available,
        available_quantity=quantity,
        quantity_presence="value" if quantity is not None else "absent",
        available_presence=presence,
    )


def test_nastele_http_200_empty_data_is_empty_catalog_not_a_parse_of_products() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        assert request.url.path == "/api/partner/products"
        return httpx.Response(200, json={"success": True, "data": []})

    with _client(handler) as client:
        products = client.list_products()

    assert seen == ["GET"]
    assert products == []
    assert client.catalog_status == NASTELE_CATALOG_EMPTY
    assert any("không phải bằng chứng" in note for note in client.notices)


def test_nastele_empty_pagination_does_not_invent_products() -> None:
    payload = {
        "success": True,
        "data": {
            "items": [],
            "pagination": {"currentPage": 1, "perPage": 20, "total": 0, "lastPage": 1},
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(200, json=payload)

    with _client(handler) as client:
        products = client.list_products()

    assert products == []
    assert client.catalog_status == NASTELE_CATALOG_EMPTY
    assert any("phân trang" in note for note in client.notices)
    items, status, _notes = interpret_product_list(payload)
    assert items == []
    assert status == NASTELE_CATALOG_EMPTY


def test_nastele_schema_change_is_rejected_and_keeps_the_other_supplier() -> None:
    bodies = [
        {"success": True, "data": {"products": [{"id": 9, "name": "Giả", "price": 1000, "stock": 3}]}},
        {"success": True, "items": [{"id": 9, "name": "Giả", "price": 1000, "stock": 3}]},
        {
            "success": True,
            "data": {
                "items": [{"id": 9, "name": "Giả", "price": 1000, "stock": 3}],
                "pagination": {"total": 1},
            },
        },
    ]
    for body in bodies:
        def handler(request: httpx.Request, payload: dict = body) -> httpx.Response:
            assert request.method == "GET"
            return httpx.Response(200, json=payload)

        with _client(handler) as client:
            with pytest.raises(ResponseValidationError, match="thay đổi"):
                client.list_products()
            assert client.catalog_status == ""

    def changed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bodies[0])

    with _client(changed) as client:
        result = safe_fetch(NasteleProvider(client, fetched_at="t"))
    shop = ProviderResult(SUPPLIER_SHOP, [_shop_offer(), _shop_offer("local_2")])
    assert result.offers == []
    assert result.error and "thay đổi" in result.error
    assert len(shop.offers) == 2
    assert "Giả" not in str(result.offers)


def test_empty_catalog_does_not_delete_old_rows_or_reselect_supplier() -> None:
    headers = list(COMPARE_HEADERS)
    paired = [""] * len(headers)
    paired[headers.index("Canonical Product ID")] = "cxn_keep"
    paired[headers.index("Product Name")] = "Sản phẩm local_1"
    paired[headers.index("Shop Xoài Non Product ID")] = "local_1|local_1"
    paired[headers.index("Shop Xoài Non Cost")] = 10000
    paired[headers.index("Shop Xoài Non Stock")] = 4
    paired[headers.index("Nastele Product ID")] = "43"
    paired[headers.index("Nastele Cost")] = 8000
    paired[headers.index("Nastele Stock")] = 40
    paired[headers.index("Matching Status")] = "EXACT_MATCH"
    paired[headers.index("Selected Supplier")] = "Nastele"
    paired[headers.index("Selected Cost")] = 8000
    paired[headers.index("Cost Savings")] = 2000
    paired[headers.index("Suggested Selling Price")] = 10400
    lone = [""] * len(headers)
    lone[headers.index("Canonical Product ID")] = "cxn_only_nastele"
    lone[headers.index("Nastele Product ID")] = "99"
    lone[headers.index("Nastele Cost")] = 5000
    lone[headers.index("Nastele Stock")] = 12
    lone[headers.index("Selected Supplier")] = "Nastele"
    lone[headers.index("Selected Cost")] = 5000
    lone[headers.index("Cost Savings")] = 1000
    memory = MemoryGateway(titles=[TAB_COMPARE], values={TAB_COMPARE: [headers, paired, lone]})
    shop = ProviderResult(SUPPLIER_SHOP, [_shop_offer(), _shop_offer("local_24")])
    nastele = ProviderResult(SUPPLIER_NASTELE, [], catalog_status=NASTELE_CATALOG_EMPTY)
    settings = make_settings(dry_run=True, google_spreadsheet_id="sheet")
    nastele_memory = MemoryGateway(
        titles=[TAB_PRODUCTS],
        values={TAB_PRODUCTS: [["Product ID", "Stock Quantity"], ["43", 40]]},
    )
    plan = SourceSyncService(
        settings,
        shop,
        nastele,
        writer=SourceSheetWriter(memory, "sheet", dry_run=True),
        nastele_writer=NasteleSheetWriter(nastele_memory, "nastele-sheet", dry_run=True),
        updated_at="2026-11-23T12:00:00+07:00",
    ).run(write_sheets=True)

    assert memory.writes == []
    assert memory.added == []
    assert nastele_memory.writes == []
    assert nastele.offers == []
    assert len(shop.offers) == 2
    assert NASTELE_CATALOG_EMPTY in plan.text
    assert plan.cheaper_nastele == 0
    assert plan.nastele_status == NASTELE_CATALOG_EMPTY
    kept = next(row for row in plan.catalog_records if row.get("Canonical Product ID") == "cxn_keep")
    assert kept["Selected Supplier"] == "Nastele"
    assert kept["Selected Cost"] == 8000
    assert kept["Nastele Product ID"] == "43"
    assert kept["Nastele Stock"] == 40
    assert kept["Matching Status"] == NASTELE_CATALOG_EMPTY
    assert kept["Shop Xoài Non Product ID"] == "local_1|local_1"
    orphan = next(row for row in plan.catalog_records if row.get("Nastele Product ID") == "99")
    assert orphan["Matching Status"] == NASTELE_CATALOG_EMPTY
    assert orphan["Nastele Stock"] == 12
    assert MISSING_THIS_READ not in str(plan.catalog_records)
    fresh = next(row for row in plan.catalog_records if row.get("Shop Xoài Non Product ID") == "local_24|local_24")
    assert fresh.get("Nastele Product ID", "") in ("", None)
    assert fresh["Selected Supplier"] == "Shop Xoài Non"


def test_one_supplier_error_keeps_the_supplier_that_still_has_data() -> None:
    headers = list(COMPARE_HEADERS)
    old = [""] * len(headers)
    old[headers.index("Canonical Product ID")] = "cxn_shop"
    old[headers.index("Shop Xoài Non Product ID")] = "local_1|local_1"
    old[headers.index("Shop Xoài Non Cost")] = 10000
    old[headers.index("Shop Xoài Non Stock")] = 4
    old[headers.index("Nastele Product ID")] = "43"
    old[headers.index("Nastele Stock")] = 9
    old[headers.index("Selected Supplier")] = "Nastele"
    old[headers.index("Selected Cost")] = 7000
    memory = MemoryGateway(titles=[TAB_COMPARE], values={TAB_COMPARE: [headers, old]})
    shop = ProviderResult(SUPPLIER_SHOP, [_shop_offer()])
    nastele = ProviderResult(SUPPLIER_NASTELE, [], error="Hết thời gian chờ khi gọi Nastele.")
    settings = make_settings(dry_run=True, google_spreadsheet_id="sheet")
    plan = SourceSyncService(
        settings,
        shop,
        nastele,
        writer=SourceSheetWriter(memory, "sheet", dry_run=True),
        updated_at="t",
    ).run(write_sheets=True)
    assert memory.writes == []
    assert len(shop.offers) == 1
    assert nastele.offers == []
    kept = plan.catalog_records[0]
    assert kept["Shop Xoài Non Product ID"] == "local_1|local_1"
    assert kept["Shop Xoài Non Stock"] == 4
    assert kept["Nastele Product ID"] == "43"
    assert kept["Nastele Stock"] == 9
    assert kept["Selected Supplier"] == "Nastele"
    assert kept["Matching Status"] == "CẦN XÁC MINH"
    assert MISSING_THIS_READ not in str(plan.catalog_records)
    assert "LỖI Nastele" in plan.text
    assert "Shop Xoài Non: 1 nguồn" in plan.text


def test_in_stock_is_not_the_same_as_selectable() -> None:
    valid = _shop_offer(available=True, cost=9000, quantity=3)
    missing_price = _shop_offer("local_2", available=True, cost=None, quantity=3)
    sold_out = _shop_offer("local_3", available=False, cost=9000, quantity=0)
    unknown = _shop_offer("local_4", available=None, cost=9000, quantity=0, presence="absent")
    assert classify_offer(valid, 1) == "in_stock_valid"
    assert classify_offer(missing_price, 1) == "in_stock_invalid_price"
    assert classify_offer(sold_out, 1) == "out_of_stock"
    assert classify_offer(unknown, 1) == "unknown"
    assert is_selectable(valid, 1, SUPPLIER_SHOP) is True
    assert is_selectable(missing_price, 1, SUPPLIER_SHOP) is False
    assert is_selectable(sold_out, 1, SUPPLIER_SHOP) is False
    assert is_selectable(unknown, 1, SUPPLIER_SHOP) is False
    assert classify_offer(unknown, 1) != "out_of_stock"


def test_empty_catalog_path_has_no_write_and_no_purchase_command() -> None:
    with pytest.raises(PurchaseDisabledError):
        assert_nastele_read("POST", "/api/partner/orders")
    with pytest.raises(PurchaseDisabledError):
        assert_nastele_read("GET", "/api/partner/orders/2244")
    parser = build_parser()
    commands = {action.dest for action in parser._actions}
    subcommands = set()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            subcommands.update(action.choices)
    assert "sync-sources" in subcommands
    assert "quote" in subcommands
    assert "quote-layout" in subcommands
    assert not {"buy", "purchase", "deliver"} & subcommands
    assert "purchase" not in commands
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert request.method == "GET"
        assert "orders" not in request.url.path
        return httpx.Response(200, json={"success": True, "data": []})

    with _client(handler) as client:
        client.list_products()
        with pytest.raises(PurchaseDisabledError):
            client.place_order("43", 1)
    assert calls["n"] == 1


def test_github_actions_live_sync_stays_blocked_without_a_google_write(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    readiness = assess_live_sync_readiness()
    assert readiness["status"] == "FAIL"
    assert readiness["ready"] is False
    assert readiness["github_actions_live_sync"] == "NOT_READY"
    assert readiness["unmanaged_tabs_preserved"] is True
    memory = MemoryGateway()
    called = {"backup": False}

    class Backup:
        def create_and_verify(self) -> dict:
            called["backup"] = True
            return {"status": "verified"}

    settings = make_settings(dry_run=False, google_spreadsheet_id="sheet")
    with pytest.raises(ConfigError, match="chưa đủ điều kiện"):
        SourceSyncService(
            settings,
            ProviderResult(SUPPLIER_SHOP, [_shop_offer()]),
            ProviderResult(SUPPLIER_NASTELE, [], catalog_status=NASTELE_CATALOG_EMPTY),
            writer=SourceSheetWriter(memory, "sheet", dry_run=False),
            backup=Backup(),
            updated_at="t",
        ).run(write_sheets=True)
    assert called["backup"] is False
    assert memory.writes == []
    assert memory.added == []


def test_backup_readiness_command_prints_not_ready(capsys: pytest.CaptureFixture[str]) -> None:
    code = command_backup_readiness(argparse.Namespace(), make_settings())
    output = capsys.readouterr().out
    assert code == 0
    assert "Backup readiness: FAIL" in output
    assert "NOT_READY" in output
    assert "Trang tính1" in output
    assert "Google Drive" in output
