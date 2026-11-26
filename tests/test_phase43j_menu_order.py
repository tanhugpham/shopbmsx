"""Hồi quy Phase 4.3J: gom sản phẩm cùng loại về cạnh nhau trên MENU.

Chỉ đổi thứ tự dòng A:H. Không thêm cột/sheet, không sửa dữ liệu, không ghi J:M.
Mọi test dùng gateway giả, không gọi Google Sheets.
"""

from __future__ import annotations

from typing import Any

import pytest

from shop_integration.combined_quote import MENU_HEADERS, TAB_MENU
from shop_integration.errors import MenuLayoutError
from shop_integration.menu_layout import MenuCapacity, cell_name
from shop_integration.menu_order import (
    GROUP_SIMILAR_ENV,
    apply_menu_order,
    cluster_menu_rows,
    fold_menu_text,
    may_group_similar,
    menu_family,
    menu_order_cells,
    menu_order_report,
    menu_row_values,
    plan_menu_order,
)
from shop_integration.menu_sync import _group_similar_menu_rows
from tests.test_combined_quote import QuoteMemory
from tests.test_phase43b_menu_layout import StructuredBook

_HEADER_ROW = 6  # dòng tiêu đề, sản phẩm bắt đầu từ dòng 8

# Thứ tự gốc cố tình xen kẽ loại sản phẩm.
_NAMES = [
    "ChatGPT Plus 1 tháng riêng tư - BHF",
    "Canva Pro 1 năm chính chủ (BHF)",
    "Google Drive chính chủ 100GB/năm (BHF)",
    "Capcut Pro 30d full Date - BHF",
    "Chatgpt Plus pay Apple Gmail 1 thg riêng tư - BH 24H",
    "Tài khoản Canva Pro 1 tháng",
    "Google Drive chính chủ 400GB/năm (BHF)",
    "Capcut Pro 7 ngày 500 Credit BH6D",
    "ChatGPT Plus 1 tháng riêng tư mail Icloud UPI- KBH",
]
_FIRST_ROW = 8
_LAST_ROW = _FIRST_ROW + len(_NAMES) - 1  # 16
_PRODUCT_ROWS = list(range(_FIRST_ROW, _LAST_ROW + 1))

# Dòng 8..16 nhận nguyên bộ A:H theo thứ tự mới.
_EXPECTED_ORDER = (8, 12, 16, 9, 13, 10, 14, 11, 15)


def _blank_row() -> list[Any]:
    return [""] * 13


def _product_row(index: int, name: str, *, row_number: int) -> list[Any]:
    return [
        f"SXN-{index:02d}",
        name,
        f"Mô tả {index}",
        "1 Tháng",
        "BHF",
        100000 + index,
        index,
        "IN STOCK" if index % 2 else "SOLD OUT",
        "",
        f"panel {row_number}",
        "",
        "",
        "",
    ]


def _grid(names: list[str] | None = None, *, total_rows: int = 40) -> list[list[Any]]:
    grid = [_blank_row() for _ in range(total_rows)]
    grid[_HEADER_ROW - 1] = list(MENU_HEADERS) + ["", "📋 CÁC GÓI BẢO HÀNH"]
    for offset, name in enumerate(names or _NAMES):
        row_number = _FIRST_ROW + offset
        grid[row_number - 1] = _product_row(offset + 1, name, row_number=row_number)
    return grid


def _payloads(grid: list[list[Any]], rows: list[int]) -> dict[str, tuple[Any, ...]]:
    """ID -> (tên, mô tả, giá, tồn kho, trạng thái) để so trước/sau khi sắp xếp."""

    return {
        str(grid[row - 1][0]): (
            grid[row - 1][1],
            grid[row - 1][2],
            grid[row - 1][5],
            grid[row - 1][6],
            grid[row - 1][7],
        )
        for row in rows
    }


def _panel(grid: list[list[Any]]) -> list[list[Any]]:
    """Ảnh chụp J:M để chắc chắn không ô nào bị đụng."""

    return [
        [str(row[column]) if column < len(row) else "" for column in range(9, 13)]
        for row in grid
    ]


