"""Hồi quy Phase 4.3B: khóa layout MENU, không ghi J:M, không đổi merge/format."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from shop_integration.combined_quote import (
    MENU_HEADERS,
    TAB_MENU,
    CombinedQuoteWriter,
    shop_quote_rows,
)
from shop_integration.errors import MenuLayoutError
from shop_integration.menu_layout import (
    LayoutLockedGateway,
    MenuLayoutGuard,
    assert_menu_rows_valid,
    load_menu_baseline,
    menu_baseline_from_structure,
    menu_structure_diff,
    read_menu_baseline,
    save_menu_baseline,
    validate_menu_rows,
)
from shop_integration.menu_sync import run_menu_sync
from tests.conftest import make_settings
from tests.test_combined_quote import QuoteMemory, _menu_storefront, _products
from tests.test_menu_sync import StockShop

_SHEET_ID = 1  # QuoteMemory đánh sheetId theo thứ tự, MENU đứng đầu.


def _structure(
    grid: list[list[Any]] | None = None,
    *,
    merges: list[dict[str, int]] | None = None,
    formulas: list[dict[str, str]] | None = None,
    validations: list[dict[str, Any]] | None = None,
    widths: dict[str, int] | None = None,
) -> dict[str, Any]:
    values = [list(row) for row in (grid or _menu_storefront())]
    return {
        "title": TAB_MENU,
        "sheet_id": _SHEET_ID,
        "grid_properties": {
            "rowCount": max(len(values), 50),
            "columnCount": 13,
            "frozenRowCount": 6,
        },
        "conditional_format_count": 2,
        "merges": merges or [],
        "values": values,
        "formulas": formulas or [],
        "data_validations": validations or [],
        "column_widths": widths
        or {
            "A": 70,
            "B": 280,
            "C": 360,
            "D": 120,
            "E": 140,
            "F": 120,
            "G": 100,
            "H": 100,
            "I": 30,
            "J": 150,
            "K": 150,
            "L": 150,
            "M": 150,
        },
        "row_heights": {"6": 32},
    }


class StructuredBook(QuoteMemory):
    """QuoteMemory có thêm cấu trúc MENU để thử guard trước khi ghi."""

    def __init__(
        self,
        titles: list[str],
        values: dict[str, list[list[Any]]],
        structure: dict[str, Any],
    ) -> None:
        super().__init__(titles, values)
        self.structure = structure

    def get_sheet_structure(
        self, spreadsheet_id: str, title: str, **_kwargs: Any
    ) -> dict[str, Any]:
        del spreadsheet_id, title
        return self.structure


def _structured_book(structure: dict[str, Any] | None = None) -> StructuredBook:
    return StructuredBook(
        [TAB_MENU, "NASTELEID", "BAO_GIA", "BAO_GIA_TONG_HOP"],
        {
            TAB_MENU: [list(row) for row in _menu_storefront()],
            "NASTELEID": [
                ["ID", "PRODUCT", "DESCRIPTION", "DURATION", "WARRANTY", "PRICE", "STOCK"]
            ],
        },
        structure or _structure(),
    )


def _policy_snapshot(grid: list[list[Any]]) -> list[list[Any]]:
    return [
        [str(row[column]) if column < len(row) else "" for column in range(9, 13)]
        for row in grid
    ]


def _request_sheet_ids(node: Any) -> list[Any]:
    found: list[Any] = []
    if isinstance(node, dict):
        if "sheetId" in node:
            found.append(node["sheetId"])
        for value in node.values():
            found.extend(_request_sheet_ids(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_request_sheet_ids(value))
    return found


def _menu_format_requests(book: QuoteMemory) -> list[dict[str, Any]]:
    return [
        request
        for batch in book.format_requests
        for request in batch
        if _SHEET_ID in _request_sheet_ids(request)
    ]


def _merge_shop_header() -> list[dict[str, int]]:
    return [
        {
            "startRowIndex": 6,
            "endRowIndex": 7,
            "startColumnIndex": 0,
            "endColumnIndex": 8,
        }
    ]


def test_baseline_counts_merges_formulas_dropdowns_and_roundtrips(
    tmp_path: Path,
) -> None:
    structure = _structure(
        merges=_merge_shop_header(),
        formulas=[{"cell": "F8", "formula": "=SUM(F7)"}],
        validations=[
            {
                "cell": f"H{row}",
                "type": "ONE_OF_LIST",
                "values": ["IN STOCK", "SOLD OUT"],
            }
            for row in (7, 8, 9)
        ],
    )
    baseline = menu_baseline_from_structure(structure, MENU_HEADERS, captured_at="test")
    assert baseline.header_row == 6
    assert baseline.merge_count == 1
    assert len(baseline.formulas) == 1
    assert baseline.data_validations[0]["range"] == "H7:H9"
    assert baseline.headers == MENU_HEADERS
    assert baseline.data_rows == 17
    assert {"A7", "H7", "F8"} <= set(baseline.protected_cells)

    target = save_menu_baseline(baseline, tmp_path / "menu-baseline.json")
    loaded = load_menu_baseline(target)
    assert loaded.fingerprint() == baseline.fingerprint()
    assert loaded.protected_cells == baseline.protected_cells


def test_guard_rejects_policy_merged_formula_and_row_overflow() -> None:
    structure = _structure(
        merges=_merge_shop_header(),
        formulas=[{"cell": "F8", "formula": "=SUM(F7)"}],
    )
    baseline = menu_baseline_from_structure(structure, MENU_HEADERS)
    guard = MenuLayoutGuard.from_baseline(baseline)

    guard.check_cell("'MENU'!A9")
    guard.check_cell("'MENU'!H9")
    for forbidden in (
        "'MENU'!I7",
        "'MENU'!J7",
        "'MENU'!M17",
        "'MENU'!H7",
        "'MENU'!F8",
    ):
        with pytest.raises(MenuLayoutError):
            guard.check_cell(forbidden)
    with pytest.raises(MenuLayoutError):
        guard.check_cell(f"'MENU'!A{baseline.data_rows + 1}")
    with pytest.raises(MenuLayoutError):
        guard.check_cell("'MENU'!A1")
    with pytest.raises(MenuLayoutError):
        guard.check_cell("'BAO_GIA'!A9")


def test_guard_blocks_menu_batch_requests_but_not_other_tabs() -> None:
    baseline = menu_baseline_from_structure(_structure(), MENU_HEADERS)
    guard = MenuLayoutGuard.from_baseline(baseline)

    with pytest.raises(MenuLayoutError):
        guard.check_batch_requests(
            [
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": _SHEET_ID,
                            "startRowIndex": 5,
                            "endRowIndex": 6,
                        },
                        "cell": {
                            "userEnteredFormat": {"textFormat": {"bold": True}}
                        },
                        "fields": "userEnteredFormat.textFormat.bold",
                    }
                }
            ]
        )
    guard.check_batch_requests(
        [
            {
                "mergeCells": {
                    "range": {"sheetId": 99, "startRowIndex": 0, "endRowIndex": 1},
                    "mergeType": "MERGE_ALL",
                }
            }
        ]
    )


def test_layout_locked_gateway_blocks_menu_ranges_and_append() -> None:
    baseline = menu_baseline_from_structure(_structure(), MENU_HEADERS)
    guard = MenuLayoutGuard.from_baseline(baseline)
    book = _structured_book()
    gateway = LayoutLockedGateway(book, guard)

    gateway.batch_update_values("last-id", [("'MENU'!A9", [["SXN-99"]])])
    assert book.values[TAB_MENU][8][0] == "SXN-99"
    with pytest.raises(MenuLayoutError):
        gateway.batch_update_values("last-id", [("'MENU'!J7", [["x"]])])
    with pytest.raises(MenuLayoutError):
        gateway.update_values("last-id", "'MENU'!K9", [["x"]])
    with pytest.raises(MenuLayoutError):
        gateway.append_values("last-id", "'MENU'!A1", [["x"]])
    with pytest.raises(MenuLayoutError):
        gateway.batch_update(
            "last-id",
            [
                {
                    "unmergeCells": {
                        "range": {"sheetId": _SHEET_ID, "startRowIndex": 6, "endRowIndex": 7}
                    }
                }
            ],
        )
    gateway.batch_update(
        "last-id",
        [
            {
                "unmergeCells": {
                    "range": {"sheetId": 99, "startRowIndex": 6, "endRowIndex": 7}
                }
            }
        ],
    )




def _overflow_grid() -> list[list[Any]]:
    return [
        ["DIGITAL STORE"],
        ["banner"],
        [],
        [],
        [],
        list(MENU_HEADERS) + ["", "WARRANTY POLICY"],
        ["🤖 SHOP XOÀI NON", "", "", "", "", "", "", "", "", "KEEP_SHOP"],
        ["SXN-01", "ChatGPT — Plus", "API mô tả", "1 Tháng", "BHF", 100, 2, "IN STOCK", "", "KEEP_TWO"],
        ["💎 NASTELE", "", "", "", "", "", "", "", "", "KEEP_NST"],
    ]


def test_shop_overflow_aborts_without_writing_menu() -> None:
    grid = _overflow_grid()
    book = StructuredBook(
        [TAB_MENU, "NASTELEID", "BAO_GIA", "BAO_GIA_TONG_HOP"],
        {
            TAB_MENU: [list(row) for row in grid],
            "NASTELEID": [
                ["ID", "PRODUCT", "DESCRIPTION", "DURATION", "WARRANTY", "PRICE", "STOCK"]
            ],
        },
        _structure(grid),
    )
    before = [list(row) for row in book.values[TAB_MENU]]
    writer = CombinedQuoteWriter(book, "last-id")
    with pytest.raises(MenuLayoutError) as caught:
        writer.update(shop_quote_rows(_products()), dry_run=False)
    assert "không còn hàng" in str(caught.value).lower()
    assert book.values[TAB_MENU] == before
    assert book.batches == []


def test_writer_refuses_to_overwrite_formula_cell() -> None:
    book = _structured_book(
        _structure(formulas=[{"cell": "F8", "formula": "=VLOOKUP(B8,REF!A:B,2,FALSE)"}])
    )
    baseline = menu_baseline_from_structure(book.structure, MENU_HEADERS)
    guard = MenuLayoutGuard.from_baseline(baseline)
    before = [list(row) for row in book.values[TAB_MENU]]
    with pytest.raises(MenuLayoutError):
        CombinedQuoteWriter(book, "last-id", layout_guard=guard).update(
            shop_quote_rows(_products()[:1]),
            dry_run=False,
        )
    assert book.values[TAB_MENU] == before
    assert book.batches == []


def test_normal_sync_keeps_policy_headers_and_merges() -> None:
    book = _structured_book(_structure(merges=_merge_shop_header()))
    book.merges[TAB_MENU] = _merge_shop_header()
    headers_before = list(book.values[TAB_MENU][5])
    policy_before = _policy_snapshot(book.values[TAB_MENU])
    result = CombinedQuoteWriter(book, "last-id").update(
        shop_quote_rows(_products()[:1]),
        dry_run=False,
    )
    assert result.menu_count == 1
    assert book.values[TAB_MENU][5] == headers_before
    assert _policy_snapshot(book.values[TAB_MENU]) == policy_before
    assert book.merges[TAB_MENU] == _merge_shop_header()
    assert _menu_format_requests(book) == []
    menu_ranges = [
        item
        for batch in book.batches
        for item, _matrix in batch[2]
        if item.startswith(f"'{TAB_MENU}'!")
    ]
    assert menu_ranges
    for range_a1 in menu_ranges:
        assert range_a1.rsplit("!", 1)[1][0:1] in {"A", "B", "C", "D", "E", "F", "G", "H"}


def test_writer_aborts_when_header_order_drifts() -> None:
    grid = [list(row) for row in _menu_storefront()]
    grid[5] = ["ID", "PRODUCT", "DESCRIPTION", "DURATION", "WARRANTY", "PRICE", "SỐ LƯỢNG", "STATUS"]
    book = StructuredBook(
        [TAB_MENU, "NASTELEID", "BAO_GIA", "BAO_GIA_TONG_HOP"],
        {
            TAB_MENU: grid,
            "NASTELEID": [
                ["ID", "PRODUCT", "DESCRIPTION", "DURATION", "WARRANTY", "PRICE", "STOCK"]
            ],
        },
        _structure(grid),
    )
    before = [list(row) for row in book.values[TAB_MENU]]
    with pytest.raises(MenuLayoutError):
        CombinedQuoteWriter(book, "last-id").update([], dry_run=False)
    assert book.values[TAB_MENU] == before
    assert book.batches == []


def test_structure_diff_detects_merge_formula_width_and_header_drift() -> None:
    validations = [
        {
            "cell": f"H{row}",
            "type": "ONE_OF_LIST",
            "values": ["IN STOCK", "SOLD OUT"],
        }
        for row in (7, 8, 9)
    ]
    baseline = menu_baseline_from_structure(
        _structure(
            merges=_merge_shop_header(),
            formulas=[{"cell": "F8", "formula": "=SUM(F7)"}],
            validations=validations,
        ),
        MENU_HEADERS,
    )
    same = menu_baseline_from_structure(
        _structure(
            merges=_merge_shop_header(),
            formulas=[{"cell": "F8", "formula": "=SUM(F7)"}],
            validations=validations,
        ),
        MENU_HEADERS,
    )
    assert menu_structure_diff(baseline, same) == []

    drifted_grid = [list(row) for row in _menu_storefront()]
    drifted_grid[5] = [
        "ID",
        "PRODUCT",
        "MÔ TẢ",
        "DURATION",
        "WARRANTY",
        "PRICE",
        "STOCK",
        "STATUS",
    ]
    drifted = menu_baseline_from_structure(
        _structure(
            grid=drifted_grid,
            merges=[],
            formulas=[{"cell": "F8", "formula": "=SUM(F7:F8)"}],
            widths={"A": 99},
        ),
        MENU_HEADERS,
    )
    differences = menu_structure_diff(baseline, drifted)
    joined = " ".join(differences)
    assert "Dòng tiêu đề" in joined
    assert "Vùng gộp" in joined
    assert "Công thức" in joined
    assert "Chiều rộng cột A" in joined
    assert "Dropdown" in joined


def test_source_validation_rejects_bad_rows_before_any_write() -> None:
    problems = validate_menu_rows(
        [{"PRODUCT": "", "PRICE": 0, "STOCK": -1}],
        source="Shop Xoài Non",
    )
    assert any("PRODUCT trống" in item for item in problems)
    assert any("PRICE" in item for item in problems)
    assert any("STOCK" in item for item in problems)
    assert (
        validate_menu_rows(
            [{"PRODUCT": "A", "PRICE": 1000, "STOCK": "Còn hàng"}],
            source="Shop Xoài Non",
        )
        == []
    )
    duplicates = validate_menu_rows(
        [
            {"PRODUCT": "A", "PRICE": 1000, "STOCK": 0},
            {"PRODUCT": "a", "PRICE": 1000, "STOCK": 0},
        ],
        source="NASTELEID",
    )
    assert any("trùng" in item for item in duplicates)
    with pytest.raises(MenuLayoutError):
        assert_menu_rows_valid(
            [{"PRODUCT": "A", "PRICE": "", "STOCK": 0}],
            source="Shop Xoài Non",
        )

    book = _structured_book()
    with pytest.raises(MenuLayoutError):
        CombinedQuoteWriter(book, "last-id").update(
            [
                {
                    "PRODUCT": "Hàng lỗi",
                    "Nguồn": "Shop Xoài Non",
                    "PRICE": 0,
                    "STOCK": 0,
                }
            ],
            dry_run=False,
        )
    assert book.batches == []



def test_read_menu_baseline_needs_structure_gateway() -> None:
    with pytest.raises(MenuLayoutError):
        read_menu_baseline(object(), "last-id", MENU_HEADERS)


def test_menu_sync_reads_structure_and_keeps_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MENU_BASELINE_FILE", raising=False)
    book = _structured_book(_structure(merges=_merge_shop_header()))
    policy_before = _policy_snapshot(book.values[TAB_MENU])
    headers_before = list(book.values[TAB_MENU][5])
    result = run_menu_sync(
        make_settings(google_spreadsheet_id="last-id"),
        live=True,
        gateway=book,
        shop_client=StockShop(),
    )
    assert result.status == "ok"
    assert result.cells_written > 0
    assert any("MENU layout:" in note for note in result.notes)
    assert _policy_snapshot(book.values[TAB_MENU]) == policy_before
    assert book.values[TAB_MENU][5] == headers_before
    assert _menu_format_requests(book) == []


def test_menu_sync_aborts_on_baseline_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = menu_baseline_from_structure(_structure(), MENU_HEADERS)
    target = save_menu_baseline(baseline, tmp_path / "menu-baseline.json")
    monkeypatch.setenv("MENU_BASELINE_FILE", str(target))
    book = _structured_book(_structure(merges=_merge_shop_header()))
    before = [list(row) for row in book.values[TAB_MENU]]
    result = run_menu_sync(
        make_settings(google_spreadsheet_id="last-id"),
        live=True,
        gateway=book,
        shop_client=StockShop(),
    )
    assert result.status == "error"
    assert result.cells_written == 0
    assert any("lệch baseline" in error for error in result.errors)
    assert book.values[TAB_MENU] == before
    assert book.batches == []
