from __future__ import annotations

import re
from typing import Any

import httpx

from shop_integration.cli import build_parser
from shop_integration.combined_quote import (
    MENU_HEADERS,
    QUOTE_HEADERS,
    TAB_AUTO,
    TAB_COMBINED,
    TAB_MANUAL,
    TAB_MENU,
    CombinedQuoteWriter,
    is_category_header_row,
    list_menu_products,
    manual_quote_rows,
    menu_quote_grid,
    nastele_quote_rows,
    parse_menu_regions,
    plan_tab,
    quote_duration,
    quote_layout_tables,
    quote_warranty,
    render_quote_layout,
    shop_quote_rows,
    shop_sheet_rows,
)
from shop_integration.docs_service import DocsService
from shop_integration.models import Product, Variant
from shop_integration.pricing import PricingPolicy, quote_cost, round_pretty_vnd
from shop_integration.product_service import ProductService
from shop_integration.sheets_service import SheetsService
from shop_integration.sync_service import SyncService
from shop_integration.text_normalize import FORMULA_PREFIX
from tests.conftest import client_for, load_fixture, make_settings
from tests.test_docs_service import MemoryDocs
from tests.test_sheets_service import MemorySheets
from tests.test_sync_service import AcceptingBackup, _clock

_CELL = re.compile(r"^'([^']+)'!([A-Z]+)(\d+)$")


class QuoteMemory:
    def __init__(self, titles: list[str], values: dict[str, list[list[Any]]]) -> None:
        self.titles = list(titles)
        self.values = {key: [list(row) for row in rows] for key, rows in values.items()}
        self.added: list[str] = []
        self.batches: list[tuple[str, str, list]] = []
        self.format_requests: list[list] = []
        self.merges: dict[str, list[dict[str, int]]] = {}

    def list_sheet_titles(self, spreadsheet_id: str) -> list[str]:
        del spreadsheet_id
        return list(self.titles)

    def add_sheet(self, spreadsheet_id: str, title: str) -> None:
        del spreadsheet_id
        self.added.append(title)
        self.titles.append(title)
        self.values.setdefault(title, [])

    def list_sheet_meta(self, spreadsheet_id: str) -> list[dict[str, Any]]:
        del spreadsheet_id
        return [
            {
                "title": title,
                "sheetId": index + 1,
                "bandingIds": [],
                "conditionalFormatCount": 0,
                "merges": list(self.merges.get(title, [])),
            }
            for index, title in enumerate(self.titles)
        ]

    def get_values(self, spreadsheet_id: str, range_a1: str) -> list[list[Any]]:
        del spreadsheet_id
        tab = range_a1.split("!", 1)[0].strip("'")
        return [list(row) for row in self.values.get(tab, [])]

    def batch_update_values(
        self,
        spreadsheet_id: str,
        items: list[tuple[str, list[list[Any]]]],
        *,
        value_input_option: str = "RAW",
    ) -> None:
        self.batches.append((spreadsheet_id, value_input_option, items))
        for range_a1, matrix in items:
            _paint(self.values, range_a1, matrix)

    def batch_update(self, spreadsheet_id: str, requests: list) -> None:
        del spreadsheet_id
        self.format_requests.append(requests)


def _paint(values: dict[str, list[list[Any]]], range_a1: str, matrix: list[list[Any]]) -> None:
    match = _CELL.match(range_a1)
    assert match is not None
    tab, letters, row_text = match.group(1), match.group(2), match.group(3)
    grid = values.setdefault(tab, [])
    start_row = int(row_text) - 1
    start_col = _column_index(letters)
    for row_offset, line in enumerate(matrix):
        target = start_row + row_offset
        while len(grid) <= target:
            grid.append([])
        for col_offset, cell in enumerate(line):
            dest = start_col + col_offset
            while len(grid[target]) <= dest:
                grid[target].append("")
            grid[target][dest] = cell


def _column_index(letters: str) -> int:
    number = 0
    for char in letters:
        number = number * 26 + (ord(char) - 64)
    return number - 1


def _products() -> list[Product]:
    return [
        Product(
            id="local_cg",
            title="ChatGPT",
            variants=[
                Variant(
                    id="v1",
                    price_vnd=168000,
                    in_stock=True,
                    in_stock_presence="value",
                    title="Plus",
                    description="API mô tả",
                )
            ],
        ),
        Product(
            id="local_grok",
            title="Grok",
            variants=[
                Variant(
                    id="v1",
                    price_vnd=79000,
                    in_stock=True,
                    in_stock_presence="value",
                    description="Gói API",
                )
            ],
        ),
    ]


def _styled_book() -> QuoteMemory:
    return QuoteMemory(
        [TAB_MANUAL, TAB_AUTO, TAB_COMBINED],
        {
            TAB_MANUAL: [
                ["Nastele ID", "Tên sản phẩm", "Giá bán", "Tình trạng", "Cấu hình", "Ghi chú tay"],
                ["N-1", "Canva tay", 102700, "Còn", "Gói năm", "GIU_TAY"],
                ["N-2", "=CMD", 1500, "Còn", "Gói", ""],
            ],
            TAB_AUTO: [
                ["Ghi chú", "Nguồn", "Mã", "Tên sản phẩm", "Giá bán", "Tình trạng", "Cấu hình"],
                ["giữ shop", "Shop Xoài Non", "local_cg|v1", "ChatGPT cũ", 100, "Cũ", "cũ"],
                ["giữ mất", "Shop Xoài Non", "local_old|v", "Sản phẩm cũ", 5000, "Hết", "bỏ"],
            ],
            TAB_COMBINED: [
                ["Màu", "Nguồn", "Mã", "Tên sản phẩm", "Giá bán", "Tình trạng", "Cấu hình", "Ghi chú"],
                ["xanh", "NASTELEID", "N-1", "Canva cũ", 1000, "Hết", "cũ", "GIU_TONG"],
                ["đỏ", "Shop Xoài Non", "local_cg|v1", "ChatGPT cũ", 100, "Cũ", "cũ", "GIU_SHOP"],
                ["", "", "", "", "", "", "", "DONG_TRONG"],
                ["vàng", "Shop Xoài Non", "local_old|v", "Cũ", 5000, "Hết", "x", "GIU_CU"],
            ],
        },
    )


