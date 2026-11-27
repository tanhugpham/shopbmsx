"""Test hotfix khôi phục layout MENU sau khi bị sửa tay (2026-11-27).

Fixture dựng lại đúng trạng thái MENU bị lệch: dòng tiêu đề nhân bản ở dòng 8, mất
dropdown K38 cùng công thức J39/J40, panel J:M bị cắt, footer dời xuống và khổ cột
bị auto-fit. Test chỉ dùng gateway giả, không gọi Google.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

import pytest

from shop_integration.combined_quote import (
    MENU_COLUMNS,
    MENU_HEADERS,
    is_menu_header_row,
    is_menu_product_row,
)
from shop_integration.config import PROJECT_ROOT
from shop_integration.errors import MenuLayoutError
from shop_integration.menu_expand import menu_expansion_shift, shift_menu_baseline
from shop_integration.menu_layout import (
    load_menu_baseline,
    menu_baseline_from_structure,
    menu_structure_diff,
    normalize_protected_ranges,
)
from shop_integration.menu_repair import (
    apply_menu_restore,
    load_policy_panel,
    plan_menu_restore,
    verify_menu_restore,
)

BASELINE_FILE = PROJECT_ROOT / "data" / "menu-baseline.json"
PANEL_FILE = PROJECT_ROOT / "data" / "menu-policy-panel.json"
COLUMNS = 13

# Merge thật đọc từ MENU lúc 09:43 ngày 2026-11-27: 54 vùng gộp, thiếu khối chọn.
LIVE_MERGES = (
    "A2:H2", "J2:M2", "A3:H3", "J3:M3", "A4:H4", "J4:M4", "A5:F5", "G5:H5",
    "J5:M5", "J6:M6", "K8:M8", "K9:M9", "J10:J11", "K10:M10", "K11:M11",
    "J12:J13", "K12:M12", "K13:M13", "J14:J15", "K14:M14", "K15:M15", "J16:M16",
    "J17:M17", "J18:M18", "J19:M19", "J20:M20", "J21:K21", "L21:M21", "J22:M22",
    "J23:M23", "J24:M24", "J25:M25", "J26:K26", "L26:M26", "J27:M27", "J28:M28",
    "J29:M29", "J30:M30", "J31:M31", "J32:M32", "A57:H57", "A58:H58", "A59:H59",
    "A60:H60", "A61:B61", "C61:D61", "E61:F61", "G61:H61", "A62:B62", "C62:D62",
    "E62:F62", "G62:H62", "A63:H63", "A64:H64",
)


def _column_index(column: str) -> int:
    index = 0
    for letter in column:
        index = index * 26 + (ord(letter.upper()) - 64)
    return index - 1


def _merge(range_a1: str) -> dict[str, int]:
    start, end = range_a1.split(":")
    start_match = re.match(r"^([A-Z]+)(\d+)$", start)
    end_match = re.match(r"^([A-Z]+)(\d+)$", end)
    assert start_match and end_match
    return {
        "startRowIndex": int(start_match.group(2)) - 1,
        "endRowIndex": int(end_match.group(2)),
        "startColumnIndex": _column_index(start_match.group(1)),
        "endColumnIndex": _column_index(end_match.group(1)) + 1,
    }


def _grid(range_dict: dict[str, Any]) -> dict[str, int]:
    return {
        key: int(range_dict[key])
        for key in ("startRowIndex", "endRowIndex", "startColumnIndex", "endColumnIndex")
    }


def _corrupt_values() -> list[list[str]]:
    """Giá trị MENU đúng như lúc bị sửa tay: 35 sản phẩm từ dòng 9, footer từ 57."""

    values = [[""] * COLUMNS for _ in range(70)]
    values[4][6] = "=NOW()"  # G5
    for column, header in enumerate(MENU_HEADERS):
        values[5][column] = header
        values[7][column] = header  # dòng tiêu đề nhân bản ở dòng 8
    for order in range(35):
        values[8 + order] = [
            f"SXN-{order + 1:02d}",
            f"Sản phẩm {order + 1}",
            f"Mô tả {order + 1}",
            "1 Tháng",
            "BHF",
            100000 + order,
            order,
            "IN STOCK" if order % 2 else "SOLD OUT",
        ] + [""] * (COLUMNS - 8)
    for row in range(16, 33):
        values[row - 1][9] = f"Panel cũ dòng {row}"
    for row in range(57, 65):
        values[row - 1][0] = f"Footer {row}"
    return values


class FakeMenuBook:
    """Gateway giả: giữ giá trị + cấu trúc và áp request giống Google Sheets."""

    def __init__(
        self,
        values: Sequence[Sequence[Any]],
        merges: Sequence[dict[str, int]],
        formulas: Sequence[dict[str, str]],
        widths: dict[str, int],
        protected: Sequence[dict[str, Any]],
        *,
        conditional_format_count: int = 8,
    ) -> None:
        self.values = [list(row) + [""] * (COLUMNS - len(row)) for row in values]
        self.merges = [dict(item) for item in merges]
        self.formulas = [dict(item) for item in formulas]
        self.validations: list[dict[str, Any]] = []
        self.widths = dict(widths)
        self.protected = [dict(item) for item in protected]
        self.conditional_format_count = int(conditional_format_count)

        self.batches = 0
        self.value_batches = 0

    def get_sheet_structure(self, spreadsheet_id: str, title: str) -> dict[str, Any]:
        del spreadsheet_id
        values = [list(row) for row in self.values]
        while values and not any(str(cell).strip() for cell in values[-1]):
            values.pop()
        return {
            "title": title,
            "sheet_id": 0,
            "grid_properties": {
                "rowCount": 1000,
                "columnCount": 26,
                "frozenRowCount": 6,
            },
            "conditional_format_count": self.conditional_format_count,
            "conditional_formats": [],
            "merges": [dict(item) for item in self.merges],
            "protected_ranges": normalize_protected_ranges(self.protected),
            "protected_ranges_raw": [dict(item) for item in self.protected],
            "values": values,
            "formulas": [dict(item) for item in self.formulas],
            "data_validations": [dict(item) for item in self.validations],
            "column_widths": dict(self.widths),
            "row_heights": {},
        }

    def batch_update(self, spreadsheet_id: str, requests: list[dict[str, Any]]) -> None:
        del spreadsheet_id
        self.batches += 1
        for request in requests:
            (kind,) = request
            if kind == "deleteDimension":
                self._delete_row(int(request[kind]["range"]["startIndex"]))
            elif kind == "unmergeCells":
                self._drop_merge(request[kind]["range"])
            elif kind == "mergeCells":
                self.merges.append(_grid(request[kind]["range"]))
            elif kind == "setDataValidation":
                self._set_validation(request[kind])
            elif kind == "updateCells":
                self._set_formula(request[kind])
            elif kind == "updateProtectedRange":
                self._set_protected(request[kind]["protectedRange"])
            elif kind == "updateDimensionProperties":
                self._set_width(request[kind])
            else:  # pragma: no cover - request lạ phải lộ ra ngay
                raise AssertionError(f"Request chưa hỗ trợ: {kind}")

    def batch_update_values(
        self,
        spreadsheet_id: str,
        items: list[tuple[str, list[list[Any]]]],
        *,
        value_input_option: str = "RAW",
    ) -> None:
        del spreadsheet_id, value_input_option
        self.value_batches += 1
        for range_a1, matrix in items:
            self._write_range(range_a1, matrix)

    def _drop_merge(self, range_dict: dict[str, Any]) -> None:
        wanted = _grid(range_dict)
        self.merges = [item for item in self.merges if _grid(item) != wanted]

    def _delete_row(self, index: int) -> None:
        del self.values[index]
        for merge in self.merges:
            self._shift_rows(merge, index)
        for item in self.protected:
            self._shift_rows(item["range"], index)
        self.formulas = [item for item in self.formulas if self._shift_cell(item, index)]
        self.validations = [item for item in self.validations if self._shift_cell(item, index)]

    @staticmethod
    def _shift_rows(item: dict[str, Any], index: int) -> None:
        start = int(item["startRowIndex"])
        end = int(item["endRowIndex"])
        if end <= index:
            return
        if start >= index:
            item["startRowIndex"] = start - 1
        item["endRowIndex"] = end - 1

    @staticmethod
    def _shift_cell(item: dict[str, Any], index: int) -> bool:
        match = re.match(r"^([A-Z]+)(\d+)$", str(item.get("cell") or ""))
        if match is None:
            return True
        row = int(match.group(2))
        if row - 1 < index:
            return True
        if row - 1 == index:
            return False
        item["cell"] = f"{match.group(1)}{row - 1}"
        return True

    @staticmethod
    def _cell_name(range_dict: dict[str, Any]) -> str:
        column = int(range_dict["startColumnIndex"])
        row = int(range_dict["startRowIndex"]) + 1
        return f"{chr(65 + column)}{row}"

    def _set_validation(self, payload: dict[str, Any]) -> None:
        cell = self._cell_name(payload["range"])
        condition = payload["rule"]["condition"]
        self.validations = [item for item in self.validations if item["cell"] != cell]
        self.validations.append(
            {
                "cell": cell,
                "type": str(condition.get("type")),
                "values": list(condition.get("values") or []),
            }
        )

    def _set_formula(self, payload: dict[str, Any]) -> None:
        cell = self._cell_name(payload["range"])
        formula = payload["rows"][0]["values"][0]["userEnteredValue"]["formulaValue"]
        self.formulas = [item for item in self.formulas if item["cell"] != cell]
        self.formulas.append({"cell": cell, "formula": formula})

    def _set_protected(self, payload: dict[str, Any]) -> None:
        target = int(payload["protectedRangeId"])
        for item in self.protected:
            if int(item.get("protectedRangeId") or 0) == target:
                item["range"] = dict(payload["range"])
                item["warningOnly"] = bool(payload.get("warningOnly", True))

    def _set_width(self, payload: dict[str, Any]) -> None:
        index = int(payload["range"]["startIndex"])
        self.widths[chr(65 + index)] = int(payload["properties"]["pixelSize"])

    def _write_range(self, range_a1: str, matrix: Sequence[Sequence[Any]]) -> None:
        match = re.match(r"^'?MENU'?!([A-Z]+)(\d+):([A-Z]+)(\d+)$", range_a1)
        assert match, range_a1
        first_column = _column_index(match.group(1))
        first_row = int(match.group(2)) - 1
        columns = _column_index(match.group(3)) - first_column + 1
        for row_offset, line in enumerate(matrix):
            for column_offset in range(min(columns, len(line))):
                self.values[first_row + row_offset][first_column + column_offset] = line[
                    column_offset
                ]


def _book() -> tuple[FakeMenuBook, Any]:
    baseline = load_menu_baseline(BASELINE_FILE)
    widths = dict(baseline.column_widths)
    widths["A"] = 83
    widths["B"] = 300
    protected = [
        {
            "protectedRangeId": 11,
            "range": {
                "startRowIndex": 0,
                "endRowIndex": 6,
                "startColumnIndex": 0,
                "endColumnIndex": 8,
            },
            "warningOnly": True,
        },
        {
            "protectedRangeId": 12,
            "range": {
                "startRowIndex": 0,
                "endRowIndex": 66,
                "startColumnIndex": 8,
                "endColumnIndex": 13,
            },
            "warningOnly": True,
        },
    ]
    book = FakeMenuBook(
        values=_corrupt_values(),
        merges=[_merge(item) for item in LIVE_MERGES],
        formulas=[{"cell": "G5", "formula": "=NOW()"}],
        widths=widths,
        protected=protected,
        conditional_format_count=baseline.conditional_format_count,
    )
    return book, baseline


def test_panel_archive_giu_nguyen_noi_dung_chinh_sach() -> None:
    panel = load_policy_panel(PANEL_FILE)
    assert panel.first_row == 7
    assert panel.line(7)[0] == "BHF"
    assert panel.line(16)[0].startswith("💰 BH REFUND")
    assert panel.line(35)[0].startswith("🔒 DIGITAL STORE")
    assert panel.line(37)[0].startswith("📋 CHI TIẾT")


def test_duplicate_header_khong_phai_dong_san_pham() -> None:
    header = list(MENU_HEADERS)
    assert is_menu_header_row(header) is True
    assert is_menu_product_row(header) is False
    product = ["SXN-01", "Tên", "Mô tả", "1 Tháng", "BHF", 1000, 5, "IN STOCK"]
    assert is_menu_header_row(product) is False
    assert is_menu_product_row(product) is True


def test_structure_diff_bao_dong_tieu_de_nhan_ban() -> None:
    book, baseline = _book()
    current = menu_baseline_from_structure(
        book.get_sheet_structure("id", "MENU"), MENU_HEADERS
    )
    assert current.duplicate_header_rows == (8,)
    joined = " ".join(menu_structure_diff(baseline, current))
    assert "nhân bản" in joined
    assert "Vùng gộp" in joined


def test_formula_key_chuan_hoa_o_chon_k() -> None:
    from shop_integration.menu_layout import _formula_key

    first = {"cell": "J39", "formula": '=VLOOKUP(K38; B8:H35; 3; FALSE)'}
    second = {"cell": "J39", "formula": '=VLOOKUP(K45; B8:H42; 3; FALSE)'}
    assert _formula_key(first)[0] == _formula_key(second)[0]
    assert _formula_key(first)[1] != _formula_key(second)[1]


def test_shift_baseline_doi_o_chon_sang_k45() -> None:
    baseline = load_menu_baseline(BASELINE_FILE)
    shifted = shift_menu_baseline(
        baseline,
        insert_index=35,
        rows=7,
        old_selector_start_row=8,
        old_selector_end_row=35,
        new_selector_end_row=42,
    )
    formulas = {item["cell"]: item["formula"] for item in shifted.formulas}
    assert "K45" in formulas["J46"]
    assert "K38" not in formulas["J46"]
    assert "K45" in formulas["J47"]
    assert "B8:H42" in formulas["J46"]


def test_plan_khoi_phuc_dung_vung_chon_va_panel() -> None:
    book, baseline = _book()
    panel = load_policy_panel(PANEL_FILE)
    plan = plan_menu_restore(book.get_sheet_structure("id", "MENU"), baseline, panel)
    assert plan.problems == ()
    assert plan.duplicate_rows == (8,)
    assert plan.product_count == 35
    assert plan.rows_shift == 7
    assert (plan.zone_first, plan.zone_last_before, plan.zone_last_after) == (8, 35, 42)
    assert (plan.footer_from, plan.footer_to) == (57, 53)
    spans = [range_a1 for range_a1, _matrix in plan.value_writes]
    assert "'MENU'!J7:M47" in spans
    assert "'MENU'!A53:H60" in spans
    assert "'MENU'!A61:H63" in spans
    assert len(plan.merge_requests) + 10 == 62
    assert len(plan.delete_requests) == 1


def test_apply_khoi_phuc_va_kiem_tra_lai_dat() -> None:
    book, baseline = _book()
    panel = load_policy_panel(PANEL_FILE)
    before = book.get_sheet_structure("id", "MENU")
    plan = plan_menu_restore(before, baseline, panel)
    counts = apply_menu_restore(book, "id", plan)
    assert counts["deleted_rows"] == 1
    after = book.get_sheet_structure("id", "MENU")
    assert verify_menu_restore(before, after, baseline, plan, panel) == []

    after_baseline = menu_baseline_from_structure(after, MENU_HEADERS)
    assert after_baseline.merge_count == 62
    assert after_baseline.duplicate_header_rows == ()
    assert menu_expansion_shift(baseline, after_baseline) == 7
    assert after["column_widths"]["A"] == 75
    assert after["column_widths"]["B"] == 230
    assert [item["cell"] for item in after["formulas"]] == ["G5", "J46", "J47"]
    panel_formulas = {item["cell"]: item["formula"] for item in after["formulas"]}
    assert "K45" in panel_formulas["J46"]
    assert "K45" in panel_formulas["J47"]
    assert "$B$8:$B$42" in str(after["data_validations"])
    assert any(
        int(item["startColumnIndex"]) == 8 and int(item["endRowIndex"]) == 62
        for item in after["protected_ranges"]
    )
    assert [row[0] for row in book.values[7:42]] == [f"SXN-{i:02d}" for i in range(1, 36)]
    assert book.values[43][9].startswith("📋 CHI TIẾT")
    assert book.values[44][9].startswith("🔍 Chọn SP")
    assert book.values[44][10] == "ChatGPT Plus 1 tháng riêng tư - BHF"
    assert book.values[59][0] == "Footer 64"
    assert book.values[62][0] == ""


def test_plan_chan_khi_san_pham_khong_lien_mach() -> None:
    book, baseline = _book()
    panel = load_policy_panel(PANEL_FILE)
    book.values[20] = [""] * COLUMNS  # xóa một sản phẩm giữa vùng
    plan = plan_menu_restore(book.get_sheet_structure("id", "MENU"), baseline, panel)
    assert plan.problems
    assert plan.delete_requests == ()


def test_apply_tu_choi_khi_ke_hoach_con_loi() -> None:
    book, baseline = _book()
    panel = load_policy_panel(PANEL_FILE)
    book.values[20] = [""] * COLUMNS
    plan = plan_menu_restore(book.get_sheet_structure("id", "MENU"), baseline, panel)
    with pytest.raises(MenuLayoutError):
        apply_menu_restore(book, "id", plan)

