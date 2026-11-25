"""Hồi quy Phase 4.3D: vùng A31:H35, dòng legacy SXN-24, protected range, ProductID.

Không ghi Google Sheets. Mọi test dùng gateway giả hoặc cấu trúc dựng tay.
"""

from __future__ import annotations

from typing import Any

import pytest

from shop_integration.combined_quote import (
    MENU_HEADERS,
    QUOTE_HEADERS,
    SOURCE_SHOP,
    TAB_AUTO,
    TAB_MENU,
    CombinedQuoteWriter,
    parse_menu_regions,
    plan_menu_cells,
    plan_tab,
    quote_layout_problems,
    shop_quote_rows,
    shop_sheet_rows,
)
from shop_integration.errors import MenuLayoutError, SheetLayoutError
from shop_integration.menu_audit import audit_menu_grid, same_source_product
from shop_integration.menu_layout import (
    MenuCapacity,
    MenuLayoutGuard,
    menu_baseline_from_structure,
    menu_structure_diff,
    validate_menu_rows,
)
from shop_integration.models import Product, Variant
from tests.test_combined_quote import QuoteMemory, _products
from tests.test_phase43b_menu_layout import StructuredBook

_HEADER_ROW = 6
_PANEL_START = 37
_SXN_NAMES = [
    "ChatGPT Plus 1 tháng riêng tư - BHF",
    "Canva Pro 1 năm chính chủ (BHF)",
    "Google Drive chính chủ 100GB/năm (BHF)",
    "Google Drive chính chủ 400GB/năm (BHF)",
    "Google Drive chính chủ 1000GB/năm (BHF)",
    "Google Drive chính chủ 2000GB/năm (BHF)",
    "test api",
    "Grok Plus 1 tháng riêng tư - BH7D",
    "Capcut Pro 30d full Date - BHF",
    "Chatgpt Plus pay Apple Gmail 1 thg riêng tư - BH 24H",
    "Memrise Pro gói trọn đời chính chủ",
    "Capcut Pro 7 ngày 500 Credit BH6D",
    "Meitu SVip, VIP+ 7 days riêng tư - BHF",
    "Wink VIP+ riêng tư 7 Days",
    "Duolingo Super 12 Tháng Chính Chủ",
    "Nord VPN chính chủ Basic 3 tháng code",
    "ChatGPT Plus 1 tháng riêng tư mail Gmail UPI- BH24H",
    "Tài khoản Canva Pro 1 tháng",
    "Tài Khoản Figma Pro Edu 2 Năm",
    "Microsoft Office 365 Plus 1 năm riêng tư",
    "Tài khoản MUSE AI 2 TỶ TOKEN KBH",
    "Grok Premium+ Cursor 14 - 24 ngày (BH 1D)",
    "Gói Gemini AI Plus & Drive 400GB chính chủ",
]
_MISSING_NAME = "Adobe All Apps 1 Tháng riêng tư - BH7D"


def _blank_row() -> list[Any]:
    return [""] * 13


def _policy_row() -> list[Any]:
    row = _blank_row()
    row[9] = "• Ghi chú chính sách bảo hành"
    return row


def _live_grid() -> list[list[Any]]:
    grid = [_blank_row() for _ in range(40)]
    grid[1] = ["⚡ DIGITAL STORE", "", "", "", "", "", "", "", "", "🛡️ WARRANTY POLICY"]
    grid[4] = ["🕒 CẬP NHẬT LẦN CUỐI:", "", "", "", "", "", "10:42:59", ""]
    grid[5] = list(MENU_HEADERS) + ["", "📋 CÁC GÓI BẢO HÀNH"]
    for offset, name in enumerate(_SXN_NAMES, start=8):
        grid[offset - 1] = [
            f"SXN-{offset - 7:02d}",
            name,
            "Mô tả khách",
            "1 Tháng",
            "BHF",
            200000 + offset,
            0,
            "SOLD OUT",
            "",
            "",
            "",
            "",
            "",
        ]
    for row_number in range(31, 36):
        grid[row_number - 1] = _policy_row()
    grid[35] = [
        "SXN-24",
        "Shop Xoài Non",
        "ChatGPT Plus 1 tháng riêng tư - BHF",
        "ChatGPT Plus 1 tháng riêng tư - BHF",
        "Định dạng : Email|Mật khẩu|2FA. Tài khoản riêng tư, mô tả dài bị dồn vào một ô.",
        203000,
        "",
        "IN STOCK",
        "",
        "",
        "",
        "",
        "",
    ]
    grid[36] = _blank_row()
    grid[36][9] = "📋 CHI TIẾT MÔ TẢ SẢN PHẨM"
    grid[37] = _blank_row()
    grid[37][9] = "🔍 Chọn SP:"
    grid[37][10] = "ChatGPT Plus 1 tháng riêng tư - BHF"
    grid[38] = _blank_row()
    grid[38][9] = "⏱️ Thời hạn: 1 Tháng"
    grid[39] = _blank_row()
    grid[39][9] = "Mô tả chi tiết"
    return grid


