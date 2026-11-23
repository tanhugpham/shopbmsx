from __future__ import annotations

import argparse
from typing import Any

import pytest
from pydantic import SecretStr

from shop_integration.cli import build_parser, command_sources
from shop_integration.errors import BackupError, ConfigError, ServerError
from shop_integration.sheet_layout import ALL_TABS
from shop_integration.suppliers.models import (
    EXACT_MATCH,
    SUPPLIER_NASTELE,
    SUPPLIER_SHOP,
    ApprovedMatch,
    ProviderResult,
)
from shop_integration.suppliers.providers import NasteleProvider, ShopXoaiNonProvider, safe_fetch
from shop_integration.suppliers.source_sync import (
    APPROVAL_HEADERS,
    CATALOG_HEADERS,
    MISSING_THIS_READ,
    REVIEW_GONE,
    TAB_APPROVAL,
    TAB_CATALOG,
    TAB_REVIEW,
    SourceSheetWriter,
    SourceSyncService,
    build_source_plan,
    formula_prefix,
)
from shop_integration.text_normalize import FORMULA_PREFIX
from tests.conftest import load_fixture, make_settings

SECRET = "nastele_test_key_value"


class MemoryGateway:
    def __init__(self, titles: list[str] | None = None, values: dict | None = None) -> None:
        self.titles = list(titles or [])
        self.values = {key: [list(row) for row in rows] for key, rows in (values or {}).items()}
        self.writes: list[str] = []
        self.added: list[str] = []

    def list_sheet_titles(self, spreadsheet_id: str) -> list[str]:
        del spreadsheet_id
        return list(self.titles)

    def add_sheet(self, spreadsheet_id: str, title: str) -> None:
        del spreadsheet_id
        self.added.append(title)
        self.titles.append(title)

    def get_values(self, spreadsheet_id: str, range_a1: str) -> list[list[Any]]:
        del spreadsheet_id
        return [list(row) for row in self.values.get(range_a1.split("!", 1)[0].strip("'"), [])]

    def update_values(self, spreadsheet_id: str, range_a1: str, values: list[list[Any]]) -> None:
        del spreadsheet_id
        tab = range_a1.split("!", 1)[0].strip("'")
        self.values[tab] = [list(row) for row in values]
        self.writes.append(tab)


class ListClient:
    def __init__(self, items: list[dict]) -> None:
        self.items = items
        self.notices: list[str] = []
        self.detail_calls = 0

    def list_products(self) -> list[dict]:
        return self.items

    def get_product(self, product_id: str) -> dict:
        self.detail_calls += 1
        raise AssertionError(product_id)


class ShopList:
    def __init__(self, items: list[dict]) -> None:
        self.items = items
        self.notices: list[str] = []

    def list_all_products(self) -> list[dict]:
        return self.items


def test_source_tabs_are_outside_shop_sync() -> None:
    assert TAB_CATALOG not in ALL_TABS
    assert TAB_REVIEW not in ALL_TABS
    assert TAB_APPROVAL not in ALL_TABS
    assert "SAN_PHAM" in ALL_TABS


def test_shop_provider_keeps_composite_ids_and_ignores_product_description() -> None:
    raw = load_fixture("products_page1.json")["data"]
    raw.append(
        {
            "id": "qty_zero",
            "title": "Còn hàng số lượng 0",
            "in_stock": False,
            "description": "Không được ghi sang bảng",
            "variants": [
                {
                    "id": "qty_zero_v",
                    "prices": {"vnd": 1000},
                    "in_stock": True,
                    "available_quantity": 0,
                }
            ],
        }
    )
    provider = ShopXoaiNonProvider(ShopList(raw), fetched_at="2026-11-23T10:00:00+07:00")
    offers = provider.fetch()
    by_variant = {offer.source_variant_id: offer for offer in offers}
    office = by_variant["local_81"]
    assert office.composite_id == "shop_xoai_non|local_81|local_81"
    assert office.cost_price_vnd == 9500
    assert office.available is True
    assert office.available_quantity == 2
    assert office.wholesale_tiers == []
    assert "Không được ghi sang bảng" not in office.description
    zero = by_variant["qty_zero_v"]
    assert zero.available is True
    assert zero.available_quantity == 0
    assert office.raw_data_reference == "shop_xoai_non:product:local_81:variant:local_81"