def test_shop_and_manual_prices_stay_independent() -> None:
    shop = shop_quote_rows(_products(), PricingPolicy())
    assert [row["ID"] for row in shop] == [1, 2]
    assert all("local_" not in str(row["ID"]) for row in shop)
    assert all("Mã" not in row for row in shop)
    by_name = {row["PRODUCT"]: row for row in shop}
    assert by_name["ChatGPT — Plus"]["PRICE"] == 203000
    assert by_name["ChatGPT — Plus"]["PRICE"] == round_pretty_vnd(quote_cost(168000).selling or 0)
    assert by_name["Grok"]["PRICE"] == 103000
    assert by_name["ChatGPT — Plus"]["DESCRIPTION"] == "API mô tả"
    assert by_name["Grok"]["DESCRIPTION"] == "Gói API"
    assert by_name["Grok"]["PRODUCT"] == "Grok"


def test_locked_headers_and_format_on_both_quote_sheets() -> None:
    gateway = _styled_book()
    manual_before = [list(row) for row in gateway.values[TAB_MANUAL]]
    writer = CombinedQuoteWriter(gateway, "last-id")
    result = writer.update(shop_quote_rows(_products()), dry_run=False)

    assert result.applied is True
    assert result.manual_count == 2
    assert result.shop_count == 2
    assert gateway.added == []
    assert gateway.format_requests
    assert any("frozenRowCount" in str(item) for batch in gateway.format_requests for item in batch)
    assert gateway.values[TAB_MANUAL] == manual_before
    assert {option for _id, option, _items in gateway.batches} == {"RAW"}

    auto = gateway.values[TAB_AUTO]
    assert auto[0] == list(QUOTE_HEADERS)
    assert auto[1][0] == 1
    assert auto[1][1] == "ChatGPT — Plus"
    assert auto[1][2] == "API mô tả"
    assert auto[1][5] == 203000
    assert auto[2][0] == 2
    assert auto[2][1] == "Grok"
    assert auto[2][2] == "Gói API"
    assert auto[2][5] == 103000
    assert all("local_" not in str(row[0]) for row in auto[1:])

    combined = gateway.values[TAB_COMBINED]
    assert combined[0][: len(QUOTE_HEADERS)] == list(QUOTE_HEADERS)
    manual_rows = [row for row in combined[1:] if len(row) > 1 and "Canva tay" in str(row[1])]
    shop_rows = [row for row in combined[1:] if len(row) > 1 and str(row[1]).startswith("ChatGPT")]
    grok_rows = [row for row in combined[1:] if len(row) > 1 and str(row[1]) == "Grok"]
    assert manual_rows[0][1].startswith("Canva tay")
    assert manual_rows[0][5] == 102700
    formula_rows = [row for row in combined[1:] if len(row) > 1 and str(row[1]).startswith(FORMULA_PREFIX)]
    assert formula_rows
    assert shop_rows[0][2] == "API mô tả"
    assert shop_rows[0][5] == 203000
    assert grok_rows[0][2] == "Gói API"


def test_unlocked_headers_keep_other_source() -> None:
    grid = [
        ["Tiêu đề của tôi", "Nguồn", "Giá bán"],
        ["xin chào", "Shop Xoài Non", 1],
    ]
    plan = plan_tab(
        TAB_COMBINED,
        grid,
        [{"Nguồn": "NASTELEID", "STT": 1, "Giá bán": 102700, "Tên sản phẩm": "Canva"}],
        exists=True,
        sources_to_refresh={"NASTELEID"},
    )
    assert plan.cells == []
    assert plan.full_grid is None
    assert "Không ghi" in plan.warning


def test_new_tabs_use_locked_headers_and_create_missing_manual() -> None:
    gateway = QuoteMemory([], {})
    result = CombinedQuoteWriter(gateway, "last-id").update(
        shop_quote_rows(_products()[:1]),
        dry_run=False,
    )
    assert gateway.added == [TAB_MANUAL, TAB_AUTO, TAB_COMBINED]
    assert gateway.format_requests
    assert gateway.values[TAB_MANUAL][0] == list(QUOTE_HEADERS)
    assert gateway.values[TAB_MANUAL][1:] == []
    assert gateway.values[TAB_AUTO][0] == list(QUOTE_HEADERS)
    assert gateway.values[TAB_AUTO][1][0] == 1
    assert gateway.values[TAB_AUTO][1][1] == "ChatGPT — Plus"
    assert gateway.values[TAB_AUTO][1][5] == 203000
    assert "local_" not in str(gateway.values[TAB_AUTO][1])
    assert result.applied is True


def test_dry_run_plans_without_writing() -> None:
    gateway = QuoteMemory([], {})
    result = CombinedQuoteWriter(gateway, "last-id").update(
        shop_quote_rows(_products()[:1]),
        dry_run=True,
    )
    assert result.applied is False
    assert result.ranges
    assert gateway.added == []
    assert gateway.batches == []
    assert gateway.format_requests == []