def _source_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, name in enumerate(_SXN_NAMES, start=1):
        rows.append(
            {
                "ID": index,
                "Nguồn": SOURCE_SHOP,
                "PRODUCT": name,
                "DESCRIPTION": "Mô tả",
                "DURATION": "1 Tháng",
                "WARRANTY": "BHF",
                "PRICE": 200000 + index,
                "STOCK": 1,
                "ProductID": f"local_{index}",
                "VariantID": f"local_{index}",
            }
        )
    rows.append(
        {
            "ID": 24,
            "Nguồn": SOURCE_SHOP,
            "PRODUCT": _MISSING_NAME,
            "DESCRIPTION": "Mô tả Adobe",
            "DURATION": "1 Tháng",
            "WARRANTY": "BH7D",
            "PRICE": 90000,
            "STOCK": 7,
            "ProductID": "local_41",
            "VariantID": "local_41",
        }
    )
    return rows


def _merges() -> list[dict[str, int]]:
    return [
        {"startRowIndex": 5, "endRowIndex": 6, "startColumnIndex": 0, "endColumnIndex": 8},
        *[
            {
                "startRowIndex": row - 1,
                "endRowIndex": row,
                "startColumnIndex": 9,
                "endColumnIndex": 13,
            }
            for row in (31, 32, 33, 34, 35, 37, 39)
        ],
        {"startRowIndex": 39, "endRowIndex": 45, "startColumnIndex": 9, "endColumnIndex": 13},
    ]


def _formulas(end_row: int = 30) -> list[dict[str, str]]:
    return [
        {"cell": "G5", "formula": "=NOW()"},
        {
            "cell": "J39",
            "formula": (
                f'="⏱️ Thời hạn: " & IFERROR(VLOOKUP(K38; B8:H{end_row}; 3; FALSE); "—")'
            ),
        },
        {
            "cell": "J40",
            "formula": (
                '=IFERROR(VLOOKUP(K38; BAO_GIA_TONG_HOP!C2:E24; 3; FALSE); '
                f'IFERROR(VLOOKUP(K38; B8:C{end_row}; 2; FALSE); "—"))'
            ),
        },
    ]


def _validations(end_row: int = 30) -> list[dict[str, Any]]:
    return [
        {
            "cell": "K38",
            "type": "ONE_OF_RANGE",
            "values": [{"userEnteredValue": f"=MENU!$B$8:$B${end_row}"}],
        }
    ]