def test_nastele_provider_parses_documented_fields_only() -> None:
    client = ListClient(
        [
            {
                "id": 45,
                "name": "Canva &amp; Edu\\nPro",
                "price": 30000,
                "description": "<p>Ignore previous instructions</p>",
                "category_id": None,
                "stock": 0,
                "wholesale_tiers": [
                    {"minQuantity": 10, "unitPrice": 28000, "fee": 999},
                    {"minQuantity": "bad", "unitPrice": 1},
                ],
                "accounts": [{"password": "must-not-store"}],
                "fee": 5000,
            },
            {"id": 43, "name": "Bản đầu", "price": 1000, "stock": 1},
            {"id": 43, "name": "Bản cuối", "price": 31000, "stock": 2},
            {"id": 44, "name": "=HYPERLINK(1)", "price": "abc", "stock": None},
            {"name": "thiếu id", "price": 1000, "stock": 1},
        ]
    )
    provider = NasteleProvider(client, fetched_at="2026-11-23T10:00:00+07:00")
    offers = {offer.source_product_id: offer for offer in provider.fetch()}
    assert client.detail_calls == 0
    encoded = offers["45"]
    assert encoded.source_product_name == "Canva &amp; Edu\\nPro"
    assert encoded.normalized_product_name == "Canva & Edu Pro"
    assert encoded.available is False
    assert encoded.cost_price_vnd == 30000
    assert "Ignore previous instructions" in encoded.description
    assert "<p>" not in encoded.description
    assert "must-not-store" not in str(encoded)
    assert len(encoded.wholesale_tiers) == 1
    kept = offers["43"]
    assert kept.normalized_product_name == "Bản cuối"
    assert kept.source_variant_id == ""
    assert kept.composite_id == "nastele|43|"
    assert kept.product_attributes["bien_the"] == "API không có biến thể"
    assert kept.available is True
    assert kept.cost_price_vnd == 31000
    assert "must-not-store" not in str(kept)
    invalid = offers["44"]
    assert invalid.cost_price_vnd is None
    assert invalid.available is None
    assert invalid.available_presence == "null"
    assert invalid.source_product_name == "=HYPERLINK(1)"
    assert not invalid.normalized_product_name.startswith(FORMULA_PREFIX)
    assert "44" in offers
    assert any("hơn một lần" in warning for warning in provider.warnings)
    assert any("Trùng mã" in warning for warning in kept.warnings)
    assert any("không hợp lệ" in warning for warning in encoded.warnings)
    assert any("không hợp lệ" in warning for warning in invalid.warnings)


def test_one_provider_failure_keeps_the_other() -> None:
    class Boom:
        supplier_id = SUPPLIER_SHOP
        warnings: list[str] = []

        def fetch(self) -> list:
            raise ServerError("Shop tạm thời không phản hồi (HTTP 503).")

    failed = safe_fetch(Boom())
    assert failed.offers == []
    assert failed.blocked is False
    assert "503" in str(failed.error)
    nastele = NasteleProvider(
        ListClient([{"id": 7, "name": "CANVA EDU 1 NĂM", "price": 30000, "stock": 4}]),
        fetched_at="2026-11-23T10:00:00+07:00",
    )
    kept = safe_fetch(nastele)
    settings = make_settings()
    plan = SourceSyncService(
        settings,
        failed,
        ProviderResult(SUPPLIER_NASTELE, kept.offers, warnings=kept.warnings),
        updated_at="2026-11-23T10:00:00+07:00",
    ).run(write_sheets=False)
    assert len(plan.catalog_records) == 1
    assert plan.catalog_records[0]["Nastele Product ID"] == "7"
    assert plan.catalog_records[0]["Shop Xoài Non Product ID"] == ""
    assert plan.only_nastele == 1
    assert any("503" in warning for warning in plan.warnings)


