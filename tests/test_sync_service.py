from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from shop_integration.docs_service import DocsService
from shop_integration.product_service import ProductService
from shop_integration.sheet_layout import PRICE_HISTORY_HEADERS
from shop_integration.sheets_service import SheetsService
from shop_integration.sync_service import SyncService
from shop_integration.time_utils import VIETNAM_TZ
from tests.conftest import client_for, load_fixture, make_settings
from tests.test_docs_service import MemoryDocs
from tests.test_sheets_service import OLD_PRODUCT_HEADERS, OLD_VARIANT_HEADERS, MemorySheets


def _clock() -> datetime:
    return datetime(2026, 11, 23, 10, 0, tzinfo=VIETNAM_TZ)


class AcceptingBackup:
    def __init__(self) -> None:
        self.created = 0
        self.journals: list[dict] = []

    def create_and_verify(self) -> dict:
        self.created += 1
        return {"status": "verified", "complete_fidelity": False}

    def write_journal(self, payload: dict) -> None:
        self.journals.append(payload)


def test_missing_price_tab_keeps_the_written_quote() -> None:
    from shop_integration.errors import BackupError, GoogleWorkspaceError

    class MissingPriceTab(MemorySheets):
        def get_values(self, spreadsheet_id: str, range_a1: str):
            if "BANG_GIA_BAN" in range_a1:
                raise GoogleWorkspaceError("Unable to parse range")
            return super().get_values(spreadsheet_id, range_a1)

    service = SyncService.__new__(SyncService)
    service._sheets = SheetsService(
        MissingPriceTab(titles=["SAN_PHAM"], values={"SAN_PHAM": [["x"]]}),
        "sheet-id",
        dry_run=False,
    )
    service._backup = AcceptingBackup()

    with pytest.raises(BackupError, match="Báo giá đã ghi"):
        service._write_journal("abc", doc_appended_text="BÁO GIÁ")

    assert service._backup.journals == []


def test_sync_paginates_writes_price_history_and_never_purchases() -> None:
    secret = "sxn_live_test_key_value"
    settings = make_settings(dry_run=False, enable_purchase=True, enable_telegram=True)
    calls: list[tuple[str, str]] = []
    page1 = load_fixture("products_page1.json")
    page1["data"][0]["title"] = secret

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method != "GET":
            return httpx.Response(500, text="purchase-or-quote")
        offset = request.url.params.get("offset", "0")
        if offset == "0":
            return httpx.Response(200, json=page1)
        if offset == "2":
            return httpx.Response(200, json=load_fixture("products_page2.json"))
        return httpx.Response(200, json=load_fixture("products_empty.json"))

    gateway = MemorySheets(
        titles=["SAN_PHAM", "BIEN_THE", "LICH_SU_GIA"],
        values={
            "SAN_PHAM": [
                OLD_PRODUCT_HEADERS + ["Ghi chú nội bộ"],
                ["local_81", "Tên cũ", "Còn hàng", 9000, 9000, 1, "cũ", "Thành công", "giữ lại"],
                ["local_old", "Cũ", "Hết hàng", 1000, 1000, 1, "cũ", "Thành công", ""],
            ],
            "BIEN_THE": [
                OLD_VARIANT_HEADERS,
                ["local_81", "local_81", 9000, "Còn hàng", 1, "cũ", "Thành công"],
            ],
            "LICH_SU_GIA": [PRICE_HISTORY_HEADERS],
        },
    )
    docs = MemoryDocs()
    sheets = SheetsService(gateway, "sheet-id", dry_run=False, secret=secret)
    with client_for(settings, handler) as api:
        report = SyncService(
            settings,
            api,
            ProductService(),
            sheets_service=sheets,
            docs_service=DocsService(docs, "doc-id", dry_run=False, secret=secret),
            clock=_clock,
            page_size=2,
            backup_service=AcceptingBackup(),
        ).run(write_sheets=True, write_doc=True)

    assert {method for method, _path in calls} == {"GET"}
    assert all(path.endswith("/products") for _method, path in calls)
    assert report.summary.product_count == 3
    assert report.summary.variant_count == 4
    assert len(report.price_changes) == 1
    assert report.price_changes[0].old_price == 9000
    assert report.price_changes[0].new_price == 9500
    assert report.sheets_written is True
    assert report.doc_written is True
    assert secret not in report.text
    assert any("ENABLE_PURCHASE" in warning for warning in report.warnings)
    assert any("ENABLE_TELEGRAM" in warning for warning in report.warnings)
    product_headers = gateway.values["SAN_PHAM"][0]
    assert gateway.values["SAN_PHAM"][1][product_headers.index("Tên sản phẩm đã decode")] == "***"
    assert gateway.values["SAN_PHAM"][1][product_headers.index("Tên gốc")] == "***"
    assert gateway.values["SAN_PHAM"][1][-1] == "giữ lại"
    assert gateway.values["SAN_PHAM"][2][product_headers.index("Product ID")] == "local_old"
    assert "Không còn trong API" in gateway.values["SAN_PHAM"][2]
    assert "Giá gốc" not in gateway.values["BANG_GIA_BAN"][0]
    assert docs.requests
    assert secret not in docs.requests[0]["insertText"]["text"]
    config_blob = str(gateway.values["CAU_HINH"])
    assert secret not in config_blob
    assert "GET /products" in config_blob
    assert "pricing_middle_profit" in config_blob
    assert "Không được ghi sang bảng" not in str(gateway.values)
    assert secret not in str(gateway.values)


def test_dry_run_reads_prices_without_writing() -> None:
    settings = make_settings(dry_run=True)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(200, json=load_fixture("products_page2.json"))

    gateway = MemorySheets(
        titles=["SAN_PHAM", "BIEN_THE", "LICH_SU_GIA", "NHAT_KY_DONG_BO", "CAU_HINH"],
        values={
            "BIEN_THE": [
                OLD_VARIANT_HEADERS,
                ["local_083", "var_083", 100, "Còn hàng", 1, "cũ", "Thành công"],
            ]
        },
    )
    sheets = SheetsService(gateway, "sheet-id", dry_run=True)
    docs = MemoryDocs()
    with client_for(settings, handler) as api:
        report = SyncService(
            settings,
            api,
            ProductService(),
            sheets_service=sheets,
            docs_service=DocsService(docs, "doc-id", dry_run=True),
            clock=_clock,
        ).run(write_sheets=True, write_doc=True)

    assert calls == ["GET"]
    assert report.dry_run is True
    assert report.sheets_written is False
    assert report.doc_written is False
    assert report.price_changes[0].old_price == 100
    assert report.price_changes[0].new_price == 500
    assert gateway.writes == []
    assert gateway.appends == []
    assert docs.requests == []
    assert "Dry-run" in report.text


def test_clock_timezone_is_vietnam() -> None:
    assert _clock().tzinfo == VIETNAM_TZ
    assert timezone.utc is not VIETNAM_TZ
