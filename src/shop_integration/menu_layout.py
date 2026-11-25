"""Khóa layout MENU Phase 4.3B.

MENU là UI chính thức của shop. Tool chỉ được ghi giá trị ô sản phẩm cột A–H.
Cột I là khoảng đệm, cột J–M là chính sách bảo hành. Merge, công thức, dropdown,
màu, border, chiều rộng cột và chiều cao dòng phải giữ nguyên.

Module này chỉ đọc cấu trúc và kiểm tra trước khi ghi. Không tự gọi Google.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from shop_integration.errors import MenuLayoutError
from shop_integration.sheet_io import column_letter

MENU_TAB = "MENU"
MENU_WRITABLE_COLUMNS: tuple[int, ...] = tuple(range(8))  # A..H
MENU_PROTECTED_COLUMN_START = 8  # I
MENU_POLICY_COLUMN_START = 9  # J
MENU_LAST_COLUMN = 12  # M
MENU_POLICY_COLUMNS = ("J", "K", "L", "M")
MENU_INSPECT_ROWS = 200
MENU_INSPECT_COLUMNS = 13
MENU_BASELINE_SCHEMA = 2

# Request batchUpdate chạm MENU trong lúc đồng bộ sản phẩm đều bị từ chối.
LAYOUT_REQUEST_TYPES = frozenset(
    {
        "mergeCells",
        "unmergeCells",
        "updateDimensionProperties",
        "autoResizeDimensions",
        "updateSheetProperties",
        "updateCells",
        "repeatCell",
        "updateBorders",
        "setDataValidation",
        "addBanding",
        "updateBanding",
        "deleteBanding",
        "addConditionalFormatRule",
        "updateConditionalFormatRule",
        "deleteConditionalFormatRule",
        "setBasicFilter",
        "clearBasicFilter",
        "insertDimension",
        "deleteDimension",
        "appendDimension",
        "insertRange",
        "deleteRange",
        "sortRange",
        "copyPaste",
        "cutPaste",
        "pasteData",
    }
)

_CELL_RANGE = re.compile(
    r"^'?(?P<tab>[^'!]+)'?!\$?(?P<column>[A-Za-z]{1,3})\$?(?P<row>\d+)$"
)
_COLUMN = re.compile(r"^[A-Za-z]{1,3}$")


def column_index(letters: str) -> int:
    """Chữ cái A1 thành chỉ số cột 0-based."""

    if not _COLUMN.match(letters or ""):
        raise MenuLayoutError(f"Cột '{letters}' không hợp lệ.")
    number = 0
    for char in letters.upper():
        number = number * 26 + (ord(char) - 64)
    return number - 1


def parse_cell_range(range_a1: str) -> tuple[str, int, int]:
    """Tách 'MENU'!H12 thành tab, cột 0-based và dòng 1-based. Chỉ nhận một ô."""

    text = str(range_a1 or "").strip()
    match = _CELL_RANGE.match(text)
    if match is None:
        raise MenuLayoutError(f"Vùng ghi '{range_a1}' không phải một ô A1 hợp lệ.")
    return (
        match.group("tab"),
        column_index(match.group("column")),
        int(match.group("row")),
    )


def cell_name(column: int, row: int) -> str:
    return f"{column_letter(column)}{row}"


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _merged_cells(merges: Iterable[dict[str, Any]]) -> set[str]:
    """Mọi ô nằm trong vùng gộp đều bị khóa, kể cả ô góc trên trái."""

    found: set[str] = set()
    for merge in merges:
        if not isinstance(merge, dict):
            continue
        start_row = _as_int(merge.get("startRowIndex"))
        end_row = _as_int(merge.get("endRowIndex"))
        start_column = _as_int(merge.get("startColumnIndex"))
        end_column = _as_int(merge.get("endColumnIndex"))
        if None in {start_row, end_row, start_column, end_column}:
            continue
        for row in range(int(start_row), int(end_row)):
            for column in range(int(start_column), int(end_column)):
                found.add(cell_name(column, row + 1))
    return found


def normalize_merges(merges: Iterable[Any]) -> list[dict[str, int]]:
    found: list[dict[str, int]] = []
    for merge in merges:
        if not isinstance(merge, dict):
            continue
        values = {
            key: _as_int(merge.get(key))
            for key in (
                "startRowIndex",
                "endRowIndex",
                "startColumnIndex",
                "endColumnIndex",
            )
        }
        if any(value is None for value in values.values()):
            continue
        found.append({key: int(value) for key, value in values.items()})
    found.sort(
        key=lambda item: (
            item["startRowIndex"],
            item["startColumnIndex"],
            item["endRowIndex"],
            item["endColumnIndex"],
        )
    )
    return found


def normalize_protected_ranges(ranges: Iterable[Any]) -> list[dict[str, Any]]:
    """Chuẩn hóa protectedRanges của Sheets. Chỉ giữ chỉ số, không giữ ghi chú UI."""

    found: list[dict[str, Any]] = []
    for item in ranges:
        if not isinstance(item, dict):
            continue
        raw_range = item.get("range") if isinstance(item.get("range"), dict) else item
        values = {
            key: _as_int(raw_range.get(key))
            for key in (
                "startRowIndex",
                "endRowIndex",
                "startColumnIndex",
                "endColumnIndex",
            )
        }
        if any(value is None for value in values.values()):
            continue
        found.append(
            {
                **{key: int(value) for key, value in values.items()},
                "warningOnly": bool(item.get("warningOnly")),
            }
        )
    found.sort(
        key=lambda entry: (
            entry["startRowIndex"],
            entry["startColumnIndex"],
            entry["endRowIndex"],
            entry["endColumnIndex"],
        )
    )
    return found


def _protected_range_cells(
    ranges: Iterable[dict[str, Any]],
    *,
    max_rows: int,
    last_column: int = MENU_LAST_COLUMN,
) -> set[str]:
    """Mọi ô nằm trong vùng bảo vệ, giới hạn ở vùng tool có thể ghi (A–M)."""

    found: set[str] = set()
    for entry in ranges:
        if not isinstance(entry, dict):
            continue
        start_row = _as_int(entry.get("startRowIndex"))
        end_row = _as_int(entry.get("endRowIndex"))
        start_column = _as_int(entry.get("startColumnIndex"))
        end_column = _as_int(entry.get("endColumnIndex"))
        if None in {start_row, end_row, start_column, end_column}:
            continue
        for row in range(int(start_row), min(int(end_row), max_rows)):
            for column in range(int(start_column), min(int(end_column), last_column + 1)):
                found.add(cell_name(column, row + 1))
    return found


def _header_key(value: object) -> str:
    return " ".join(str(value or "").strip().split()).casefold()


def find_header_row(values: Sequence[Sequence[Any]], headers: Sequence[str]) -> int | None:
    """Dòng tiêu đề 1-based. So khớp chữ, không phân biệt hoa thường."""

    wanted = [_header_key(header) for header in headers]
    if not wanted:
        return None
    for index, row in enumerate(values, start=1):
        cells = [
            _header_key(row[column] if column < len(row) else "")
            for column in range(len(wanted))
        ]
        if cells == wanted:
            return index
    return None



def _validation_ranges(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Nén dropdown theo cột và dòng liên tiếp để baseline gọn, dễ so sánh."""

    grouped: dict[tuple[int, str], dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        cell = str(entry.get("cell") or "").strip()
        match = re.match(r"^([A-Za-z]{1,3})(\d+)$", cell)
        if match is None:
            continue
        values = list(entry.get("values") or [])
        payload = json.dumps(values, ensure_ascii=False, sort_keys=True)
        key = (column_index(match.group(1)), f"{entry.get('type') or ''}|{payload}")
        slot = grouped.setdefault(
            key,
            {
                "column": column_index(match.group(1)),
                "type": str(entry.get("type") or ""),
                "values": values,
                "rows": [],
            },
        )
        slot["rows"].append(int(match.group(2)))
    found: list[dict[str, Any]] = []
    for slot in grouped.values():
        rows = sorted(set(slot["rows"]))
        if not rows:
            continue
        column = column_letter(slot["column"])
        found.append(
            {
                "range": f"{column}{rows[0]}:{column}{rows[-1]}",
                "type": slot["type"],
                "values": slot["values"],
                "rows": rows,
            }
        )
    found.sort(key=lambda item: (item["range"], item["type"]))
    return found


@dataclass(frozen=True)
class MenuBaseline:
    """Ảnh chụp cấu trúc MENU, không chứa giá trị kinh doanh ngoài dòng tiêu đề."""

    headers: list[str]
    header_row: int | None
    data_rows: int
    data_columns: int
    merged_ranges: list[dict[str, int]]
    formulas: list[dict[str, str]]
    data_validations: list[dict[str, Any]]
    conditional_format_count: int
    column_widths: dict[str, int]
    row_heights: dict[str, int]
    protected_cells: list[str]
    protected_ranges: list[dict[str, Any]] = field(default_factory=list)
    protected_ranges_captured: bool = False
    sheet_id: int | None = None
    grid_properties: dict[str, Any] = field(default_factory=dict)
    tab: str = MENU_TAB
    captured_at: str = ""

    @property
    def merge_count(self) -> int:
        return len(self.merged_ranges)

    @property
    def protected(self) -> frozenset[str]:
        return frozenset(self.protected_cells)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": MENU_BASELINE_SCHEMA,
            "tab": self.tab,
            "sheet_id": self.sheet_id,
            "grid_properties": self.grid_properties,
            "captured_at": self.captured_at,
            "headers": self.headers,
            "header_row": self.header_row,
            "data_rows": self.data_rows,
            "data_columns": self.data_columns,
            "merge_count": self.merge_count,
            "merged_ranges": self.merged_ranges,
            "formulas": self.formulas,
            "data_validations": self.data_validations,
            "conditional_format_count": self.conditional_format_count,
            "column_widths": self.column_widths,
            "row_heights": self.row_heights,
            "protected_cells": self.protected_cells,
            "protected_ranges": self.protected_ranges,
            "protected_ranges_captured": self.protected_ranges_captured,
        }

    def fingerprint(self) -> str:
        payload = {
            key: value for key, value in self.as_dict().items() if key != "captured_at"
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def menu_baseline_from_structure(
    structure: dict[str, Any],
    headers: Sequence[str],
    *,
    captured_at: str = "",
) -> MenuBaseline:
    """Chuyển kết quả get_sheet_structure của MENU thành baseline đã chuẩn hóa."""

    if not isinstance(structure, dict):
        raise MenuLayoutError("Cấu trúc MENU trả về không hợp lệ.")
    values = [
        list(row)
        for row in (structure.get("values") or [])
        if isinstance(row, list)
    ]
    header_row = find_header_row(values, headers)
    header_values = (
        [
            str(values[header_row - 1][column])
            if column < len(values[header_row - 1])
            else ""
            for column in range(len(headers))
        ]
        if header_row is not None
        else []
    )
    merges = normalize_merges(structure.get("merges") or [])
    formulas = sorted(
        (
            {
                "cell": str(item.get("cell") or "").upper(),
                "formula": str(item.get("formula") or ""),
            }
            for item in (structure.get("formulas") or [])
            if isinstance(item, dict) and item.get("cell")
        ),
        key=lambda item: item["cell"],
    )
    validations = _validation_ranges(structure.get("data_validations") or [])
    widths = {
        str(key).upper(): int(value)
        for key, value in (structure.get("column_widths") or {}).items()
        if _as_int(value) is not None
    }
    heights = {
        str(key): int(value)
        for key, value in (structure.get("row_heights") or {}).items()
        if _as_int(value) is not None
    }
    protected = _merged_cells(merges)
    protected.update(item["cell"] for item in formulas if item["cell"])
    protected_ranges = normalize_protected_ranges(structure.get("protected_ranges") or [])
    protected.update(
        _protected_range_cells(protected_ranges, max_rows=max(len(values), 1))
    )
    return MenuBaseline(
        headers=header_values,
        header_row=header_row,
        data_rows=_last_data_row(values, MENU_LAST_COLUMN),
        data_columns=_last_data_column(values),
        merged_ranges=merges,
        formulas=formulas,
        data_validations=validations,
        conditional_format_count=int(structure.get("conditional_format_count") or 0),
        column_widths=widths,
        row_heights=heights,
        protected_cells=sorted(protected),
        protected_ranges=protected_ranges,
        protected_ranges_captured="protected_ranges" in structure,
        sheet_id=_as_int(structure.get("sheet_id")),
        grid_properties=dict(structure.get("grid_properties") or {}),
        tab=str(structure.get("title") or MENU_TAB),
        captured_at=captured_at,
    )


def save_menu_baseline(baseline: MenuBaseline, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(baseline.as_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def load_menu_baseline(path: str | Path) -> MenuBaseline:
    source = Path(path)
    if not source.is_file():
        raise MenuLayoutError(f"Không thấy file baseline MENU: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MenuLayoutError(f"Không đọc được baseline MENU: {exc}") from exc
    if not isinstance(payload, dict):
        raise MenuLayoutError("Baseline MENU không phải JSON object.")
    return MenuBaseline(
        headers=[str(item) for item in payload.get("headers") or []],
        header_row=_as_int(payload.get("header_row")),
        data_rows=int(payload.get("data_rows") or 0),
        data_columns=int(payload.get("data_columns") or 0),
        merged_ranges=normalize_merges(payload.get("merged_ranges") or []),
        formulas=[
            {
                "cell": str(item.get("cell") or "").upper(),
                "formula": str(item.get("formula") or ""),
            }
            for item in payload.get("formulas") or []
            if isinstance(item, dict)
        ],
        data_validations=[
            dict(item)
            for item in payload.get("data_validations") or []
            if isinstance(item, dict)
        ],
        conditional_format_count=int(payload.get("conditional_format_count") or 0),
        column_widths={
            str(key).upper(): int(value)
            for key, value in (payload.get("column_widths") or {}).items()
        },
        row_heights={
            str(key): int(value)
            for key, value in (payload.get("row_heights") or {}).items()
        },
        protected_cells=[str(item) for item in payload.get("protected_cells") or []],
        protected_ranges=normalize_protected_ranges(payload.get("protected_ranges") or []),
        protected_ranges_captured=bool(payload.get("protected_ranges_captured", False)),
        sheet_id=_as_int(payload.get("sheet_id")),
        grid_properties=dict(payload.get("grid_properties") or {}),
        tab=str(payload.get("tab") or MENU_TAB),
        captured_at=str(payload.get("captured_at") or ""),
    )


def _merge_key(merge: dict[str, int]) -> tuple[int, int, int, int]:
    return (
        merge["startRowIndex"],
        merge["startColumnIndex"],
        merge["endRowIndex"],
        merge["endColumnIndex"],
    )


def _formula_key(item: dict[str, str]) -> tuple[str, str]:
    return (str(item.get("cell") or "").upper(), str(item.get("formula") or ""))


def _validation_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("range") or ""),
        str(item.get("type") or ""),
        json.dumps(item.get("values") or [], ensure_ascii=False, sort_keys=True),
    )


def _protected_key(item: dict[str, Any]) -> tuple[int, int, int, int, bool]:
    return (
        int(item.get("startRowIndex") or 0),
        int(item.get("startColumnIndex") or 0),
        int(item.get("endRowIndex") or 0),
        int(item.get("endColumnIndex") or 0),
        bool(item.get("warningOnly")),
    )


def menu_structure_diff(
    baseline: MenuBaseline,
    current: MenuBaseline,
    *,
    columns: int = MENU_LAST_COLUMN + 1,
) -> list[str]:
    """Liệt kê khác biệt cấu trúc. Rỗng nghĩa là layout còn đúng baseline."""

    differences: list[str] = []
    if baseline.tab != current.tab:
        differences.append(f"Tab đổi từ '{baseline.tab}' sang '{current.tab}'.")
    if baseline.sheet_id is not None and current.sheet_id != baseline.sheet_id:
        differences.append(
            f"sheetId MENU đổi từ {baseline.sheet_id} sang {current.sheet_id}."
        )
    if baseline.header_row != current.header_row:
        differences.append(
            f"Dòng tiêu đề đổi từ {baseline.header_row} sang {current.header_row}."
        )
    if baseline.headers != current.headers:
        differences.append(
            "Dòng tiêu đề MENU đổi: "
            f"'{' | '.join(baseline.headers)}' → '{' | '.join(current.headers)}'."
        )
    missing_merges = sorted(
        set(map(_merge_key, baseline.merged_ranges))
        - set(map(_merge_key, current.merged_ranges))
    )
    extra_merges = sorted(
        set(map(_merge_key, current.merged_ranges))
        - set(map(_merge_key, baseline.merged_ranges))
    )
    if missing_merges or extra_merges:
        differences.append(
            f"Vùng gộp đổi: mất {len(missing_merges)}, thêm {len(extra_merges)} "
            f"(baseline {baseline.merge_count}, hiện tại {current.merge_count})."
        )
    missing_formulas = sorted(
        set(map(_formula_key, baseline.formulas))
        - set(map(_formula_key, current.formulas))
    )
    extra_formulas = sorted(
        set(map(_formula_key, current.formulas))
        - set(map(_formula_key, baseline.formulas))
    )
    if missing_formulas or extra_formulas:
        differences.append(
            f"Công thức đổi: mất {len(missing_formulas)}, thêm {len(extra_formulas)}."
        )
    missing_validations = sorted(
        set(map(_validation_key, baseline.data_validations))
        - set(map(_validation_key, current.data_validations))
    )
    extra_validations = sorted(
        set(map(_validation_key, current.data_validations))
        - set(map(_validation_key, baseline.data_validations))
    )
    if missing_validations or extra_validations:
        differences.append(
            "Dropdown đổi: "
            f"mất {len(missing_validations)}, thêm {len(extra_validations)}."
        )
    if baseline.conditional_format_count != current.conditional_format_count:
        differences.append(
            "Số rule định dạng điều kiện đổi từ "
            f"{baseline.conditional_format_count} sang {current.conditional_format_count}."
        )
    if baseline.protected_ranges_captured and current.protected_ranges_captured:
        before = {_protected_key(item) for item in baseline.protected_ranges}
        after = {_protected_key(item) for item in current.protected_ranges}
        if before != after:
            differences.append(
                "Vùng bảo vệ đổi: "
                f"mất {len(before - after)}, thêm {len(after - before)}."
            )
    for key in sorted(baseline.column_widths):
        if column_index(key) >= columns:
            continue
        if current.column_widths.get(key) != baseline.column_widths[key]:
            differences.append(
                f"Chiều rộng cột {key} đổi từ {baseline.column_widths[key]} "
                f"sang {current.column_widths.get(key)}."
            )
    for key in sorted(baseline.row_heights):
        if current.row_heights.get(key) != baseline.row_heights[key]:
            differences.append(
                f"Chiều cao dòng {key} đổi từ {baseline.row_heights[key]} "
                f"sang {current.row_heights.get(key)}."
            )
    for key in ("columnCount", "frozenRowCount", "frozenColumnCount"):
        before = _as_int(baseline.grid_properties.get(key))
        after = _as_int(current.grid_properties.get(key))
        if before is not None and after != before:
            differences.append(f"{key} đổi từ {before} sang {after}.")
    return differences


def _last_data_row(values: Sequence[Sequence[Any]], last_column: int) -> int:
    for index in range(len(values), 0, -1):
        row = values[index - 1]
        if any(
            str(row[column]).strip()
            for column in range(min(last_column + 1, len(row)))
        ):
            return index
    return 0



@dataclass(frozen=True)
class MenuLayoutGuard:
    """Whitelist ghi MENU: chỉ A–H, trong vùng dữ liệu, không chạm merge/công thức."""

    max_row: int
    sheet_id: int | None = None
    protected_cells: frozenset[str] = field(default_factory=frozenset)
    writable_columns: tuple[int, ...] = MENU_WRITABLE_COLUMNS

    @classmethod
    def from_sheet_meta(cls, meta: dict[str, Any], *, max_row: int) -> MenuLayoutGuard:
        merges = normalize_merges(meta.get("merges") or [])
        protected = _merged_cells(merges)
        protected.update(
            _protected_range_cells(
                normalize_protected_ranges(meta.get("protectedRanges") or []),
                max_rows=max(int(max_row), 1),
            )
        )
        return cls(
            max_row=int(max_row),
            sheet_id=_as_int(meta.get("sheetId")),
            protected_cells=frozenset(protected),
        )

    @classmethod
    def from_baseline(cls, baseline: MenuBaseline) -> MenuLayoutGuard:
        capacity = MenuCapacity.from_baseline(baseline)
        limit = (
            capacity.last_writable_row
            if capacity.last_writable_row is not None
            else baseline.data_rows
        )
        return cls(
            max_row=max(limit, baseline.header_row or 0),
            sheet_id=baseline.sheet_id,
            protected_cells=baseline.protected,
        )

    def merged_with(self, other: MenuLayoutGuard | None) -> MenuLayoutGuard:
        if other is None:
            return self
        limits = [value for value in (self.max_row, other.max_row) if value > 0]
        return MenuLayoutGuard(
            max_row=min(limits) if limits else 0,
            sheet_id=self.sheet_id if self.sheet_id is not None else other.sheet_id,
            protected_cells=self.protected_cells | other.protected_cells,
            writable_columns=tuple(
                sorted(set(self.writable_columns) & set(other.writable_columns))
            ),
        )

    def check_cell(self, range_a1: str) -> None:
        tab, column, row = parse_cell_range(range_a1)
        if tab != MENU_TAB:
            raise MenuLayoutError(
                f"Từ chối ghi '{range_a1}': guard MENU chỉ nhận tab {MENU_TAB}."
            )
        if column not in self.writable_columns:
            raise MenuLayoutError(
                f"Từ chối ghi MENU ô {cell_name(column, row)}: chỉ cho phép cột A–H. "
                "Cột I là khoảng đệm, cột J–M là chính sách bảo hành."
            )
        if row < 2:
            raise MenuLayoutError(
                f"Từ chối ghi MENU ô {cell_name(column, row)}: không ghi dòng tiêu đề."
            )
        if self.max_row and row > self.max_row:
            raise MenuLayoutError(
                f"Từ chối ghi MENU ô {cell_name(column, row)}: vượt vùng dữ liệu "
                f"1–{self.max_row}. Không append dòng để tránh đè layout."
            )
        if cell_name(column, row) in self.protected_cells:
            raise MenuLayoutError(
                f"Từ chối ghi MENU ô {cell_name(column, row)}: ô nằm trong vùng gộp "
                "hoặc đang có công thức. Giữ nguyên layout."
            )

    def check_cells(self, cells: Iterable[tuple[str, Any]]) -> None:
        for range_a1, _value in cells:
            self.check_cell(range_a1)

    def check_ranges(self, ranges: Iterable[str]) -> None:
        for range_a1 in ranges:
            self.check_cell(range_a1)

    def check_matrix(self, range_a1: str, matrix: Sequence[Sequence[Any]]) -> None:
        tab, column, row = parse_cell_range(range_a1)
        if tab != MENU_TAB:
            raise MenuLayoutError(f"Từ chối ghi '{range_a1}': guard MENU chỉ nhận tab MENU.")
        width = max((len(line) for line in matrix), default=0)
        height = max(len(matrix), 1)
        for row_offset in range(height):
            for column_offset in range(max(width, 1)):
                self.check_cell(cell_range_for(tab, column + column_offset, row + row_offset))

    def check_batch_requests(self, requests: Iterable[dict[str, Any]]) -> None:
        """Không cho bất kỳ batchUpdate nào chạm MENU khi đồng bộ sản phẩm."""

        for request in requests:
            if not isinstance(request, dict):
                raise MenuLayoutError("Request batchUpdate không hợp lệ.")
            kinds = set(request)
            ranges = list(_sheet_ranges(request))
            if (
                self.sheet_id is not None
                and any(
                    _as_int(sheet_range.get("sheetId")) == self.sheet_id
                    for sheet_range in ranges
                )
            ):
                raise MenuLayoutError(
                    "Từ chối thay đổi layout/format trên MENU trong lúc đồng bộ "
                    f"sản phẩm ({', '.join(sorted(kinds)) or '?'})."
                )
            if self.sheet_id is None and kinds & LAYOUT_REQUEST_TYPES:
                raise MenuLayoutError(
                    "Không xác định được tab đích của request layout/format. "
                    "Không ghi khi chưa có sheetId MENU."
                )


@dataclass(frozen=True)
class MenuCapacity:
    """Vùng A:H còn dùng được, tách khỏi panel J:M và vùng chọn K38.

    blocked_cells: ô A:H có merge/công thức/protected, không được ghi.
    selector_max_row: dòng cuối mà dropdown K38 và công thức J39/J40 còn đọc tới.
    panel_start_row: dòng bắt đầu panel chi tiết J:M (không dùng A:H từ dòng này).
    """

    blocked_cells: frozenset[str] = field(default_factory=frozenset)
    selector_min_row: int | None = None
    selector_max_row: int | None = None
    panel_start_row: int | None = None
    last_writable_row: int | None = None

    @classmethod
    def from_baseline(cls, baseline: MenuBaseline) -> MenuCapacity:
        blocked = frozenset(
            cell
            for cell in baseline.protected
            if column_index(re.sub(r"\d+$", "", cell)) in MENU_WRITABLE_COLUMNS
        )
        control_rows = sorted(
            row
            for row, column in (
                _validation_position(item) for item in baseline.data_validations
            )
            if row is not None and column is not None and column >= MENU_POLICY_COLUMN_START
        )
        control_rows.extend(
            row
            for row, column in (_cell_position(item.get("cell")) for item in baseline.formulas)
            if row is not None and column is not None and column >= MENU_POLICY_COLUMN_START
        )
        panel_start = min(control_rows) - 1 if control_rows else None
        if panel_start is not None and panel_start <= (baseline.header_row or 0) + 1:
            panel_start = None
        selector_min, selector_max = _selector_span(baseline)
        return cls(
            blocked_cells=blocked,
            selector_min_row=selector_min,
            selector_max_row=selector_max,
            panel_start_row=panel_start,
            last_writable_row=panel_start - 1 if panel_start is not None else None,
        )

    @property
    def selector_span(self) -> str:
        if self.selector_min_row is None or self.selector_max_row is None:
            return ""
        return f"B{self.selector_min_row}:B{self.selector_max_row}"

    def selector_covers(self, row: int) -> bool:
        return self.selector_max_row is None or row <= self.selector_max_row

    def row_allowed(self, row: int) -> bool:
        return self.last_writable_row is None or row <= self.last_writable_row

    def cell_blocked(self, column: int, row: int) -> bool:
        return cell_name(column, row) in self.blocked_cells


def _cell_position(cell: object) -> tuple[int | None, int | None]:
    match = re.match(r"^([A-Za-z]{1,3})(\d+)$", str(cell or "").strip())
    if match is None:
        return None, None
    return int(match.group(2)), column_index(match.group(1))


def _validation_position(item: dict[str, Any]) -> tuple[int | None, int | None]:
    cell = item.get("cell")
    if cell:
        return _cell_position(cell)
    start = str(item.get("range") or "").split(":", 1)[0]
    return _cell_position(start)


_SELECTOR_REFERENCE = re.compile(
    r"B\$?(\d+)\s*:\s*[A-Za-z]{1,3}\$?(\d+)", re.IGNORECASE
)


def _selector_span(baseline: MenuBaseline) -> tuple[int | None, int | None]:
    """Đọc vùng chọn từ dropdown K38 và công thức VLOOKUP B…:H… của J39/J40."""

    starts: list[int] = []
    ends: list[int] = []
    for item in baseline.data_validations:
        for value in item.get("values") or []:
            text = value.get("userEnteredValue") if isinstance(value, dict) else value
            for match in _SELECTOR_REFERENCE.finditer(str(text or "")):
                starts.append(int(match.group(1)))
                ends.append(int(match.group(2)))
    for item in baseline.formulas:
        for match in _SELECTOR_REFERENCE.finditer(str(item.get("formula") or "")):
            starts.append(int(match.group(1)))
            ends.append(int(match.group(2)))
    if not starts or not ends:
        return None, None
    return min(starts), max(ends)


def cell_range_for(tab: str, column: int, row: int) -> str:
    return f"'{tab}'!{cell_name(column, row)}"


def _sheet_ranges(node: Any) -> Iterable[dict[str, Any]]:
    if isinstance(node, dict):
        if "sheetId" in node:
            yield node
        for value in node.values():
            yield from _sheet_ranges(value)
    elif isinstance(node, list):
        for value in node:
            yield from _sheet_ranges(value)


def _last_data_column(values: Sequence[Sequence[Any]]) -> int:
    width = 0
    for row in values:
        for column, cell in enumerate(row):
            if str(cell).strip():
                width = max(width, column + 1)
    return width



def menu_guard_from_sheet_meta(
    items: Iterable[Any],
    *,
    max_row: int,
    tab: str = MENU_TAB,
) -> MenuLayoutGuard | None:
    for item in items:
        if isinstance(item, dict) and str(item.get("title")) == tab:
            return MenuLayoutGuard.from_sheet_meta(item, max_row=max_row)
    return None


def read_menu_baseline(
    gateway: Any,
    spreadsheet_id: str,
    headers: Sequence[str],
    *,
    tab: str = MENU_TAB,
    captured_at: str = "",
) -> MenuBaseline:
    """Đọc cấu trúc qua gateway. Gateway cũ không có get_sheet_structure sẽ báo lỗi rõ."""

    reader = getattr(gateway, "get_sheet_structure", None)
    if not callable(reader):
        raise MenuLayoutError(
            "Gateway không đọc được cấu trúc MENU (thiếu get_sheet_structure)."
        )
    structure = reader(spreadsheet_id, tab)
    return menu_baseline_from_structure(structure, headers, captured_at=captured_at)


class LayoutLockedGateway:
    """Bọc gateway để chặn tuyệt đối batchUpdate và ô ghi ngoài whitelist MENU."""

    def __init__(self, gateway: Any, guard: MenuLayoutGuard) -> None:
        self._gateway = gateway
        self.guard = guard

    def __getattr__(self, name: str) -> Any:
        return getattr(self._gateway, name)

    def batch_update(self, spreadsheet_id: str, requests: list[dict[str, Any]]) -> None:
        self.guard.check_batch_requests(requests)
        self._gateway.batch_update(spreadsheet_id, requests)

    def batch_update_values(
        self,
        spreadsheet_id: str,
        items: list[tuple[str, list[list[Any]]]],
        *,
        value_input_option: str = "RAW",
    ) -> None:
        for range_a1, matrix in items:
            if _range_tab(range_a1) == MENU_TAB:
                self.guard.check_matrix(range_a1, matrix)
        self._gateway.batch_update_values(
            spreadsheet_id,
            items,
            value_input_option=value_input_option,
        )

    def update_values(
        self,
        spreadsheet_id: str,
        range_a1: str,
        values: list[list[Any]],
        *,
        value_input_option: str = "RAW",
    ) -> None:
        if _range_tab(range_a1) == MENU_TAB:
            self.guard.check_matrix(range_a1, values)
        self._gateway.update_values(
            spreadsheet_id,
            range_a1,
            values,
            value_input_option=value_input_option,
        )

    def append_values(
        self, spreadsheet_id: str, range_a1: str, values: list[list[Any]]
    ) -> None:
        if _range_tab(range_a1) == MENU_TAB:
            raise MenuLayoutError(
                "Từ chối append dòng vào MENU. Chỉ ghi vào hàng trống có sẵn."
            )
        self._gateway.append_values(spreadsheet_id, range_a1, values)


def _range_tab(range_a1: str) -> str:
    text = str(range_a1 or "")
    if "!" not in text:
        return ""
    return text.split("!", 1)[0].strip().strip("'")


def _price_number(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().replace(".", "").replace(",", "").replace(" ", "")
    return int(text) if text.isdigit() else None


def _stock_value(value: object) -> tuple[int | None, str]:
    if isinstance(value, bool) or value is None:
        return None, ""
    if isinstance(value, (int, float)):
        return int(value), ""
    text = str(value).strip()
    if text.isdigit():
        return int(text), ""
    return None, text


def _menu_identity(row: dict[str, Any]) -> str:
    """Khóa ổn định khi có ProductID/VariantID. Rỗng nghĩa là chỉ còn tên để đối chiếu."""

    product_id = str(row.get("ProductID") or "").strip()
    variant_id = str(row.get("VariantID") or "").strip()
    if not product_id and not variant_id:
        return ""
    return f"{product_id or '?'}|{variant_id or '?'}"


def validate_menu_rows(
    rows: Iterable[dict[str, Any]],
    *,
    source: str,
) -> list[str]:
    """Kiểm tra dữ liệu nguồn trước khi chạm MENU. Trả danh sách lỗi, rỗng là hợp lệ."""

    problems: list[str] = []
    seen_names: dict[str, tuple[int, str]] = {}
    seen_identities: dict[str, tuple[int, str]] = {}
    for index, row in enumerate(rows, start=1):
        label = f"{source} dòng {index}"
        if not isinstance(row, dict):
            problems.append(f"{label}: không phải object.")
            continue
        name = str(row.get("PRODUCT") or "").strip()
        identity = _menu_identity(row)
        if not name:
            problems.append(f"{label}: PRODUCT trống.")
        else:
            key = name.casefold()
            if key in seen_names:
                other_index, other_identity = seen_names[key]
                if identity and other_identity and identity != other_identity:
                    problems.append(
                        f"{label}: PRODUCT '{name}' trùng tên dòng {other_index} nhưng "
                        f"khác ProductID/VariantID ({other_identity} → {identity}). "
                        "Không dùng tên làm định danh duy nhất."
                    )
                else:
                    problems.append(
                        f"{label}: PRODUCT '{name}' trùng dòng {other_index} trong cùng nguồn."
                    )
            else:
                seen_names[key] = (index, identity)
        if identity:
            if identity in seen_identities:
                other_index, other_name = seen_identities[identity]
                if other_name.casefold() != name.casefold():
                    problems.append(
                        f"{label}: ProductID/VariantID {identity} đã dùng cho "
                        f"'{other_name}' ở dòng {other_index}."
                    )
            else:
                seen_identities[identity] = (index, name)
        price = _price_number(row.get("PRICE"))
        if price is None or price <= 0:
            problems.append(
                f"{label}: PRICE phải là số dương, đang là {row.get('PRICE')!r}."
            )
        quantity, stock_text = _stock_value(row.get("STOCK"))
        if quantity is not None and quantity < 0:
            problems.append(f"{label}: STOCK không được âm, đang là {quantity}.")
        elif quantity is None and stock_text not in {
            "",
            "Còn hàng",
            "Hết hàng",
            "Chưa có dữ liệu",
        }:
            problems.append(f"{label}: STOCK '{stock_text}' không hợp lệ.")
    return problems


def assert_menu_rows_valid(rows: Iterable[dict[str, Any]], *, source: str) -> None:
    problems = validate_menu_rows(rows, source=source)
    if not problems:
        return
    shown = "; ".join(problems[:5])
    more = f" (+{len(problems) - 5} lỗi nữa)" if len(problems) > 5 else ""
    raise MenuLayoutError(
        f"Dữ liệu nguồn MENU không hợp lệ: {shown}{more}. Không ghi ô nào."
    )
