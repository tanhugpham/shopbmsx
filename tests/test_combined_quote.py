from __future__ import annotations

import re
from typing import Any

import httpx

from shop_integration.cli import build_parser
from shop_integration.combined_quote import (
    QUOTE_HEADERS,
    TAB_AUTO,
    TAB_COMBINED,
    TAB_MANUAL,
    CombinedQuoteWriter,
    manual_quote_rows,
    plan_tab,
    quote_layout_tables,
    render_quote_layout,
    shop_quote_rows,
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

    def list_sheet_titles(self, spreadsheet_id: str) -> list[str]:
        del spreadsheet_id
        return list(self.titles)

    def add_sheet(self, spreadsheet_id: str, title: str) -> None:
        del spreadsheet_id
        self.added.append(title)
        self.titles.append(title)
        self.values.setdefault(title, [])

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
    by_code = {row["Mã"]: row for row in shop}
    assert by_code["local_cg|v1"]["Giá bán"] == 203000
    assert by_code["local_cg|v1"]["Giá bán"] == round_pretty_vnd(quote_cost(168000).selling or 0)
    assert by_code["local_grok|v1"]["Giá bán"] == 103000
    assert by_code["local_cg|v1"]["Tên sản phẩm"] == "ChatGPT — Plus"
    assert by_code["local_cg|v1"]["Cấu hình"] == "API mô tả"
    assert by_code["local_grok|v1"]["Cấu hình"] == "Gói API"


def test_styled_sheets_keep_layout_prices_and_manual_tab() -> None:
    gateway = _styled_book()
    manual_before = [list(row) for row in gateway.values[TAB_MANUAL]]
    writer = CombinedQuoteWriter(gateway, "last-id")
    result = writer.update(shop_quote_rows(_products()), dry_run=False)

    assert result.applied is True
    assert result.manual_count == 2
    assert result.shop_count == 2
    assert gateway.added == []
    assert gateway.format_requests == []
    assert gateway.values[TAB_MANUAL] == manual_before
    assert {option for _id, option, _items in gateway.batches} == {"RAW"}
    assert {sheet_id for sheet_id, _option, _items in gateway.batches} == {"last-id"}
    ranges = [item[0] for _id, _option, items in gateway.batches for item in items]
    parsed = [_CELL.match(item) for item in ranges]
    assert all(item is not None and item.group(3) != "1" for item in parsed)
    assert all(not item.startswith(f"'{TAB_MANUAL}'") for item in ranges)
    assert not any(item.startswith(f"'{TAB_AUTO}'!A") for item in ranges)
    assert not any(item.startswith(f"'{TAB_COMBINED}'!A") for item in ranges)
    assert not any(item.startswith(f"'{TAB_COMBINED}'!H") for item in ranges)

    auto = gateway.values[TAB_AUTO]
    assert auto[0] == ["Ghi chú", "Nguồn", "Mã", "Tên sản phẩm", "Giá bán", "Tình trạng", "Cấu hình"]
    assert auto[1][0] == "giữ shop"
    assert auto[1][4] == 203000
    assert auto[1][3] == "ChatGPT — Plus"
    assert auto[1][6] == "API mô tả"
    assert auto[2][0] == "giữ mất"
    assert auto[2][1:] == ["", "", "", "", "", ""]
    assert len(auto) == 4
    assert auto[3][2] == "local_grok|v1"
    assert auto[3][4] == 103000
    assert all(row[4] != 102700 for row in auto[1:])

    combined = gateway.values[TAB_COMBINED]
    assert combined[0][0] == "Màu"
    assert combined[1][0] == "xanh"
    assert combined[1][4] == 102700
    assert combined[1][3] == "Canva tay"
    assert combined[1][6] == "Gói năm"
    assert combined[1][7] == "GIU_TONG"
    assert combined[2][0] == "đỏ"
    assert combined[2][4] == 203000
    assert combined[2][6] == "API mô tả"
    assert combined[2][7] == "GIU_SHOP"
    assert combined[3][7] == "DONG_TRONG"
    assert combined[4][0] == "vàng"
    assert combined[4][1:] == ["", "", "", "", "", "", "GIU_CU"]
    assert len(combined) == 7
    assert combined[5][1] == "NASTELEID"
    assert combined[5][2] == "N-2"
    assert str(combined[5][3]).startswith(FORMULA_PREFIX)
    assert combined[6][1] == "Shop Xoài Non"
    assert combined[6][4] == 103000
    assert 102700 in {combined[1][4]}
    assert 103000 in {combined[2][4], combined[6][4]}

    again = writer.update(shop_quote_rows(_products()), dry_run=False)
    assert again.ranges == []


def test_missing_key_header_skips_the_sheet() -> None:
    grid = [["Tiêu đề của tôi", "Ghi chú"], ["xin chào", "giữ"]]
    plan = plan_tab(
        TAB_COMBINED,
        grid,
        [{"Nguồn": "NASTELEID", "Mã": "N-1", "Giá bán": 102700}],
        exists=True,
        sources_to_refresh={"NASTELEID"},
    )
    assert plan.cells == []
    assert plan.full_grid is None
    assert "Không ghi" in plan.warning


def test_new_tabs_get_values_only_and_manual_tab_is_not_created() -> None:
    gateway = QuoteMemory(
        [TAB_MANUAL],
        {
            TAB_MANUAL: [
                ["Nastele ID", "Tên sản phẩm", "Giá bán", "Tình trạng", "Cấu hình"],
                ["N-9", "Tay", 88100, "Còn", "Giữ"],
            ]
        },
    )
    result = CombinedQuoteWriter(gateway, "last-id").update(
        shop_quote_rows(_products()[:1]),
        dry_run=False,
    )
    assert gateway.added == [TAB_AUTO, TAB_COMBINED]
    assert TAB_MANUAL not in gateway.added
    assert gateway.format_requests == []
    assert gateway.values[TAB_AUTO][0] == [
        "Nguồn",
        "Mã",
        "Tên sản phẩm",
        "Giá bán",
        "Tình trạng",
        "Cấu hình",
    ]
    assert gateway.values[TAB_AUTO][1][3] == 203000
    assert gateway.values[TAB_COMBINED][1][0] == "NASTELEID"
    assert gateway.values[TAB_COMBINED][1][3] == 88100
    assert gateway.values[TAB_COMBINED][2][3] == 203000
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
    assert combined[1][3] == 102700
    assert combined[1][5] == "Gói năm"
    assert combined[2][3] == 203000
    assert combined[2][5] == "API mô tả"
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
    assert quote.format_requests == []
    assert {sheet_id for sheet_id, _option, _items in quote.batches} == {"last-id"}
    assert quote.values[TAB_AUTO][1][3] == 700
    prices = [row[3] for row in quote.values[TAB_COMBINED][1:]]
    assert prices == [102700, 700]
    assert "Không ghi NASTELEID" in " ".join(report.warnings)


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
    combined = {row[1]: row for row in gateway.values[TAB_COMBINED][1:]}
    assert "43" in combined
    assert combined["43"][3] == 102700
    assert "local_old|v" not in combined
    assert combined["local_cg|v1"][3] == 203000


def test_unified_nasteleid_headers_are_read() -> None:
    rows, warning = manual_quote_rows(
        [
            list(QUOTE_HEADERS),
            ["NASTELEID", "43", "Canva Pro 1 năm", 102700, "Còn hàng", "Gói năm"],
            ["", "44", "CapCut", 88000, "Hết hàng", ""],
        ]
    )
    assert warning == ""
    assert [row["Mã"] for row in rows] == ["43", "44"]
    assert rows[0]["Nguồn"] == "NASTELEID"
    assert rows[0]["Giá bán"] == 102700
    assert rows[1]["Nguồn"] == "NASTELEID"


def test_nasteleid_skips_shop_rows_and_cost_columns() -> None:
    rows, warning = manual_quote_rows(
        [
            ["Nguồn", "Mã", "Tên sản phẩm", "Giá bán", "Giá gốc", "Tình trạng", "Cấu hình"],
            ["Shop Xoài Non", "local_cg|v1", "ChatGPT", 203000, 168000, "Còn hàng", "API"],
            ["NASTELEID", "43", "Canva", 102700, 79000, "Còn hàng", "Gói năm"],
        ]
    )
    assert [row["Mã"] for row in rows] == ["43"]
    assert rows[0]["Giá bán"] == 102700
    assert "Giá gốc" in warning
    assert "bỏ 1 dòng" in warning


def test_legacy_nastele_id_header_still_reads() -> None:
    rows, warning = manual_quote_rows(
        [
            ["Nastele ID", "Tên sản phẩm", "Giá bán", "Tình trạng", "Cấu hình"],
            ["N-1", "Canva tay", 102700, "Còn", "Gói năm"],
        ]
    )
    assert warning == ""
    assert rows == [
        {
            "Nguồn": "NASTELEID",
            "Mã": "N-1",
            "Tên sản phẩm": "Canva tay",
            "Giá bán": 102700,
            "Tình trạng": "Còn",
            "Cấu hình": "Gói năm",
        }
    ]


def test_missing_code_header_skips_manual_tab() -> None:
    rows, warning = manual_quote_rows([["Tên sản phẩm", "Giá bán"], ["Canva", 1000]])
    assert rows == []
    assert "thiếu cột Mã" in warning


def test_quote_layout_tables_cover_both_files() -> None:
    tables = {title: (headers, rows) for title, headers, rows in quote_layout_tables()}
    files = tables["Hai file Google"][1]
    assert files[0][0] == "Catalog Shop"
    assert files[0][1] == "GOOGLE_SPREADSHEET_ID"
    assert files[1][0] == "Báo giá tổng hợp"
    assert files[1][1] == "LAST_SPREADSHEET_ID"
    columns = tables["Cột thống nhất — dòng 1 của NASTELEID, BAO_GIA, BAO_GIA_TONG_HOP"][1]
    assert [row[0] for row in columns] == list(QUOTE_HEADERS)
    text = render_quote_layout()
    assert text.count("|") > 20
    assert "Giá gốc" in text
    assert "Không copy từ file catalog sang file tổng hợp" in text


def test_quote_layout_command_prints_tables() -> None:
    parser = build_parser()
    args = parser.parse_args(["quote-layout"])
    assert args.command == "quote-layout"