def test_empty_shop_catalog_does_not_clear_shop_rows() -> None:
    gateway = QuoteMemory(
        [TAB_MANUAL, TAB_COMBINED],
        {
            TAB_MANUAL: [
                ["Nastele ID", "Tên sản phẩm", "Giá bán", "Tình trạng", "Cấu hình"],
                ["N-1", "Canva tay", 102700, "Còn", "Gói năm"],
            ],
            TAB_COMBINED: [
                ["Nguồn", "Mã", "Tên sản phẩm", "Giá bán", "Tình trạng", "Cấu hình"],
                ["NASTELEID", "N-1", "Tên cũ", 1000, "Hết", "cũ"],
                ["Shop Xoài Non", "local_cg|v1", "ChatGPT", 203000, "Còn hàng", "API mô tả"],
            ],
        },
    )
    CombinedQuoteWriter(gateway, "last-id").update([], dry_run=False)
    combined = gateway.values[TAB_COMBINED]
    assert combined[0] == list(QUOTE_HEADERS)
    assert "Canva tay" in str(combined[1][1])
    assert combined[1][5] == 102700
    assert "ChatGPT" in str(combined[2][1])
    assert combined[2][5] == 203000
    assert TAB_AUTO not in gateway.values


def test_missing_last_id_and_dry_run_do_not_write() -> None:
    class Exploding(QuoteMemory):
        def get_values(self, spreadsheet_id: str, range_a1: str) -> list[list[Any]]:
            raise AssertionError(range_a1)

    quote = Exploding([], {})
    settings = make_settings(dry_run=True, last_spreadsheet_id="  ")
    shop = MemorySheets(titles=["SAN_PHAM"], values={"SAN_PHAM": [["Product ID"]]})

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json=load_fixture("products_empty.json"))

    with client_for(settings, handler) as api:
        report = SyncService(
            settings,
            api,
            ProductService(),
            sheets_service=SheetsService(shop, "sheet-id", dry_run=True),
            quote_gateway=quote,
            clock=_clock,
        ).run(write_sheets=True, write_doc=False)

    assert quote.batches == []
    assert shop.writes == []
    assert "tổng hợp" not in " ".join(report.warnings)


def test_report_updates_quote_values_without_shop_sheet_writes() -> None:
    quote = QuoteMemory(
        [TAB_MANUAL],
        {
            TAB_MANUAL: [
                ["Nastele ID", "Tên sản phẩm", "Giá bán", "Tình trạng", "Cấu hình"],
                ["N-1", "Canva tay", 102700, "Còn", "Gói năm"],
            ]
        },
    )
    shop = MemorySheets(
        titles=["SAN_PHAM", "BIEN_THE"],
        values={"SAN_PHAM": [["Product ID"], ["local_old"]]},
    )
    settings = make_settings(dry_run=False, last_spreadsheet_id=" last-id ")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(200, json=load_fixture("products_page2.json"))

    with client_for(settings, handler) as api:
        report = SyncService(
            settings,
            api,
            ProductService(),
            sheets_service=SheetsService(shop, "sheet-id", dry_run=False),
            docs_service=DocsService(MemoryDocs(), "doc-id", dry_run=False),
            backup_service=AcceptingBackup(),
            quote_gateway=quote,
            clock=_clock,
        ).run(write_sheets=False, write_doc=True)

    assert calls == ["GET"]
    assert shop.writes == []
    assert shop.added == []
    assert quote.added == [TAB_AUTO, TAB_COMBINED]
    assert TAB_MANUAL not in quote.added
    assert quote.format_requests
    assert {sheet_id for sheet_id, _option, _items in quote.batches} == {"last-id"}
    assert quote.values[TAB_AUTO][0] == list(QUOTE_HEADERS)
    assert quote.values[TAB_AUTO][1][5] == 700
    prices = [row[5] for row in quote.values[TAB_COMBINED][1:]]
    assert prices == [102700, 700]
    assert "Không ghi dữ liệu NASTELEID" in " ".join(report.warnings)


def test_writer_uses_unified_nasteleid_and_keeps_cost_warning() -> None:
    gateway = QuoteMemory(
        [TAB_MANUAL],
        {
            TAB_MANUAL: [
                ["Nguồn", "Mã", "Tên sản phẩm", "Giá bán", "Giá gốc", "Tình trạng", "Cấu hình"],
                ["Shop Xoài Non", "local_old|v", "Shop dán nhầm", 203000, 168000, "Còn hàng", "x"],
                ["NASTELEID", "43", "Canva Pro 1 năm", 102700, 79000, "Còn hàng", "Gói năm"],
            ]
        },
    )
    result = CombinedQuoteWriter(gateway, "last-id").update(
        shop_quote_rows(_products()[:1]),
        dry_run=False,
    )
    assert result.applied is True
    assert result.manual_count == 1
    assert "Giá gốc" in " ".join(result.warnings)
    combined = gateway.values[TAB_COMBINED]
    assert combined[0] == list(QUOTE_HEADERS)
    names = [str(row[1]) for row in combined[1:] if len(row) > 1]
    assert any("Canva Pro 1 năm" in name for name in names)
    assert all("Shop dán nhầm" not in name for name in names)
    assert any(name.startswith("ChatGPT") for name in names)
    assert 102700 in [row[5] for row in combined[1:] if len(row) > 5]
    assert all("local_" not in str(row[0]) for row in combined[1:])