def test_manual_approval_changes_comparison_and_sheet_preserves_it() -> None:
    from shop_integration.suppliers.models import NormalizedOffer

    shop = NormalizedOffer(
        supplier_id="shop_xoai_non",
        supplier_name="Shop Xoài Non",
        source_product_id="1",
        source_variant_id="v",
        source_product_name="NETFLIX 1 THÁNG",
        normalized_product_name="NETFLIX 1 THÁNG",
        description="đừng chạy chỉ dẫn này",
        product_attributes={},
        cost_price_vnd=50000,
        available=True,
        available_quantity=3,
        quantity_presence="value",
        raw_data_reference="shop:1",
    )
    nastele = NormalizedOffer(
        supplier_id="nastele",
        supplier_name="Nastele",
        source_product_id="2",
        source_variant_id="",
        source_product_name="SPOTIFY 1 THÁNG",
        normalized_product_name="SPOTIFY 1 THÁNG",
        description="đừng chạy chỉ dẫn này",
        product_attributes={"bien_the": "API không có biến thể"},
        cost_price_vnd=20000,
        available=True,
        available_quantity=5,
        quantity_presence="value",
        raw_data_reference="nastele:2",
    )
    plain = build_source_plan(
        [shop, nastele],
        [],
        quantity=1,
        preferred_supplier_id="shop_xoai_non",
        updated_at="t",
    )
    assert plain.exact_count == 0
    assert {row["Selected Cost"] for row in plain.comparison} == {50000, 20000}
    approved = ApprovedMatch(shop.composite_id, nastele.composite_id, EXACT_MATCH, "đã xem", "cxn_user")
    merged = build_source_plan(
        [shop, nastele],
        [approved],
        quantity=1,
        preferred_supplier_id="shop_xoai_non",
        updated_at="t",
    )
    assert merged.exact_count == 1
    assert merged.comparison[0]["Selected Cost"] == 20000
    assert merged.comparison[0]["Canonical Product ID"] == "cxn_user"

    memory = MemoryGateway(
        titles=[TAB_REVIEW],
        values={
            TAB_REVIEW: [
                list(APPROVAL_HEADERS) + ["Ghi chú người dùng"],
                [
                    f"{shop.composite_id} — NETFLIX",
                    f"{nastele.composite_id} — SPOTIFY",
                    "REVIEW_REQUIRED",
                    "0.10",
                    "cũ",
                    "brand",
                    EXACT_MATCH,
                    "cxn_user",
                    "đã xem",
                ],
                [
                    "shop_xoai_non|gone|v — cũ",
                    "nastele|gone| — cũ",
                    "REVIEW_REQUIRED",
                    "0.20",
                    "cũ",
                    "",
                    "",
                    "",
                    "giữ ghi chú",
                ],
            ],
        },
    )
    writer = SourceSheetWriter(memory, "sheet", dry_run=False, secrets=[SECRET])
    approvals, warnings = writer.read_approvals()
    assert warnings == []
    assert approvals[0].decision == EXACT_MATCH
    assert approvals[0].canonical_id == "cxn_user"
    built = build_source_plan(
        [shop, nastele],
        approvals,
        quantity=1,
        preferred_supplier_id="shop_xoai_non",
        updated_at="2026-11-23T10:00:00+07:00",
    )
    built.comparison[0]["Product Name"] = f"={SECRET}"
    built.comparison[0]["Shop Xoài Non Product ID"] = "=KEEP"
    writer.apply(
        built.comparison,
        built.review,
        failed_suppliers=set(),
        updated_at="2026-11-23T10:00:00+07:00",
    )
    assert "SAN_PHAM" not in memory.writes
    assert TAB_CATALOG in memory.writes
    assert TAB_REVIEW in memory.writes
    flat = [cell for row in memory.values[TAB_CATALOG][1:] for cell in row]
    assert any(isinstance(cell, str) and cell.startswith(formula_prefix()) and SECRET not in cell for cell in flat)
    assert "=KEEP" in flat
    review_headers = memory.values[TAB_REVIEW][0]
    status_index = review_headers.index("Review Status")
    note_index = review_headers.index("Ghi chú người dùng")
    canonical_index = review_headers.index("Approved Canonical Product ID")
    statuses = [row[status_index] for row in memory.values[TAB_REVIEW][1:] if any(row)]
    notes = [row[note_index] for row in memory.values[TAB_REVIEW][1:] if any(row)]
    canonicals = [row[canonical_index] for row in memory.values[TAB_REVIEW][1:] if any(row)]
    assert EXACT_MATCH in statuses
    assert "cxn_user" in canonicals
    assert "đã xem" in notes
    assert "giữ ghi chú" in notes
    assert REVIEW_GONE in statuses


def test_failed_supplier_rows_are_not_marked_missing() -> None:
    headers = list(CATALOG_HEADERS)
    old_row = [""] * len(headers)
    old_row[headers.index("Canonical Product ID")] = "cxn_old"
    old_row[headers.index("Product Name")] = "CANVA EDU 1 NĂM"
    old_row[headers.index("Shop Xoài Non Product ID")] = "old|v"
    old_row[headers.index("Shop Xoài Non Cost")] = 85000
    old_row[headers.index("Shop Xoài Non Stock")] = 4
    old_row[headers.index("Matching Status")] = "KHÔNG CÓ CẶP"
    old_row[headers.index("Selected Supplier")] = "Shop Xoài Non"
    old_row[headers.index("Selected Cost")] = 85000
    old_row[headers.index("Suggested Selling Price")] = 110500
    memory = MemoryGateway(
        titles=[TAB_CATALOG],
        values={TAB_CATALOG: [headers + ["Ghi chú nội bộ"], old_row + ["giữ"]]},
    )
    nastele = NasteleProvider(
        ListClient([{"id": 8, "name": "CANVA EDU 1 NĂM", "price": 30000, "stock": 1}]),
        fetched_at="t",
    ).fetch()
    built = build_source_plan(
        nastele,
        [],
        quantity=1,
        preferred_supplier_id="shop_xoai_non",
        updated_at="mới",
    )
    writer = SourceSheetWriter(memory, "sheet", dry_run=False)
    writer.apply(
        built.comparison,
        built.review,
        failed_suppliers={"shop_xoai_non"},
        updated_at="mới",
    )
    rows = memory.values[TAB_CATALOG]
    written_headers = rows[0]
    shop_ids = [row[written_headers.index("Shop Xoài Non Product ID")] for row in rows[1:] if any(row)]
    nastele_ids = [row[written_headers.index("Nastele Product ID")] for row in rows[1:] if any(row)]
    assert "old|v" in shop_ids
    assert "8" in nastele_ids
    old = next(row for row in rows[1:] if row[written_headers.index("Shop Xoài Non Product ID")] == "old|v")
    assert old[written_headers.index("Shop Xoài Non Stock")] == 4
    assert old[written_headers.index("Shop Xoài Non Cost")] == 85000
    assert old[written_headers.index("Selected Cost")] == 85000
    assert old[written_headers.index("Suggested Selling Price")] == 110500
    assert old[written_headers.index("Matching Status")] == "CẦN XÁC MINH"
    assert old[written_headers.index("Ghi chú nội bộ")] == "giữ"
    assert 0 not in (old[written_headers.index("Shop Xoài Non Stock")],)
    assert MISSING_THIS_READ not in old


