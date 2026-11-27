"""Khôi phục layout MENU về baseline đã xác minh sau khi bị sửa ngoài pipeline.

Chỉ dùng khi ``sync-menu-preflight`` báo MENU lệch baseline vì bị chỉnh tay: dòng
tiêu đề bị nhân bản, panel chính sách J:M bị cắt, mất dropdown K38 cùng công thức
J39/J40, hoặc khổ cột bị auto-fit. Module lập kế hoạch trước (chỉ đọc), in rõ diff,
và chỉ ghi khi lệnh CLI có ``--apply --confirm``.

Nguyên tắc:

* dữ liệu sản phẩm A:H giữ nguyên từng ô, chỉ dời lên khi xóa dòng tiêu đề nhân bản;
* panel J:M dựng lại từ ``data/menu-policy-panel.json`` (bản lưu 2026-11-25);
* merge, công thức, dropdown, vùng bảo vệ, khổ cột lấy từ ``data/menu-baseline.json``;
* ghi xong phải đọc lại và đối chiếu; lệch thì trả mã 1 và giữ nguyên backup.

Module này không được gọi từ ``sync-menu``: lượt đồng bộ sản phẩm vẫn chỉ ghi A:H.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from shop_integration.combined_quote import MENU_COLUMNS, MENU_HEADERS, TAB_MENU
from shop_integration.errors import MenuLayoutError
from shop_integration.menu_expand import (
    menu_expansion_shift,
    menu_selector_span,
    shift_menu_baseline,
)
from shop_integration.menu_layout import (
    MENU_LAST_COLUMN,
    MENU_POLICY_COLUMN_START,
    MENU_PROTECTED_COLUMN_START,
    MenuBaseline,
    column_index,
    menu_baseline_from_structure,
)

DEFAULT_PANEL_FILE = Path("data") / "menu-policy-panel.json"
POLICY_COLUMNS = 4  # J..M
GRID_KEYS = ("startRowIndex", "startColumnIndex", "endRowIndex", "endColumnIndex")
_PRODUCT_ID = re.compile(r"^[A-Za-z]{2,6}-\d+$")


def _cell(row: Sequence[Any] | None, index: int) -> Any:
    raw = row or []
    return raw[index] if index < len(raw) else ""


def _text(row: Sequence[Any] | None, index: int) -> str:
    return str(_cell(row, index) or "").strip()


def _is_header_row(row: Sequence[Any] | None) -> bool:
    """Dòng lặp lại đúng tiêu đề A:H (dấu hiệu bị copy/paste trong Google Sheets)."""

    cells = [_text(row, column).upper() for column in range(MENU_COLUMNS)]
    return cells == [str(header).strip().upper() for header in MENU_HEADERS]


def _grid(values: Sequence[Sequence[Any]], row: int) -> list[Any]:
    index = row - 1
    if index < 0 or index >= len(values):
        return []
    return list(values[index])


def _product_rows(values: Sequence[Sequence[Any]], first: int, last: int) -> list[int]:
    found: list[int] = []
    for row in range(first, last + 1):
        if _PRODUCT_ID.match(_text(_grid(values, row), 0)):
            found.append(row)
    return found


@dataclass(frozen=True)
class PolicyPanel:
    """Panel chính sách J:M đã lưu, đánh số dòng 1-based như trên sheet."""

    first_row: int
    rows: dict[int, tuple[str, ...]]
    columns: int = POLICY_COLUMNS
    source: str = ""

    def max_row(self) -> int:
        return max(self.rows) if self.rows else self.first_row

    def line(self, row: int) -> tuple[str, ...]:
        cells = self.rows.get(row, ())
        return tuple(
            str(cells[index]) if index < len(cells) else ""
            for index in range(self.columns)
        )

    def matrix(self, first: int, last: int) -> list[list[str]]:
        return [list(self.line(row)) for row in range(first, last + 1)]


def load_policy_panel(path: str | Path = DEFAULT_PANEL_FILE) -> PolicyPanel:
    source = Path(path)
    if not source.is_file():
        raise MenuLayoutError(f"Thiếu file panel chính sách MENU: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MenuLayoutError(f"Không đọc được panel chính sách MENU: {exc}") from exc
    raw_rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(raw_rows, dict) or not raw_rows:
        raise MenuLayoutError("Panel chính sách MENU không có dòng nào.")
    rows: dict[int, tuple[str, ...]] = {}
    for key, cells in raw_rows.items():
        try:
            row_number = int(key)
        except (TypeError, ValueError) as exc:
            raise MenuLayoutError(f"Dòng panel không hợp lệ: {key!r}") from exc
        if not isinstance(cells, list):
            raise MenuLayoutError(f"Dòng panel {key} không phải danh sách ô.")
        rows[row_number] = tuple(str(cell) for cell in cells)
    first_row = int(payload.get("first_row") or min(rows))
    return PolicyPanel(
        first_row=first_row,
        rows=rows,
        source=str(payload.get("source") or ""),
    )


def _grid_range(sheet_id: int | None, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "sheetId": sheet_id,
        **{key: int(item[key]) for key in GRID_KEYS if isinstance(item.get(key), int)},
    }


def _shift_merges(
    merges: Iterable[dict[str, Any]],
    first_row: int,
    delta: int,
) -> list[dict[str, Any]]:
    """Dời merge có dòng bắt đầu >= ``first_row`` (1-based) đi ``delta`` dòng."""

    shifted: list[dict[str, Any]] = []
    for merge in merges:
        start = int(merge["startRowIndex"])
        end = int(merge["endRowIndex"])
        if start + 1 >= first_row:
            start += delta
            end += delta
        shifted.append({**merge, "startRowIndex": start, "endRowIndex": end})
    return shifted

@dataclass(frozen=True)
class MenuRestorePlan:
    """Kế hoạch khôi phục: request đã dựng sẵn, chưa ghi gì."""

    sheet_id: int | None
    header_row: int
    rows_shift: int
    zone_first: int
    zone_last_before: int
    zone_last_after: int
    product_count: int
    duplicate_rows: tuple[int, ...]
    footer_from: int
    footer_to: int
    value_writes: tuple[tuple[str, list[list[str]]], ...] = ()
    delete_requests: tuple[dict[str, Any], ...] = ()
    unmerge_requests: tuple[dict[str, Any], ...] = ()
    merge_requests: tuple[dict[str, Any], ...] = ()
    structure_requests: tuple[dict[str, Any], ...] = ()
    products: dict[str, tuple[str, ...]] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()
    selector_from: int = 0
    selector_to: int = 0

    @property
    def blocked(self) -> bool:
        return bool(self.problems)

    @property
    def request_count(self) -> int:
        return (
            len(self.delete_requests)
            + len(self.unmerge_requests)
            + len(self.merge_requests)
            + len(self.structure_requests)
        )

    def describe(self) -> list[str]:
        lines = [
            (
                f"Khôi phục MENU: {self.product_count} sản phẩm, vùng chọn "
                f"B{self.zone_first}:B{self.zone_last_after} "
                f"(trước B{self.zone_first}:B{self.zone_last_before}, "
                f"dịch {self.rows_shift} dòng)."
            ),
            (
                "Xóa dòng tiêu đề nhân bản: "
                f"{list(self.duplicate_rows) if self.duplicate_rows else 'không'}; "
                f"footer dời {self.footer_from} → {self.footer_to}."
            ),
            (
                f"Request: xóa {len(self.delete_requests)}, bỏ gộp "
                f"{len(self.unmerge_requests)}, gộp lại {len(self.merge_requests)}, "
                f"cấu trúc {len(self.structure_requests)}; "
                f"lượt ghi giá trị {len(self.value_writes)}."
            ),
        ]
        lines.extend(f"- {note}" for note in self.notes)
        lines.extend(f"LỖI: {problem}" for problem in self.problems)
        return lines

    def as_dict(self) -> dict[str, Any]:
        return {
            "sheet_id": self.sheet_id,
            "header_row": self.header_row,
            "product_count": self.product_count,
            "rows_shift": self.rows_shift,
            "zone": f"B{self.zone_first}:B{self.zone_last_after}",
            "zone_before": f"B{self.zone_first}:B{self.zone_last_before}",
            "duplicate_rows": list(self.duplicate_rows),
            "footer": f"{self.footer_from}->{self.footer_to}",
            "request_count": self.request_count,
            "value_ranges": [range_a1 for range_a1, _matrix in self.value_writes],
            "notes": list(self.notes),
            "problems": list(self.problems),
        }


PANEL_TOP_FIRST = 7
PANEL_TOP_LAST = 35
PANEL_BOTTOM_FIRST = 36




def _delete_row_request(sheet_id: int, row: int) -> dict[str, Any]:
    return {
        "deleteDimension": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "ROWS",
                "startIndex": row - 1,
                "endIndex": row,
            }
        }
    }


def _unmerge_request(sheet_id: int, merge: dict[str, Any]) -> dict[str, Any]:
    return {"unmergeCells": {"range": _grid_range(sheet_id, merge)}}


def _merge_request(sheet_id: int, merge: dict[str, Any]) -> dict[str, Any]:
    return {"mergeCells": {"range": _grid_range(sheet_id, merge), "mergeType": "MERGE_ALL"}}


def _validation_request(sheet_id: int, item: dict[str, Any]) -> dict[str, Any] | None:
    rows = [int(row) for row in (item.get("rows") or []) if isinstance(row, int)]
    head = str(item.get("range") or "").split(":", 1)[0]
    match = re.match(r"^([A-Za-z]{1,3})(\d+)$", head)
    if match is None or not rows:
        return None
    values = [
        str(value.get("userEnteredValue") if isinstance(value, dict) else value)
        for value in (item.get("values") or [])
    ]
    rule = {
        "condition": {
            "type": str(item.get("type") or "ONE_OF_RANGE"),
            "values": [{"userEnteredValue": text} for text in values],
        },
        "showCustomUi": True,
        "strict": False,
    }
    column = column_index(match.group(1))
    return {
        "setDataValidation": {
            "range": _grid_range(
                sheet_id,
                {
                    "startRowIndex": rows[0] - 1,
                    "endRowIndex": rows[-1],
                    "startColumnIndex": column,
                    "endColumnIndex": column + 1,
                },
            ),
            "rule": rule,
        }
    }


def _formula_request(sheet_id: int, item: dict[str, Any]) -> dict[str, Any] | None:
    match = re.match(r"^([A-Za-z]{1,3})(\d+)$", str(item.get("cell") or "").strip())
    formula = str(item.get("formula") or "")
    if match is None or not formula:
        return None
    row = int(match.group(2))
    column = column_index(match.group(1))
    return {
        "updateCells": {
            "range": _grid_range(
                sheet_id,
                {
                    "startRowIndex": row - 1,
                    "endRowIndex": row,
                    "startColumnIndex": column,
                    "endColumnIndex": column + 1,
                },
            ),
            "rows": [{"values": [{"userEnteredValue": {"formulaValue": formula}}]}],
            "fields": "userEnteredValue.formulaValue",
        }
    }


def _width_request(sheet_id: int, column: str, width: int) -> dict[str, Any]:
    index = column_index(column)
    return {
        "updateDimensionProperties": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "COLUMNS",
                "startIndex": index,
                "endIndex": index + 1,
            },
            "properties": {"pixelSize": width},
            "fields": "pixelSize",
        }
    }




def plan_menu_restore(
    structure: dict[str, Any],
    baseline: MenuBaseline,
    panel: PolicyPanel,
) -> MenuRestorePlan:
    """Lập kế hoạch khôi phục layout MENU. Chỉ đọc ``structure``, không ghi Google."""

    if not isinstance(structure, dict):
        raise MenuLayoutError("Cấu trúc MENU không hợp lệ.")
    values = [list(row) for row in (structure.get("values") or []) if isinstance(row, list)]
    current = menu_baseline_from_structure(structure, MENU_HEADERS)
    header_row = int(baseline.header_row or 0)
    if header_row <= 0:
        raise MenuLayoutError("Baseline MENU thiếu dòng tiêu đề.")
    zone_first, zone_last_before = menu_selector_span(baseline)
    if zone_first is None or zone_last_before is None:
        raise MenuLayoutError("Baseline MENU thiếu dropdown vùng chọn K để khôi phục.")
    sheet_id = current.sheet_id if current.sheet_id is not None else baseline.sheet_id
    if sheet_id is None:
        raise MenuLayoutError("Không xác định được sheetId của tab MENU.")

    problems: list[str] = []
    notes: list[str] = []

    if baseline.headers != list(MENU_HEADERS):
        problems.append("Baseline không đúng bộ tiêu đề A:H.")
    if current.header_row != header_row:
        problems.append(
            f"Dòng tiêu đề hiện tại là {current.header_row}, baseline là {header_row}."
        )

    duplicated = [
        row
        for row in range(header_row + 1, len(values) + 1)
        if _is_header_row(_grid(values, row))
    ]
    deletable = tuple(row for row in duplicated if row <= header_row + 3)
    leftover = tuple(row for row in duplicated if row not in deletable)
    if leftover:
        problems.append(
            "Còn dòng tiêu đề nhân bản trong vùng sản phẩm: "
            + ", ".join(str(row) for row in leftover)
            + "."
        )
    if len(deletable) > 2:
        problems.append("Có nhiều hơn 2 dòng tiêu đề liền nhau; cần kiểm tra tay.")

    first_product = next(
        (
            row
            for row in range(header_row + 1, len(values) + 1)
            if _PRODUCT_ID.match(_text(_grid(values, row), 0))
        ),
        None,
    )
    rows: list[int] = []
    if first_product is None:
        problems.append("Không thấy dòng sản phẩm nào (cột A là mã SXN-…).")
    else:
        rows = _product_rows(values, first_product, len(values))
        if rows != list(range(first_product, first_product + len(rows))):
            problems.append("Các dòng sản phẩm không liền mạch; không tự đoán vị trí.")
        expected_first = first_product - len(deletable)
        if expected_first != zone_first:
            problems.append(
                f"Sau khi bỏ {len(deletable)} dòng tiêu đề nhân bản, sản phẩm bắt đầu ở "
                f"dòng {expected_first} chứ không phải B{zone_first} của baseline."
            )
    product_count = len(rows)
    zone_rows_before = zone_last_before - zone_first + 1
    current_selector = menu_selector_span(current)
    current_zone_end = int(current_selector[1]) if current_selector[1] is not None else 0
    zone_rows_after = max(
        product_count,
        zone_rows_before,
        current_zone_end - zone_first + 1 if current_zone_end else 0,
    )
    zone_last_after = zone_first + zone_rows_after - 1
    if product_count and product_count < zone_rows_before:
        problems.append(
            f"Chỉ còn {product_count} sản phẩm, ít hơn {zone_rows_before} chỗ của "
            "baseline; không tự thu hẹp vùng chọn."
        )
    rows_shift = max(0, zone_last_after - zone_last_before)

    base_footer = _footer_rows(baseline)
    current_footer = _footer_rows(current)
    footer_from = current_footer[0] if current_footer else 0
    footer_to = (base_footer[0] + rows_shift) if base_footer else 0
    if not current_footer or not base_footer:
        problems.append("Không xác định được khối footer A:H để dời chỗ.")
    elif len(current_footer) != len(base_footer):
        problems.append(
            f"Footer hiện có {len(current_footer)} dòng, baseline {len(base_footer)} dòng."
        )

    products: dict[str, tuple[str, ...]] = {}
    for row in rows:
        label = _text(_grid(values, row), 0)
        products[label] = tuple(
            str(_cell(_grid(values, row), column)) for column in range(MENU_COLUMNS)
        )

    if problems:
        return MenuRestorePlan(
            sheet_id=sheet_id,
            header_row=header_row,
            rows_shift=rows_shift,
            zone_first=zone_first,
            zone_last_before=zone_last_before,
            zone_last_after=zone_last_after,
            product_count=product_count,
            duplicate_rows=deletable,
            footer_from=footer_from,
            footer_to=footer_to,
            products=products,
            notes=tuple(notes),
            problems=tuple(problems),
        )
    return _build_plan(
        structure=structure,
        baseline=baseline,
        panel=panel,
        current=current,
        values=values,
        sheet_id=sheet_id,
        header_row=header_row,
        zone_first=zone_first,
        zone_last_before=zone_last_before,
        zone_last_after=zone_last_after,
        product_count=product_count,
        rows_shift=rows_shift,
        deletable=deletable,
        base_footer=base_footer,
        current_footer=current_footer,
        products=products,
        notes=notes,
    )

def _footer_rows(baseline: MenuBaseline) -> list[int]:
    """Dòng của khối footer A:H (merge bắt đầu ở cột A, dưới dòng tiêu đề)."""

    header = baseline.header_row or 0
    return sorted(
        {
            int(merge["startRowIndex"]) + 1
            for merge in baseline.merged_ranges
            if int(merge.get("startColumnIndex") or 0) == 0
            and int(merge.get("startRowIndex") or 0) + 1 > header
        }
    )


def _build_plan(
    *,
    structure: dict[str, Any],
    baseline: MenuBaseline,
    panel: PolicyPanel,
    current: MenuBaseline,
    values: Sequence[Sequence[Any]],
    sheet_id: int,
    header_row: int,
    zone_first: int,
    zone_last_before: int,
    zone_last_after: int,
    product_count: int,
    rows_shift: int,
    deletable: Sequence[int],
    base_footer: Sequence[int],
    current_footer: Sequence[int],
    products: dict[str, tuple[str, ...]],
    notes: list[str],
) -> MenuRestorePlan:
    problems: list[str] = []
    notes = list(notes)
    target = shift_menu_baseline(
        baseline,
        insert_index=zone_last_before,
        rows=rows_shift,
        old_selector_start_row=zone_first,
        old_selector_end_row=zone_last_before,
        new_selector_end_row=zone_last_after,
    )
    delete_requests = tuple(
        _delete_row_request(sheet_id, row) for row in sorted(deletable, reverse=True)
    )
    surviving = list(current.merged_ranges)
    for row in sorted(deletable, reverse=True):
        surviving = _shift_merges(surviving, first_row=row, delta=-1)
    unmerge_requests = tuple(
        _unmerge_request(sheet_id, merge)
        for merge in surviving
        if int(merge["startRowIndex"]) >= header_row
    )
    merge_requests = tuple(
        _merge_request(sheet_id, merge)
        for merge in target.merged_ranges
        if int(merge["startRowIndex"]) >= header_row
    )

    structure_requests: list[dict[str, Any]] = []
    for item in target.data_validations:
        request = _validation_request(sheet_id, item)
        if request is not None:
            structure_requests.append(request)
    for item in target.formulas:
        match = re.match(r"^([A-Za-z]{1,3})(\d+)$", str(item.get("cell") or ""))
        if match is None or column_index(match.group(1)) < MENU_POLICY_COLUMN_START:
            continue
        request = _formula_request(sheet_id, item)
        if request is not None:
            structure_requests.append(request)

    raw_protected = [
        item
        for item in (structure.get("protected_ranges_raw") or [])
        if isinstance(item, dict) and isinstance(item.get("range"), dict)
    ]
    live_policy = next(
        (
            item
            for item in raw_protected
            if int(item["range"].get("startColumnIndex") or 0)
            == MENU_PROTECTED_COLUMN_START
        ),
        None,
    )
    target_policy = next(
        (
            item
            for item in target.protected_ranges
            if int(item.get("startColumnIndex") or 0) == MENU_PROTECTED_COLUMN_START
        ),
        None,
    )
    protected_id = live_policy.get("protectedRangeId") if live_policy else None
    if not isinstance(protected_id, int):
        problems.append("Không đọc được protectedRangeId của vùng bảo vệ I:M.")
    elif target_policy is None:
        problems.append("Baseline thiếu vùng bảo vệ I:M.")
    else:
        structure_requests.append(
            {
                "updateProtectedRange": {
                    "protectedRange": {
                        "protectedRangeId": protected_id,
                        "range": _grid_range(sheet_id, target_policy),
                        "warningOnly": bool(target_policy.get("warningOnly", True)),
                    },
                    "fields": "range",
                }
            }
        )

    for column in sorted(target.column_widths, key=column_index):
        if column_index(column) > MENU_LAST_COLUMN:
            continue
        width = int(target.column_widths[column])
        if current.column_widths.get(column) == width:
            continue
        structure_requests.append(_width_request(sheet_id, column, width))

    matrix = _panel_matrix(panel, zone_last_after)
    panel_last = PANEL_TOP_FIRST + len(matrix) - 1
    writes: list[tuple[str, list[list[str]]]] = [
        (f"'{TAB_MENU}'!J{PANEL_TOP_FIRST}:M{panel_last}", matrix)
    ]
    footer_matrix = [
        [str(_cell(_grid(values, row), column)) for column in range(MENU_COLUMNS)]
        for row in current_footer
    ]
    footer_last = int(base_footer[0]) + rows_shift + len(footer_matrix) - 1
    writes.append((f"'{TAB_MENU}'!A{int(base_footer[0]) + rows_shift}:H{footer_last}", footer_matrix))
    stale_last = int(current_footer[-1]) - len(deletable)
    if stale_last > footer_last:
        writes.append(
            (
                f"'{TAB_MENU}'!A{footer_last + 1}:H{stale_last}",
                [[""] * MENU_COLUMNS for _ in range(stale_last - footer_last)],
            )
        )

    notes.append(
        f"Panel J:M lấy từ {panel.source or 'file local'} "
        f"(dòng {PANEL_TOP_FIRST}–{panel.max_row()})."
    )
    notes.append(
        f"Merge/công thức/dropdown/vùng bảo vệ/khổ cột lấy từ baseline đã xác minh, "
        f"dịch {rows_shift} dòng dưới dòng {zone_last_before + 1}."
    )
    selector_from = 0
    for item in baseline.data_validations:
        control_rows = [int(row) for row in (item.get("rows") or []) if isinstance(row, int)]
        if control_rows:
            selector_from = control_rows[0]
            break
    selector_to = selector_from + rows_shift if selector_from else 0
    if selector_from and selector_to and selector_from != selector_to:
        notes.append(f"Ô chọn sản phẩm K{selector_from} → K{selector_to}.")
    if not any(
        int(item["startColumnIndex"]) == MENU_POLICY_COLUMN_START
        and int(item["startRowIndex"]) + 1 > panel_last
        and str(_cell(_grid(values, int(item["startRowIndex"]) + 1), MENU_POLICY_COLUMN_START)).strip()
        for item in current.merged_ranges
    ):
        notes.append("Không còn nội dung J:M nằm dưới panel mới.")

    return MenuRestorePlan(
        sheet_id=sheet_id,
        header_row=header_row,
        rows_shift=rows_shift,
        zone_first=zone_first,
        zone_last_before=zone_last_before,
        zone_last_after=zone_last_after,
        product_count=product_count,
        duplicate_rows=tuple(deletable),
        footer_from=int(current_footer[0]),
        footer_to=int(base_footer[0]) + rows_shift,
        value_writes=tuple(writes),
        delete_requests=delete_requests,
        unmerge_requests=unmerge_requests,
        merge_requests=merge_requests,
        structure_requests=tuple(structure_requests),
        products=products,
        notes=tuple(notes),
        problems=tuple(problems),
        selector_from=selector_from,
        selector_to=selector_to,
    )


def _panel_matrix(panel: PolicyPanel, gap_last: int) -> list[list[str]]:
    """Panel J:M đích: khối trên giữ nguyên, chừa dòng sản phẩm, khối chọn dời xuống."""

    missing = [
        row for row in range(PANEL_TOP_FIRST, panel.max_row() + 1) if row not in panel.rows
    ]
    if missing:
        raise MenuLayoutError(
            "Panel chính sách thiếu dòng "
            + ", ".join(str(row) for row in missing[:8])
            + "."
        )
    matrix = panel.matrix(PANEL_TOP_FIRST, PANEL_TOP_LAST)
    matrix.extend([[""] * panel.columns for _ in range(max(gap_last - PANEL_TOP_LAST, 0))])
    matrix.extend(panel.matrix(PANEL_BOTTOM_FIRST, panel.max_row()))
    return matrix


def apply_menu_restore(
    gateway: Any,
    spreadsheet_id: str,
    plan: MenuRestorePlan,
) -> dict[str, int]:
    """Ghi kế hoạch: xóa dòng tiêu đề nhân bản, bỏ gộp, ghi giá trị, gộp lại."""

    if plan.blocked:
        raise MenuLayoutError(
            "Kế hoạch khôi phục còn lỗi: " + "; ".join(plan.problems[:3])
        )
    counts = {
        "deleted_rows": len(plan.delete_requests),
        "unmerged": len(plan.unmerge_requests),
        "value_ranges": len(plan.value_writes),
        "merged": len(plan.merge_requests),
        "structure": len(plan.structure_requests),
    }
    first_batch = [*plan.delete_requests, *plan.unmerge_requests]
    if first_batch:
        gateway.batch_update(spreadsheet_id, first_batch)
    if plan.value_writes:
        gateway.batch_update_values(
            spreadsheet_id,
            [(range_a1, matrix) for range_a1, matrix in plan.value_writes],
            value_input_option="RAW",
        )
    second_batch = [*plan.merge_requests, *plan.structure_requests]
    if second_batch:
        gateway.batch_update(spreadsheet_id, second_batch)
    return counts


def _panel_hits(baseline: MenuBaseline, needle: str) -> int:
    hits = 0
    for item in baseline.formulas:
        match = re.match(r"^([A-Za-z]{1,3})(\d+)$", str(item.get("cell") or ""))
        if match is None:
            continue
        if column_index(match.group(1)) < MENU_POLICY_COLUMN_START:
            continue
        if needle in str(item.get("formula") or "").replace("$", ""):
            hits += 1
    return hits


def verify_menu_restore(
    before: dict[str, Any],
    after: dict[str, Any],
    baseline: MenuBaseline,
    plan: MenuRestorePlan,
    panel: PolicyPanel,
) -> list[str]:
    """Đối chiếu sau khi ghi: layout, dropdown, công thức, sản phẩm, panel."""

    problems: list[str] = []
    del before  # chỉ dùng ``plan.products`` làm mốc trước khi ghi
    after_values = [list(row) for row in (after.get("values") or []) if isinstance(row, list)]
    after_baseline = menu_baseline_from_structure(after, MENU_HEADERS)

    if after_baseline.merge_count != baseline.merge_count:
        problems.append(
            f"Số vùng gộp sau khôi phục là {after_baseline.merge_count}, "
            f"baseline cần {baseline.merge_count}."
        )
    try:
        shift = menu_expansion_shift(baseline, after_baseline)
    except Exception as exc:  # noqa: BLE001
        shift = None
        problems.append(f"Không so được phép dịch dòng: {type(exc).__name__}.")
    if shift != plan.rows_shift:
        problems.append(
            "Layout sau khôi phục chưa bằng baseline dịch "
            f"{plan.rows_shift} dòng (phép dịch đọc lại: {shift})."
        )

    span = f"B{plan.zone_first}:B{plan.zone_last_after}"
    dropdown = any(
        span in json.dumps(item.get("values") or [], ensure_ascii=False).replace("$", "")
        for item in after_baseline.data_validations
    )
    if not dropdown:
        problems.append(f"Thiếu dropdown vùng chọn {span} ở cột K.")
    if _panel_hits(after_baseline, f"B{plan.zone_first}:") < 2:
        problems.append("Thiếu 2 công thức J:M VLOOKUP vùng sản phẩm.")
    if plan.selector_to:
        needle = f"K{plan.selector_to}"
        for item in after_baseline.formulas:
            cell = str(item.get("cell") or "").upper()
            if not cell.startswith(("J", "K", "L", "M")):
                continue
            if needle not in str(item.get("formula") or "").replace("$", ""):
                problems.append(
                    f"Công thức {cell} không trỏ ô chọn {needle} sau khôi phục."
                )
                break

    duplicated = [
        row
        for row in range(plan.header_row + 1, len(after_values) + 1)
        if _is_header_row(_grid(after_values, row))
    ]
    if duplicated:
        problems.append(
            "Vẫn còn dòng tiêu đề nhân bản ở dòng "
            + ", ".join(str(row) for row in duplicated)
            + "."
        )

    after_products: dict[str, tuple[str, ...]] = {}
    for row in range(plan.zone_first, plan.zone_last_after + 1):
        label = _text(_grid(after_values, row), 0)
        if not label:
            continue
        after_products[label] = tuple(
            str(_cell(_grid(after_values, row), column)) for column in range(MENU_COLUMNS)
        )
    if after_products != plan.products:
        missing = sorted(set(plan.products) - set(after_products))
        extra = sorted(set(after_products) - set(plan.products))
        changed = [
            key
            for key in sorted(set(plan.products) & set(after_products))
            if plan.products[key] != after_products[key]
        ]
        problems.append(
            "Dữ liệu sản phẩm A:H đổi sau khôi phục "
            f"(mất {missing[:4]}, thêm {extra[:4]}, lệch {changed[:4]})."
        )

    for row in (PANEL_TOP_FIRST, PANEL_TOP_LAST):
        expected = panel.line(row)
        actual = tuple(
            _text(_grid(after_values, row), MENU_POLICY_COLUMN_START + offset)
            for offset in range(POLICY_COLUMNS)
        )
        if expected and actual[0] != expected[0]:
            problems.append(f"Panel J{row} không khớp bản lưu.")
    selector_row = PANEL_BOTTOM_FIRST + 1 + plan.rows_shift
    if _text(_grid(after_values, selector_row), MENU_POLICY_COLUMN_START) != panel.line(
        PANEL_BOTTOM_FIRST + 1
    )[0]:
        problems.append(f"Thiếu tiêu đề panel chi tiết ở dòng {selector_row}.")

    footer_row = plan.footer_to
    footer_merged = any(
        int(item.get("startColumnIndex") or 0) == 0
        and int(item.get("startRowIndex") or -1) + 1 == footer_row
        for item in (after.get("merges") or [])
        if isinstance(item, dict)
    )
    if not footer_merged:
        problems.append(
            f"Thiếu khối footer gộp bắt đầu ở dòng {footer_row} sau khôi phục."
        )
    return problems