def _apply_to_grid(grid: list[list[Any]], plan: Any) -> list[list[Any]]:
    """Mô phỏng việc ghi: chỉ thay A:H, giữ nguyên J:M của dòng đích."""

    updated = [list(row) for row in grid]
    for slot, source in zip(plan.slots, plan.order):
        panel = [
            updated[slot - 1][column] if column < len(updated[slot - 1]) else ""
            for column in range(8, 13)
        ]
        updated[slot - 1] = menu_row_values(grid, source) + panel
    return updated


def test_fold_menu_text_bỏ_dấu_và_ký_tự_lạ() -> None:
    assert fold_menu_text("  Canva Pro 1 Năm – chính chủ (BHF) ") == "canva pro 1 nam chinh chu bhf"
    assert fold_menu_text("Tài khoản MUSE AI 30 TỶ TOKEN KBH") == (
        "tai khoan muse ai 30 ty token kbh"
    )
    assert fold_menu_text(None) == ""


@pytest.mark.parametrize(
    ("name", "family"),
    [
        ("ChatGPT Plus 1 tháng riêng tư - BHF", "chatgpt"),
        ("Chatgpt Plus pay Apple Gmail 1 thg riêng tư - BH 24H", "chatgpt"),
        ("CHAT GPT Plus dùng chung", "chatgpt"),
        ("Tài khoản Canva Pro 1 tháng", "canva"),
        ("Canva Pro 1 năm chính chủ (BHF)", "canva"),
        ("Google Drive chính chủ 100GB/năm (BHF)", "google drive"),
        ("Tài khoản MUSE AI 2 TỶ TOKEN KBH", "muse"),
        ("Buff 1B Muse chính chủ", "muse"),
        ("Gói Gemini AI Plus & Drive 400GB chính chủ", "gemini"),
        ("Grok Premium+ Cursor 14 - 24 ngày (BH 1D)", "grok"),
        ("Adobe All Apps 1 Tháng riêng tư - BH7D", "adobe"),
        ("Wink VIP+ riêng tư 7 Days", "wink"),
        ("AI PRO", "~ai pro"),
        ("", "~"),
    ],
)
def test_menu_family_nhận_đúng_loại_sản_phẩm(name: str, family: str) -> None:
    assert menu_family(name) == family


def test_cluster_giữ_thứ_tự_xuất_hiện_đầu_tiên() -> None:
    grid = _grid()

    clusters = cluster_menu_rows(_PRODUCT_ROWS, grid)

    assert [cluster.family for cluster in clusters] == [
        "chatgpt",
        "canva",
        "google drive",
        "capcut",
    ]
    assert [cluster.rows for cluster in clusters] == [(8, 12, 16), (9, 13), (10, 14), (11, 15)]
    assert [cluster.size for cluster in clusters] == [3, 2, 2, 2]
    assert clusters[0].title == _NAMES[0]


def test_plan_đưa_sản_phẩm_cùng_loại_về_cạnh_nhau() -> None:
    grid = _grid()

    plan = plan_menu_order(grid)

    assert plan.slots == tuple(_PRODUCT_ROWS)
    assert plan.order == _EXPECTED_ORDER
    assert plan.moved == 7  # dòng 8 và 14 đã nằm đúng chỗ
    assert plan.identity is False
    assert (plan.first_row, plan.last_row, plan.size) == (_FIRST_ROW, _LAST_ROW, len(_NAMES))
    assert sorted(plan.order) == sorted(plan.slots)  # không mất, không trùng dòng
    assert "gom cụm" in plan.summary()


def test_plan_không_đổi_gì_khi_đã_đúng_cụm() -> None:
    grid = _apply_to_grid(_grid(), plan_menu_order(_grid()))

    plan = plan_menu_order(grid)

    assert plan.identity is True
    assert plan.moved == 0
    assert "không đổi vị trí" in plan.summary()


def test_bỏ_dòng_có_công_thức_khỏi_vùng_sắp_xếp() -> None:
    grid = _grid()
    grid[12][2] = '=HYPERLINK("x")'  # dòng 13 có công thức trong A:H

    plan = plan_menu_order(grid)

    assert 13 not in plan.slots
    assert any("Bỏ 1 dòng" in note for note in plan.notes)
    assert sorted(plan.order) == sorted(plan.slots)