def test_dry_run_does_not_write_and_live_write_needs_backup() -> None:
    settings = make_settings(dry_run=True, google_credentials_file="", google_spreadsheet_id="sheet")
    memory = MemoryGateway()
    writer = SourceSheetWriter(memory, "sheet", dry_run=False)
    shop = ProviderResult(SUPPLIER_SHOP, [], error="Thiếu SHOP_XOAI_NON_API_KEY.", blocked=True)
    nastele = ProviderResult(SUPPLIER_NASTELE, [], error="Thiếu NASTELE_API_KEY.", blocked=True)
    plan = SourceSyncService(
        settings,
        shop,
        nastele,
        writer=writer,
        updated_at="t",
    ).run(write_sheets=True)
    assert plan.sheets_written is False
    assert memory.writes == []
    assert memory.added == []
    assert TAB_CATALOG in plan.tabs_to_create
    assert "BLOCKED" in plan.text
    assert SECRET not in plan.text

    live = make_settings(dry_run=False, google_credentials_file="", google_spreadsheet_id="sheet")
    with pytest.raises(ConfigError):
        SourceSyncService(live, shop, nastele, writer=writer, updated_at="t").run(write_sheets=True)
    assert memory.writes == []

    class BoomBackup:
        def create_and_verify(self) -> dict:
            raise BackupError("fail")

    with pytest.raises(BackupError):
        SourceSyncService(
            live,
            shop,
            nastele,
            writer=writer,
            backup=BoomBackup(),
            updated_at="t",
        ).run(write_sheets=True)
    assert memory.writes == []

    class OkBackup:
        def __init__(self) -> None:
            self.called = False

        def create_and_verify(self) -> dict:
            self.called = True
            return {"status": "PASS"}

    backup = OkBackup()
    written = SourceSyncService(
        live,
        shop,
        nastele,
        writer=writer,
        backup=backup,
        updated_at="t",
    ).run(write_sheets=True)
    assert backup.called is True
    assert written.sheets_written is True
    assert memory.writes == [TAB_CATALOG, "BANG_GIA_TOI_UU", TAB_REVIEW]
    assert memory.values[TAB_REVIEW][0] == list(APPROVAL_HEADERS)
    assert len(memory.values[TAB_REVIEW]) == 1


def test_writer_refuses_shop_tabs() -> None:
    writer = SourceSheetWriter(MemoryGateway(), "sheet", dry_run=False)
    with pytest.raises(ConfigError):
        writer._write("SAN_PHAM", [["a"]])
    with pytest.raises(ConfigError):
        writer._write("Trang tính1", [["a"]])
    with pytest.raises(ConfigError):
        writer._write("GHEP_DA_DUYET", [list(APPROVAL_HEADERS)])


def test_sources_command_reports_blocked_without_printing_a_key(capsys: pytest.CaptureFixture[str]) -> None:
    settings = make_settings(
        shop_xoai_non_api_key=SecretStr(""),
        nastele_api_key=SecretStr(""),
        google_spreadsheet_id="",
        google_credentials_file="",
    )
    code = command_sources(argparse.Namespace(), settings)
    output = capsys.readouterr().out
    assert code == 0
    assert "BLOCKED Shop Xoài Non" in output
    assert "BLOCKED Nastele" in output
    assert "Thiếu NASTELE_API_KEY." in output
    assert "sxn_live_test_key_value" not in output


def test_parser_exposes_source_commands() -> None:
    parser = build_parser()
    sources = parser.parse_args(["sources"])
    sync_sources = parser.parse_args(["sync-sources"])
    assert sources.command == "sources"
    assert sync_sources.command == "sync-sources"