def _structure(
    *,
    grid: list[list[Any]] | None = None,
    selector_end: int = 30,
    protected: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    values = [list(row) for row in (grid or _live_grid())]
    return {
        "title": TAB_MENU,
        "sheet_id": 1,
        "grid_properties": {"rowCount": 1000, "columnCount": 26, "frozenRowCount": 6},
        "conditional_format_count": 8,
        "merges": _merges(),
        "protected_ranges": protected
        if protected is not None
        else [
            {
                "startRowIndex": 0,
                "endRowIndex": 6,
                "startColumnIndex": 0,
                "endColumnIndex": 8,
                "warningOnly": True,
            },
            {
                "startRowIndex": 0,
                "endRowIndex": 55,
                "startColumnIndex": 8,
                "endColumnIndex": 13,
                "warningOnly": True,
            },
        ],
        "values": values,
        "formulas": _formulas(selector_end),
        "data_validations": _validations(selector_end),
        "column_widths": {"A": 75, "B": 230, "C": 110, "D": 105, "E": 115, "F": 125, "G": 85, "H": 125},
        "row_heights": {},
    }


def _capacity(**kwargs: Any) -> MenuCapacity:
    baseline = menu_baseline_from_structure(_structure(**kwargs), MENU_HEADERS)
    return MenuCapacity.from_baseline(baseline)


def test_baseline_captures_protected_ranges_and_guard_blocks_header() -> None:
    baseline = menu_baseline_from_structure(_structure(), MENU_HEADERS)
    assert baseline.protected_ranges_captured is True
    assert len(baseline.protected_ranges) == 2
    assert baseline.data_rows == 40
    assert {"A1", "H6", "I7", "M40"} <= set(baseline.protected_cells)

    guard = MenuLayoutGuard.from_baseline(baseline)
    guard.check_cell("'MENU'!A31")
    guard.check_cell("'MENU'!H35")
    for forbidden in ("'MENU'!A6", "'MENU'!I31", "'MENU'!J31", "'MENU'!A37", "'MENU'!H40"):
        with pytest.raises(MenuLayoutError):
            guard.check_cell(forbidden)


def test_structure_diff_reports_protected_range_drift() -> None:
    baseline = menu_baseline_from_structure(_structure(), MENU_HEADERS)
    changed = menu_baseline_from_structure(
        _structure(
            protected=[
                {
                    "startRowIndex": 0,
                    "endRowIndex": 5,
                    "startColumnIndex": 0,
                    "endColumnIndex": 8,
                    "warningOnly": True,
                }
            ]
        ),
        MENU_HEADERS,
    )
    assert any("Vùng bảo vệ" in item for item in menu_structure_diff(baseline, changed))


def test_capacity_reserves_detail_panel_and_exposes_rows_31_to_35() -> None:
    capacity = _capacity()
    assert capacity.selector_span == "B8:B30"
    assert capacity.selector_max_row == 30
    assert capacity.panel_start_row == 37
    assert capacity.last_writable_row == 36

    regions = parse_menu_regions(_live_grid(), 5, capacity=capacity)
    assert len(regions) == 1
    region = regions[0]
    assert region.key == "shop xoài non"
    assert region.product_rows[0] == 8
    assert region.product_rows[-1] == 36
    assert region.empty_rows == [31, 32, 33, 34, 35]


def test_protected_or_formula_cell_in_a_h_is_not_an_empty_slot() -> None:
    protected = [
        {
            "startRowIndex": 30,
            "endRowIndex": 31,
            "startColumnIndex": 0,
            "endColumnIndex": 8,
            "warningOnly": True,
        }
    ]
    capacity = _capacity(protected=protected)
    regions = parse_menu_regions(_live_grid(), 5, capacity=capacity)
    assert 31 not in regions[0].empty_rows
    assert regions[0].empty_rows[:1] == [32]


def test_24th_product_aborts_with_selector_scope_instead_of_silent_skip() -> None:
    capacity = _capacity()
    before = [list(row) for row in _live_grid()]
    with pytest.raises(MenuLayoutError) as caught:
        plan_menu_cells(
            _live_grid(),
            _source_rows(),
            [],
            5,
            strict_capacity=True,
            capacity=capacity,
        )
    message = str(caught.value)
    assert "dòng 31" in message
    assert "B8:B30" in message
    assert "K38" in message
    assert "J39/J40" in message
    assert _live_grid() == before


def test_24th_product_uses_row_31_when_selector_is_expanded() -> None:
    capacity = _capacity(selector_end=35)
    assert capacity.selector_span == "B8:B35"
    cells, placed, _notes = plan_menu_cells(
        _live_grid(),
        _source_rows(),
        [],
        5,
        strict_capacity=True,
        capacity=capacity,
    )
    assert placed == 24
    row31 = [value for range_a1, value in cells if range_a1.rsplit("!", 1)[1].endswith("31")]
    assert len(row31) == 8
    assert all(cell.endswith("31") for cell, _value in cells if cell.endswith("31"))



def test_legacy_sxn24_is_quarantined_and_mapped_by_product_id() -> None:
    capacity = _capacity()
    report = audit_menu_grid(_live_grid(), 6, capacity, _source_rows())
    assert report.product_count == 24
    assert report.last_product_row == 36
    assert report.extension_rows == [31, 32, 33, 34, 35]
    assert len(report.legacy_rows) == 1
    legacy = report.legacy_rows[0]
    assert legacy.row_number == 36
    assert legacy.menu_id == "SXN-24"
    assert legacy.product_cell == "Shop Xoài Non"
    assert legacy.description_cell == "ChatGPT Plus 1 tháng riêng tư - BHF"
    assert legacy.matched_product_id == "local_1"
    assert legacy.matched_variant_id == "local_1"
    assert legacy.duplicate_of == "SXN-01"
    assert "lệch cột" in legacy.reason
    assert 36 not in report.extension_rows


def test_missing_24th_source_points_to_row_31_with_product_id() -> None:
    capacity = _capacity()
    report = audit_menu_grid(_live_grid(), 6, capacity, _source_rows())
    assert len(report.missing_sources) == 1
    missing = report.missing_sources[0]
    assert missing.product_id == "local_41"
    assert missing.variant_id == "local_41"
    assert missing.name == _MISSING_NAME
    assert missing.suggested_row == 31
    assert any("ngoài vùng chọn K38" in item for item in report.warnings)


def test_shifted_bao_gia_columns_are_rejected_before_read_or_write() -> None:
    shifted = [
        [*QUOTE_HEADERS, "STOCK"],
        [1, SOURCE_SHOP, _SXN_NAMES[0], _SXN_NAMES[0], "Mô tả dài", 203000, "Còn hàng"],
    ]
    problems = quote_layout_problems(shifted, tab=TAB_AUTO)
    assert problems and "lệch cột" in problems[0]
    with pytest.raises(SheetLayoutError):
        shop_sheet_rows(shifted)
    with pytest.raises(SheetLayoutError):
        plan_tab(
            TAB_AUTO,
            shifted,
            _source_rows(),
            exists=True,
            sources_to_refresh={SOURCE_SHOP},
        )
    assert shop_sheet_rows([list(QUOTE_HEADERS)])[0] == []


def test_duplicate_product_names_with_different_ids_are_rejected() -> None:
    rows = [
        {
            "PRODUCT": "Cùng tên",
            "PRICE": 100000,
            "STOCK": 1,
            "ProductID": "local_a",
            "VariantID": "v1",
        },
        {
            "PRODUCT": "Cùng tên",
            "PRICE": 100000,
            "STOCK": 1,
            "ProductID": "local_b",
            "VariantID": "v2",
        },
    ]
    problems = validate_menu_rows(rows, source=SOURCE_SHOP)
    assert any("khác ProductID/VariantID" in item for item in problems)
    assert any("Không dùng tên làm định danh" in item for item in problems)


def test_shop_quote_rows_carry_product_and_variant_ids() -> None:
    rows = shop_quote_rows(_products())
    assert rows
    assert rows[0]["ProductID"] == "local_cg"
    assert rows[0]["VariantID"] == "v1"
    assert all(row.get("ProductID") for row in rows)

def test_renamed_source_matches_by_name_tokens_and_price_not_name_only() -> None:
    menu_entries = [
        ("grok premium+ cursor 14 - 24 ngày (bh 1d)", 140000),
        ("canva pro 1 năm chính chủ (bhf)", 89000),
    ]
    assert same_source_product(
        "Grok Premium 14 - 24 ngày (BH 1D)",
        140000,
        menu_entries,
    )
    assert not same_source_product(
        "Adobe All Apps 1 Tháng riêng tư - BH7D",
        90000,
        menu_entries,
    )



def test_expire_missing_never_touches_legacy_row_outside_selector() -> None:
    capacity = _capacity()
    cells, placed, _notes = plan_menu_cells(
        _live_grid(),
        _source_rows()[:23],
        [],
        5,
        expire_missing=True,
        strict_capacity=True,
        capacity=capacity,
    )
    assert placed == 23
    assert all(not range_a1.endswith("36") for range_a1, _value in cells)


def test_writer_uses_capacity_even_when_layout_guard_is_passed() -> None:
    structure = _structure()
    baseline = menu_baseline_from_structure(structure, MENU_HEADERS)
    guard = MenuLayoutGuard.from_baseline(baseline)
    book = StructuredBook(
        [TAB_MENU, "NASTELEID", "BAO_GIA", "BAO_GIA_TONG_HOP"],
        {
            TAB_MENU: [list(row) for row in _live_grid()],
            "NASTELEID": [list(QUOTE_HEADERS)],
        },
        structure,
    )
    before = [list(row) for row in book.values[TAB_MENU]]
    with pytest.raises(MenuLayoutError) as caught:
        CombinedQuoteWriter(book, "last-id", layout_guard=guard).update(
            _source_rows(),
            dry_run=False,
        )
    assert "dòng 31" in str(caught.value)
    assert book.values[TAB_MENU] == before
    assert book.batches == []