def test_bỏ_dòng_bị_khóa_bởi_capacity() -> None:
    grid = _grid()
    capacity = MenuCapacity(blocked_cells=frozenset({cell_name(2, 13)}))

    plan = plan_menu_order(grid, capacity=capacity)

    assert 13 not in plan.slots
    assert len(plan.slots) == len(_NAMES) - 1


def test_không_có_tiêu_đề_thì_không_sắp_xếp() -> None:
    plan = plan_menu_order([["", ""], ["", ""]])

    assert plan.identity is True
    assert plan.slots == ()
    assert plan.notes and "tiêu đề" in plan.notes[0]


def test_cells_chỉ_gồm_ô_a_đến_h_của_dòng_đích() -> None:
    grid = _grid()

    cells = menu_order_cells(plan_menu_order(grid), grid)

    assert len(cells) == 7 * 8
    for range_a1, _value in cells:
        tab, cell = range_a1.split("!", 1)
        assert tab == f"'{TAB_MENU}'"
        assert cell[0] in "ABCDEFGH"
        assert cell[1:].isdigit()
    touched = {item.split("!", 1)[1] for item, _value in cells}
    changed_rows = [9, 10, 11, 12, 13, 15, 16]  # dòng 14 đã đúng chỗ nên không ghi
    assert touched == {f"{column}{row}" for column in "ABCDEFGH" for row in changed_rows}


def test_apply_giữ_nguyên_id_giá_và_tồn_kho_theo_từng_sản_phẩm() -> None:
    grid = _grid()
    book = QuoteMemory([TAB_MENU], {TAB_MENU: grid})
    before_payloads = _payloads(grid, _PRODUCT_ROWS)
    before_panel = _panel(grid)

    written = apply_menu_order(book, "SID", plan_menu_order(grid), grid=grid)

    assert written == 7 * 8
    after = book.values[TAB_MENU]
    assert _panel(after) == before_panel  # J:M không bị đụng
    assert _payloads(after, _PRODUCT_ROWS) == before_payloads
    # Dòng 9 nhận nguyên bộ A:H của dòng 12 (ChatGPT thứ 2), J:M giữ nguyên.
    assert after[8][:8] == [
        "SXN-05",
        "Chatgpt Plus pay Apple Gmail 1 thg riêng tư - BH 24H",
        "Mô tả 5",
        "1 Tháng",
        "BHF",
        100005,
        5,
        "IN STOCK",
    ]


def test_report_phát_hiện_dòng_bị_lệch_nội_dung() -> None:
    grid = _grid()
    plan = plan_menu_order(grid)
    tampered = _apply_to_grid(grid, plan)
    tampered[8][1] = "Tên khác"

    problems = menu_order_report(plan, grid, tampered)

    assert problems and "không khớp" in problems[0]


def test_apply_dừng_và_báo_lỗi_khi_đọc_lại_không_khớp() -> None:
    grid = _grid()

    class DroppingBook(QuoteMemory):
        """Ghi thiếu một ô để mô phỏng Sheets trả về lệch."""

        def batch_update_values(
            self,
            spreadsheet_id: str,
            items: list[tuple[str, list[list[Any]]]],
            *,
            value_input_option: str = "RAW",
        ) -> None:
            super().batch_update_values(
                spreadsheet_id, items[:-1], value_input_option=value_input_option
            )

    book = DroppingBook([TAB_MENU], {TAB_MENU: grid})

    with pytest.raises(MenuLayoutError) as caught:
        apply_menu_order(book, "SID", plan_menu_order(grid), grid=grid)

    assert "kiểm tra lại không khớp" in str(caught.value)
    assert "backup" in str(caught.value).lower()


def test_apply_không_ghi_khi_đã_đúng_thứ_tự() -> None:
    grid = _apply_to_grid(_grid(), plan_menu_order(_grid()))
    book = QuoteMemory([TAB_MENU], {TAB_MENU: grid})

    assert apply_menu_order(book, "SID", plan_menu_order(grid), grid=grid) == 0
    assert book.batches == []