def test_unified_nasteleid_headers_are_read() -> None:
    rows, warning = manual_quote_rows(
        [
            list(QUOTE_HEADERS),
            [1, "Canva Pro", "Học sinh", "1 Năm", "Bảo hành 1:1", 102700, 12],
            [2, "CapCut", "", "", "", 88000, 0],
        ]
    )
    assert warning == ""
    assert [row["ID"] for row in rows] == [1, 2]
    assert rows[0]["Nguồn"] == "NASTELEID"
    assert rows[0]["PRODUCT"] == "Canva Pro"
    assert rows[0]["DESCRIPTION"] == "Học sinh"
    assert rows[0]["DURATION"] == "1 Năm"
    assert rows[0]["WARRANTY"] == "Bảo hành 1:1"
    assert rows[0]["PRICE"] == 102700
    assert rows[0]["STOCK"] == 12
    assert rows[1]["PRODUCT"] == "CapCut"
    assert rows[1]["STOCK"] == 0


def test_nasteleid_skips_shop_rows_and_cost_columns() -> None:
    rows, warning = manual_quote_rows(
        [
            ["Nguồn", "Mã", "Tên sản phẩm", "Giá bán", "Giá gốc", "Tình trạng", "Cấu hình"],
            ["Shop Xoài Non", "local_cg|v1", "ChatGPT", 203000, 168000, "Còn hàng", "API"],
            ["NASTELEID", "43", "Canva", 102700, 79000, "Còn hàng", "Gói năm"],
        ]
    )
    assert [row["PRODUCT"] for row in rows] == ["Canva — Gói năm"]
    assert rows[0]["PRICE"] == 102700
    assert "Giá gốc" in warning
    assert "Bỏ 1 dòng" in warning


def test_custom_hand_source_is_kept() -> None:
    rows, warning = manual_quote_rows(
        [
            list(QUOTE_HEADERS),
            [1, "SHopabc", "test", "TESST", "HELLO", 100000, 23],
        ]
    )
    assert warning == ""
    assert len(rows) == 1
    assert rows[0]["Nguồn"] == "NASTELEID"
    assert rows[0]["PRODUCT"] == "SHopabc"
    assert rows[0]["DESCRIPTION"] == "test"
    assert rows[0]["DURATION"] == "TESST"
    assert rows[0]["WARRANTY"] == "HELLO"
    assert rows[0]["PRICE"] == 100000
    assert rows[0]["STOCK"] == 23


def test_legacy_nastele_id_header_still_reads() -> None:
    rows, warning = manual_quote_rows(
        [
            ["Nastele ID", "Tên sản phẩm", "Giá bán", "Tình trạng", "Cấu hình"],
            ["N-1", "Canva tay", 102700, "Còn", "Gói năm"],
        ]
    )
    assert warning == ""
    assert rows[0]["ID"] == "N-1"
    assert rows[0]["Nguồn"] == "NASTELEID"
    assert rows[0]["PRODUCT"] == "Canva tay — Gói năm"
    assert rows[0]["PRICE"] == 102700
    assert rows[0]["STOCK"] == "Còn hàng"


def test_missing_name_header_skips_manual_tab() -> None:
    rows, warning = manual_quote_rows([["Giá bán"], [1000]])
    assert rows == []
    assert "PRODUCT" in warning


def test_quote_layout_tables_cover_both_files() -> None:
    tables = {title: (headers, rows) for title, headers, rows in quote_layout_tables()}
    files = tables["Hai file Google"][1]
    assert files[0][0] == "Catalog Shop"
    assert files[0][1] == "GOOGLE_SPREADSHEET_ID"
    assert files[1][0] == "Báo giá chính thức"
    assert files[1][1] == "LAST_SPREADSHEET_ID"
    assert "MENU" in files[1][2]
    columns = tables["Cột khóa — dòng 1 của NASTELEID và BAO_GIA"][1]
    assert [row[0] for row in columns] == list(QUOTE_HEADERS)
    assert "local id" in columns[0][1]
    text = render_quote_layout()
    assert text.count("|") > 20
    assert "Giá gốc" in text
    assert "Không copy từ file catalog" in text


def test_quote_layout_command_prints_tables() -> None:
    parser = build_parser()
    args = parser.parse_args(["quote-layout"])
    assert args.command == "quote-layout"


def test_duration_and_warranty_from_shop_names() -> None:
    assert quote_duration("ChatGPT Plus 1 tháng riêng tư - BHF") == "1 Tháng"
    assert quote_warranty("ChatGPT Plus 1 tháng riêng tư - BHF") == "BHF"
    assert quote_duration("Capcut Pro 30d full Date - BHF") == "30 Ngày"
    assert quote_duration("Grok Premium+ Cursor 14 - 24 ngày (BH 1D)") == "14-24 Ngày"
    assert quote_warranty("Grok Premium+ Cursor 14 - 24 ngày (BH 1D)") == "BH1D"
    assert quote_duration("Memrise Pro gói trọn đời chính chủ") == "Vĩnh viễn"
    assert quote_warranty("Tài khoản MUSE AI 2 TỶ TOKEN KBH") == "KBH"
    assert quote_warranty("Canva Pro") == "Bảo hành 1:1"


def test_nastele_quote_rows_use_selling_price() -> None:
    class Offer:
        source_product_name = "SLOT CANVA EDU 1 NĂM"
        description = "Gói giáo dục"
        product_attributes = {"bien_the": "API không có biến thể"}
        cost_price_vnd = 30000
        available = True
        available_quantity = 40
        quantity_presence = "value"

    rows = nastele_quote_rows([Offer()])
    assert rows[0]["ID"] == 1
    assert rows[0]["Nguồn"] == "NASTELEID"
    assert rows[0]["PRODUCT"] == "SLOT CANVA EDU 1 NĂM"
    assert rows[0]["PRICE"] == 39000
    assert rows[0]["DURATION"] == "1 Năm"
    assert rows[0]["STOCK"] == 40
    assert "local_" not in str(rows[0]["ID"])