def test_tắt_bằng_env_thì_không_gom_cụm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GROUP_SIMILAR_ENV, "false")


def _two_region_grid() -> list[list[Any]]:
    """Hai danh mục: sản phẩm chỉ gom cụm trong danh mục của nó."""

    grid = _grid(total_rows=20)
    for row_number in _PRODUCT_ROWS:
        grid[row_number - 1] = _blank_row()
    grid[7] = _blank_row()
    grid[7][0] = "🤖 AI TOOLS"
    for offset, name in enumerate(
        [
            "ChatGPT Plus 1 tháng riêng tư - BHF",
            "Wink VIP+ riêng tư 7 Days",
            "ChatGPT Plus mail Icloud - KBH",
        ]
    ):
        grid[8 + offset] = _product_row(offset + 1, name, row_number=9 + offset)
    grid[12] = _blank_row()
    grid[12][0] = "🎬 GIẢI TRÍ"
    for offset, name in enumerate(["Capcut Pro 30d full Date - BHF", "Capcut Pro 7 ngày 500 Credit"]):
        grid[13 + offset] = _product_row(10 + offset, name, row_number=14 + offset)
    return grid


def test_mỗi_danh_mục_gom_cụm_riêng() -> None:
    grid = _two_region_grid()

    plan = plan_menu_order(grid)

    assert plan.slots == (9, 10, 11, 14, 15)
    assert plan.order == (9, 11, 10, 14, 15)  # ChatGPT, ChatGPT, Wink | Capcut, Capcut
    assert plan.moved == 2
    assert [cluster.family for cluster in plan.clusters] == ["chatgpt", "capcut"]
    assert plan.clusters[0].rows == (9, 11)
    assert "×2" in plan.summary()


def _structure(grid: list[list[Any]]) -> dict[str, Any]:
    return {
        "title": TAB_MENU,
        "sheet_id": 1,
        "grid_properties": {"rowCount": 60, "columnCount": 13, "frozenRowCount": 6},
        "conditional_format_count": 0,
        "merges": [],
        "values": [list(row) for row in grid],
        "formulas": [],
        "data_validations": [],
        "column_widths": {},
        "row_heights": {},
    }


def _book(grid: list[list[Any]]) -> StructuredBook:
    return StructuredBook([TAB_MENU], {TAB_MENU: [list(row) for row in grid]}, _structure(grid))


def test_sync_hook_chạy_thử_không_ghi_gì() -> None:
    book = _book(_grid())
    notes: list[str] = []
    errors: list[str] = []

    written = _group_similar_menu_rows(book, "SID", live=False, notes=notes, errors=errors)

    assert written == 0
    assert errors == []
    assert book.batches == []
    assert any("Chạy thử chưa ghi" in note for note in notes)


def test_sync_hook_ghi_khi_live_và_chạy_lại_không_ghi() -> None:
    grid = _grid()
    book = _book(grid)
    notes: list[str] = []
    errors: list[str] = []

    written = _group_similar_menu_rows(book, "SID", live=True, notes=notes, errors=errors)

    assert errors == []
    assert written == 7 * 8
    assert any("gom cụm" in note for note in notes)
    assert _payloads(book.values[TAB_MENU], _PRODUCT_ROWS) == _payloads(grid, _PRODUCT_ROWS)
    assert _panel(book.values[TAB_MENU]) == _panel(grid)

    book.batches.clear()
    notes.clear()
    assert _group_similar_menu_rows(book, "SID", live=True, notes=notes, errors=errors) == 0
    assert book.batches == []
    assert any("không đổi vị trí" in note for note in notes)


def test_sync_hook_im_lặng_khi_env_tắt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GROUP_SIMILAR_ENV, "false")
    book = _book(_grid())
    notes: list[str] = []
    errors: list[str] = []

    assert _group_similar_menu_rows(book, "SID", live=True, notes=notes, errors=errors) == 0
    assert book.batches == []
    assert any("MENU_GROUP_SIMILAR=false" in note for note in notes)


    assert may_group_similar() is False


def test_mặc_định_bật_gom_cụm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(GROUP_SIMILAR_ENV, raising=False)

    assert may_group_similar() is True