def _menu_storefront() -> list[list[Any]]:
    return [
        ["DIGITAL STORE"],
        ["banner"],
        [],
        [],
        [],
        list(MENU_HEADERS) + ["", "WARRANTY POLICY"],
        ["🤖 SHOP XOÀI NON", "", "", "", "", "", "", "", "", "KEEP_SHOP"],
        ["SXN-01", "ChatGPT — Plus", "API mô tả", "1 Tháng", "BHF", 100, 2, "IN STOCK", "", "KEEP_TWO"],
        ["", "", "", "", "", "", "", ""],
        ["🤖 AI ACCOUNTS", "", "", "", "", "", "", "", "", "KEEP_AI"],
        ["AI-01", "Claude", "old", "1 Tháng", "BHF", 1, 1, "IN STOCK", "", "AI_ITEM"],
        ["", "", "", "", "", "", "", ""],
        ["WINDOWS LICENSE", "", "", "", "", "", "", "", "", "KEEP_WIN"],
        ["", "", "", "", "", "", "", ""],
        ["💎 NASTELE", "", "", "", "", "", "", "", "", "KEEP_NST"],
        ["", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", "", "", "⚠️ LƯU Ý PHÂN BIỆT"],
    ]


def _menu_book(grid: list[list[Any]] | None = None) -> QuoteMemory:
    return QuoteMemory(
        [TAB_MENU, TAB_MANUAL, TAB_AUTO, TAB_COMBINED],
        {
            TAB_MENU: [list(row) for row in (grid or _menu_storefront())],
            TAB_MANUAL: [list(QUOTE_HEADERS)],
        },
    )


def _menu_rows_written(ranges: list[str]) -> set[int]:
    found: set[int] = set()
    for item in ranges:
        match = _CELL.match(item)
        if match and match.group(1) == TAB_MENU:
            found.add(int(match.group(3)))
    return found


def _nastele_canva() -> list[dict[str, Any]]:
    return [
        {
            "STT": 1,
            "Nguồn": "NASTELEID",
            "Tên sản phẩm": "SLOT CANVA EDU 1 NĂM",
            "Tên biến thể": "",
            "Mô tả": "Gói giáo dục",
            "Giá bán": 39000,
            "Tình trạng": "Còn hàng",
            "Số lượng": 40,
        }
    ]


def _windows_product() -> Product:
    return Product(
        id="local_win",
        title="Windows 11 Pro",
        variants=[
            Variant(
                id="v1",
                price_vnd=168000,
                in_stock=True,
                in_stock_presence="value",
                title="Retail",
                description="Key điện tử",
                available_quantity=3,
                quantity_presence="value",
            )
        ],
    )


def _sold_out_product() -> Product:
    return Product(
        id="local_off",
        title="Microsoft Office 365",
        variants=[
            Variant(
                id="v1",
                price_vnd=79000,
                in_stock=False,
                in_stock_presence="value",
                description="Inbox admin @tiensky123 để nhận mã yêu cầu.",
                available_quantity=0,
                quantity_presence="value",
            )
        ],
    )


def test_menu_is_built_from_bao_gia_and_nasteleid_sheets() -> None:
    gateway = _menu_book()
    gateway.values[TAB_AUTO] = [
        list(QUOTE_HEADERS),
        [1, "ChatGPT — Plus", "Giá đã chốt trên sheet", "1 Tháng", "BHF", 150000, "Còn hàng"],
    ]
    gateway.values[TAB_MANUAL] = [
        list(QUOTE_HEADERS),
        [1, "Canva Pro — Gói năm", "Học sinh", "1 Năm", "Bảo hành 1:1", 102700, "Còn hàng"],
    ]
    result = CombinedQuoteWriter(gateway, "last-id").update([], dry_run=False)
    menu = gateway.values[TAB_MENU]
    assert menu[7][0] == "SXN-01"
    assert menu[7][1] == "ChatGPT — Plus"
    assert menu[7][2] == "Giá đã chốt trên sheet"
    assert menu[7][5] == 150000
    assert menu[7][6] == 2
    assert menu[6][0] == "🤖 SHOP XOÀI NON"
    nastele = next(row for row in menu if row and row[0] == "NST-01")
    assert nastele[1] == "Canva Pro — Gói năm"
    assert nastele[5] == 102700
    assert "BAO_GIA" in " ".join(result.warnings)
    assert "NASTELEID" in " ".join(result.warnings)
    assert shop_sheet_rows(gateway.values[TAB_AUTO])[0][0]["PRICE"] == 150000


def test_menu_is_official_quote_from_both_sources() -> None:
    gateway = _menu_book()
    gateway.values[TAB_MANUAL] = [
        list(QUOTE_HEADERS),
        [1, "SLOT CANVA EDU 1 NĂM", "Gói giáo dục", "1 Năm", "Bảo hành 1:1", 39000, "Còn hàng"],
    ]
    result = CombinedQuoteWriter(gateway, "last-id").update(
        shop_quote_rows(_products()[:1]),
        dry_run=False,
    )
    assert result.applied is True
    assert result.menu_count == 2
    menu = gateway.values[TAB_MENU]
    assert menu[0][0] == "DIGITAL STORE"
    assert menu[5][9] == "WARRANTY POLICY"
    assert menu[6][0] == "🤖 SHOP XOÀI NON"
    assert menu[6][9] == "KEEP_SHOP"
    assert menu[7][0] == "SXN-01"
    assert menu[7][1].startswith("ChatGPT")
    assert menu[7][5] == 203000
    assert menu[7][7] == "IN STOCK"
    assert menu[7][9] == "KEEP_TWO"
    assert menu[9][0] == "🤖 AI ACCOUNTS"
    assert menu[14][0] == "💎 NASTELE"
    assert menu[15][0] == "NST-01"
    assert menu[15][1] == "SLOT CANVA EDU 1 NĂM"
    assert menu[15][3] == "1 Năm"
    assert menu[15][5] == 39000
    assert menu[14][9] == "KEEP_NST"
    assert gateway.values[TAB_MANUAL][1][1] == "SLOT CANVA EDU 1 NĂM"
    assert gateway.values[TAB_MANUAL][1][3] == "1 Năm"
    assert gateway.values[TAB_MANUAL][1][5] == 39000
    grid = menu_quote_grid(("🤖 SHOP XOÀI NON", "SXN", shop_quote_rows(_products()[:1])))
    assert grid[1][0] == "SXN-01"
    assert 7 not in _menu_rows_written(result.ranges)
    assert not any("unmergeCells" in str(batch) for batch in gateway.format_requests)


def test_menu_keeps_category_header_when_updating_shop_product() -> None:
    gateway = _menu_book()
    result = CombinedQuoteWriter(gateway, "last-id").update(
        shop_quote_rows(_products()[:1]),
        dry_run=False,
    )
    menu = gateway.values[TAB_MENU]
    assert menu[6][:8] == ["🤖 SHOP XOÀI NON", "", "", "", "", "", "", ""]
    assert menu[7][0] == "SXN-01"
    assert menu[7][5] == 203000
    assert menu[7][9] == "KEEP_TWO"
    assert 7 not in _menu_rows_written(result.ranges)
    names = [row["PRODUCT"] for row in list_menu_products(menu)]
    assert "🤖 SHOP XOÀI NON" not in names
    assert is_category_header_row(menu[6]) is True


def test_menu_appends_new_shop_product_below_category_header() -> None:
    grid = [
        ["DIGITAL STORE"],
        [],
        [],
        [],
        [],
        list(MENU_HEADERS) + ["", "WARRANTY POLICY"],
        ["🤖 SHOP XOÀI NON", "", "", "", "", "", "", "", "", "KEEP_SHOP"],
        ["SXN-01", "ChatGPT — Plus", "API mô tả", "1 Tháng", "BHF", 100, 2, "IN STOCK", "", "KEEP_TWO"],
        ["", "", "", "", "", "", "", ""],
        ["💎 NASTELE", "", "", "", "", "", "", "", "", "KEEP_NST"],
    ]
    gateway = _menu_book(grid)
    CombinedQuoteWriter(gateway, "last-id").update(
        shop_quote_rows(_products()),
        dry_run=False,
    )
    menu = gateway.values[TAB_MENU]
    assert menu[6][0] == "🤖 SHOP XOÀI NON"
    assert menu[7][0] == "SXN-01"
    assert menu[8][0] == "SXN-02"
    assert menu[8][1].startswith("Grok")
    assert menu[9][0] == "💎 NASTELE"
    assert menu[6][9] == "KEEP_SHOP"


def test_menu_places_other_category_in_its_own_region() -> None:
    gateway = _menu_book()
    CombinedQuoteWriter(gateway, "last-id").update(
        shop_quote_rows([_windows_product()]),
        dry_run=False,
    )
    menu = gateway.values[TAB_MENU]
    assert menu[6][0] == "🤖 SHOP XOÀI NON"
    assert menu[7][1].startswith("ChatGPT")
    assert menu[12][0] == "WINDOWS LICENSE"
    assert menu[13][0] == "WIN-01"
    assert menu[13][1].startswith("Windows 11 Pro")
    assert menu[8][0] == ""
    assert menu[9][0] == "🤖 AI ACCOUNTS"


def test_menu_replaces_contact_handle_in_description() -> None:
    product = Product(
        id="local_cg",
        title="ChatGPT",
        variants=[
            Variant(
                id="v1",
                price_vnd=168000,
                in_stock=True,
                in_stock_presence="value",
                title="Plus",
                description=(
                    "Định dạng: Email | Mật khẩu | 2FA\n"
                    "Inbox admin @tiensky123 để nhận mã yêu cầu. "
                    "Nhắc @tiensky123 nếu chậm."
                ),
            )
        ],
    )
    gateway = _menu_book()
    CombinedQuoteWriter(gateway, "last-id").update(
        shop_quote_rows([product]),
        dry_run=False,
    )
    description = str(gateway.values[TAB_MENU][7][2])
    assert "@admin" in description
    assert "@tiensky123" not in description
    assert "Định dạng: Email | Mật khẩu | 2FA" in description
    assert gateway.values[TAB_MENU][7][5] == 203000


def test_menu_bulk_update_does_not_touch_ui_or_warranty() -> None:
    gateway = _menu_book()
    gateway.merges[TAB_MENU] = [
        {"startRowIndex": 6, "endRowIndex": 7, "startColumnIndex": 0, "endColumnIndex": 8}
    ]
    gateway.values[TAB_MANUAL] = [
        list(QUOTE_HEADERS),
        [1, "SLOT CANVA EDU 1 NĂM", "Gói giáo dục", "1 Năm", "Bảo hành 1:1", 39000, "Còn hàng"],
    ]
    result = CombinedQuoteWriter(gateway, "last-id").update(
        shop_quote_rows(_products() + [_windows_product()]),
        dry_run=False,
    )
    menu = gateway.values[TAB_MENU]
    assert menu[0][0] == "DIGITAL STORE"
    assert menu[5][:8] == list(MENU_HEADERS)
    assert menu[6][0] == "🤖 SHOP XOÀI NON"
    assert menu[9][0] == "🤖 AI ACCOUNTS"
    assert menu[12][0] == "WINDOWS LICENSE"
    assert menu[14][0] == "💎 NASTELE"
    assert menu[6][9] == "KEEP_SHOP"
    assert menu[9][9] == "KEEP_AI"
    assert menu[12][9] == "KEEP_WIN"
    assert menu[14][9] == "KEEP_NST"
    written = _menu_rows_written(result.ranges)
    assert written.isdisjoint({1, 2, 3, 4, 5, 6, 7, 10, 13, 15, 17})
    assert menu[16][9] == "⚠️ LƯU Ý PHÂN BIỆT"
    assert not any("unmergeCells" in str(batch) for batch in gateway.format_requests)
    assert all(not item.startswith(f"'{TAB_MENU}'!J") for item in result.ranges)
    assert all(not item.startswith(f"'{TAB_MENU}'!I") for item in result.ranges)


def test_menu_stock_and_status_follow_source() -> None:
    gateway = _menu_book()
    CombinedQuoteWriter(gateway, "last-id").update(
        shop_quote_rows([_windows_product(), _sold_out_product()]),
        dry_run=False,
    )
    menu = gateway.values[TAB_MENU]
    windows = menu[13]
    assert windows[6] == 3
    assert windows[7] == "LOW STOCK"
    office_row = next(row for row in list_menu_products(menu) if "Office" in str(row["PRODUCT"]))
    assert office_row["STOCK"] == 0
    assert office_row["STATUS"] == "SOLD OUT"
    assert "@tiensky123" not in str(office_row["DESCRIPTION"])
    assert "@admin" in str(office_row["DESCRIPTION"])


def test_existing_menu_description_is_sanitized_even_if_product_is_not_in_source() -> None:
    grid = _menu_storefront()
    grid[10][2] = "Inbox @tiensky123 và @tiensky1234."
    gateway = _menu_book(grid)
    CombinedQuoteWriter(gateway, "last-id").update(
        shop_quote_rows(_products()[:1]),
        dry_run=False,
    )
    row = gateway.values[TAB_MENU][10]
    assert row[0] == "AI-01"
    assert row[1] == "Claude"
    assert row[2] == "Inbox @admin và @tiensky1234."
    assert gateway.values[TAB_MENU][6][0] == "🤖 SHOP XOÀI NON"
    assert gateway.values[TAB_MENU][9][0] == "🤖 AI ACCOUNTS"


def test_category_headers_are_detected_by_structure() -> None:
    grid = _menu_storefront()
    regions = parse_menu_regions(grid, 5)
    keys = [region.key for region in regions]
    assert keys == ["shop xoài non", "ai accounts", "windows", "nastele"]
    assert all(region.header_row != 0 for region in regions)
    products = list_menu_products(grid)
    assert [row["ID"] for row in products] == ["SXN-01", "AI-01"]
    assert is_category_header_row(["AI PREMIUM / UPGRADE", "", "", "", "", "", "", ""]) is True
    assert is_category_header_row(["MICROSOFT OFFICE", "", "", "", "", "", "", ""]) is True
    assert is_category_header_row(["SOFTWARE & DIGITAL", "", "", "", "", "", "", ""]) is True
    assert is_category_header_row(["SXN-07", "ChatGPT", "x", "1 Tháng", "BHF", 1, 1, "IN STOCK"]) is False


def _chatgpt_plus(*, quantity: int | None, in_stock: bool = True) -> Product:
    presence = "value" if quantity is not None else "absent"
    return Product(
        id="local_cg",
        title="ChatGPT",
        variants=[
            Variant(
                id="v1",
                price_vnd=168000,
                in_stock=in_stock,
                in_stock_presence="value",
                title="Plus",
                description="API mô tả",
                available_quantity=quantity,
                quantity_presence=presence,
            )
        ],
    )


def test_menu_stock_updates_on_existing_row() -> None:
    gateway = _menu_book()
    CombinedQuoteWriter(gateway, "last-id").update(
        shop_quote_rows([_chatgpt_plus(quantity=9)]),
        dry_run=False,
    )
    row = gateway.values[TAB_MENU][7]
    assert row[0] == "SXN-01"
    assert row[6] == 9
    assert row[7] == "IN STOCK"


def test_menu_keeps_stock_when_source_omits_quantity() -> None:
    gateway = _menu_book()
    CombinedQuoteWriter(gateway, "last-id").update(
        shop_quote_rows([_chatgpt_plus(quantity=None)]),
        dry_run=False,
    )
    row = gateway.values[TAB_MENU][7]
    assert row[6] == 2
    assert row[7] == "IN STOCK"


def test_sold_out_menu_stock_is_zero() -> None:
    gateway = _menu_book()
    CombinedQuoteWriter(gateway, "last-id").update(
        shop_quote_rows([_chatgpt_plus(quantity=4, in_stock=False)]),
        dry_run=False,
    )
    row = gateway.values[TAB_MENU][7]
    assert row[6] == 0
    assert row[7] == "SOLD OUT"


def test_decorated_menu_name_updates_stock_only() -> None:
    grid = _menu_storefront()
    grid[7][1] = "ChatGPT Plus (1 tháng)"
    grid[7][5] = 150000
    gateway = _menu_book(grid)
    CombinedQuoteWriter(gateway, "last-id").update(
        shop_quote_rows([_chatgpt_plus(quantity=9)]),
        dry_run=False,
    )
    row = gateway.values[TAB_MENU][7]
    assert row[1] == "ChatGPT Plus (1 tháng)"
    assert row[2] == "API mô tả"
    assert row[5] == 150000
    assert row[6] == 9
    assert row[7] == "IN STOCK"
    ids = [item[0] if item else "" for item in gateway.values[TAB_MENU]]
    assert ids.count("SXN-01") == 1
    assert "SXN-02" not in ids


def test_missing_shop_product_stock_expires_without_deleting_row() -> None:
    gateway = _menu_book()
    result = CombinedQuoteWriter(gateway, "last-id").update(
        shop_quote_rows([_chatgpt_plus(quantity=9)]),
        dry_run=False,
        expire_missing=True,
    )
    menu = gateway.values[TAB_MENU]
    assert menu[7][6] == 9
    assert menu[7][7] == "IN STOCK"
    claude = menu[10]
    assert claude[0] == "AI-01"
    assert claude[1] == "Claude"
    assert claude[5] == 1
    assert claude[6] == 0
    assert claude[7] == "SOLD OUT"
    assert menu[9][0] == "🤖 AI ACCOUNTS"
    assert "STOCK 0" in " ".join(result.warnings)


def test_empty_catalog_does_not_expire_menu_stock() -> None:
    gateway = _menu_book()
    CombinedQuoteWriter(gateway, "last-id").update([], dry_run=False, expire_missing=True)
    menu = gateway.values[TAB_MENU]
    assert menu[7][6] == 2
    assert menu[7][7] == "IN STOCK"
    assert menu[10][6] == 1
    assert menu[10][7] == "IN STOCK"


def test_contact_row_is_not_marked_sold_out() -> None:
    grid = _menu_storefront()
    grid[10][6] = ""
    grid[10][7] = "CONTACT"
    gateway = _menu_book(grid)
    CombinedQuoteWriter(gateway, "last-id").update(
        shop_quote_rows([_chatgpt_plus(quantity=9)]),
        dry_run=False,
        expire_missing=True,
    )
    claude = gateway.values[TAB_MENU][10]
    assert claude[1] == "Claude"
    assert claude[6] == ""
    assert claude[7] == "CONTACT"


def test_missing_hand_row_expires_only_nastele_region() -> None:
    grid = _menu_storefront()
    grid[15] = ["NST-01", "Canva cũ", "tay", "1 Năm", "BHF", 50, 8, "IN STOCK", "", "KEEP"]
    gateway = _menu_book(grid)
    gateway.values[TAB_MANUAL] = [
        list(QUOTE_HEADERS),
        [1, "SLOT CANVA EDU 1 NĂM", "Gói giáo dục", "1 Năm", "Bảo hành 1:1", 39000, "Còn hàng"],
    ]
    CombinedQuoteWriter(gateway, "last-id").update(
        [],
        dry_run=False,
        expire_missing=True,
    )
    menu = gateway.values[TAB_MENU]
    assert menu[7][6] == 2
    assert menu[7][7] == "IN STOCK"
    assert menu[10][7] == "IN STOCK"
    assert menu[15][0] == "NST-01"
    assert menu[15][1] == "Canva cũ"
    assert menu[15][6] == 0
    assert menu[15][7] == "SOLD OUT"
    assert menu[14][0] == "💎 NASTELE"
    assert gateway.values[TAB_MANUAL][1][1] == "SLOT CANVA EDU 1 NĂM"


def test_api_catalog_does_not_overwrite_hand_sheet() -> None:
    gateway = _menu_book()
    gateway.values[TAB_MANUAL] = [
        list(QUOTE_HEADERS),
        [1, "test", "nhãn tay", "", "", 5000, "Còn hàng"],
    ]
    result = CombinedQuoteWriter(gateway, "last-id").update(
        [],
        dry_run=False,
        nastele_rows=_nastele_canva(),
    )
    assert gateway.values[TAB_MANUAL][1][1] == "test"
    assert gateway.values[TAB_MANUAL][1][5] == 5000
    names = [row[1] for row in gateway.values[TAB_MENU] if len(row) > 1]
    assert "test" in names
    assert "SLOT CANVA EDU 1 NĂM" not in names
    assert "Không ghi catalog API" in " ".join(result.warnings)


def test_custom_hand_source_reaches_flat_menu() -> None:
    grid = [
        ["DIGITAL STORE"],
        [],
        [],
        [],
        [],
        list(MENU_HEADERS),
        ["SXN-01", "ChatGPT — Plus", "mô tả", "1 Tháng", "BHF", 100, 2, "IN STOCK"],
        ["", "", "", "", "", "", "", ""],
    ]
    gateway = _menu_book(grid)
    gateway.values[TAB_MANUAL] = [
        list(QUOTE_HEADERS),
        [1, "test", "mô tả tay", "TESST", "HELLO", 100000, 23],
    ]
    before = [list(row) for row in gateway.values[TAB_MANUAL]]
    result = CombinedQuoteWriter(gateway, "last-id").update([], dry_run=False, expire_missing=True)
    assert gateway.values[TAB_MANUAL] == before
    placed = next(row for row in gateway.values[TAB_MENU] if len(row) > 1 and row[1] == "test")
    assert str(placed[0]).startswith("SXN-")
    assert placed[2] == "mô tả tay"
    assert placed[3] == "TESST"
    assert placed[4] == "HELLO"
    assert placed[5] == 100000
    assert placed[6] == 23
    assert placed[7] == "IN STOCK"
    assert result.manual_count == 1
    assert gateway.values[TAB_MENU][6][6] == 2
