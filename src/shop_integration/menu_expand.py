"""Tự mở rộng vùng sản phẩm MENU khi nguồn có thêm sản phẩm.

Nguyên tắc: đúng một phép biến đổi layout được phép — chèn thêm dòng rỗng ngay
sau dòng sản phẩm cuối cùng (trước hàng trống và panel chi tiết J:M), rồi mở rộng
đúng những vùng phụ thuộc vào dòng cuối đó:

* dropdown K38 (vùng chọn ``MENU!$B$<đầu>:$B$<cuối>``);
* công thức J39/J40 (VLOOKUP ``B<đầu>:H<cuối>``, ``B<đầu>:C<cuối>``);
* định dạng điều kiện của cột STATUS (H);
* vùng bảo vệ phía dưới (I:M) để panel vẫn được cảnh báo sau khi dời xuống.

Không chèn/xóa dòng đang có sản phẩm, không đổi số vùng gộp (preflight yêu cầu
62) và không sửa nội dung J:M: panel chính sách chỉ bị dời xuống nguyên khối.

Ngoài ``apply_menu_expansion``, mọi hàm ở đây chỉ đọc dữ liệu.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, replace
from typing import Any, Sequence

from shop_integration.menu_layout import (
    MenuBaseline,
    MenuCapacity,
    column_index,
    menu_baseline_from_structure,
    menu_structure_diff,
)

MENU_TAB = "MENU"
MENU_COLUMNS = 8
_STATUS_COLUMN = 7
_STOCK_COLUMN = 6
DEFAULT_EXPAND_STEP = 10
DEFAULT_EXPAND_MAX_ROWS = 40
_GRID_TAIL_MARGIN = 10
_PRODUCT_ID = re.compile(r"^[A-Za-z]{2,6}-\d+$")
_SPAN = re.compile(
    r"(?P<prefix>\$?B\$?)(?P<start>\d+)\s*:\s*(?P<end_column>\$?[A-Za-z]{1,3}\$?)(?P<end>\d+)"
)
_CELL = re.compile(r"^([A-Za-z]{1,3})(\d+)$")


def _env_flag(name: str, default: str = "true") -> bool:
    raw = os.environ.get(name, default).strip().lower()
    return raw not in {"false", "0", "no", "off", ""}


def _env_int(name: str, default: int, *, low: int, high: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def auto_expand_enabled() -> bool:
    """Bật/tắt tự mở rộng. Mặc định bật; ``MENU_AUTO_EXPAND=false`` để tắt."""

    return _env_flag("MENU_AUTO_EXPAND", "true")


def expand_step() -> int:
    return _env_int("MENU_EXPAND_STEP", DEFAULT_EXPAND_STEP, low=1, high=50)


def expand_max_rows() -> int:
    return _env_int("MENU_EXPAND_MAX_ROWS", DEFAULT_EXPAND_MAX_ROWS, low=1, high=200)


@dataclass(frozen=True)
class MenuLayoutSnapshot:
    """Phần cấu trúc MENU cần cho việc mở rộng, đọc từ ``get_sheet_structure``."""

    sheet_id: int | None
    row_count: int | None
    selector_start_row: int | None
    selector_end_row: int | None
    panel_start_row: int | None
    product_start_row: int | None
    header_row: int | None
    validations: list[dict[str, Any]] = field(default_factory=list)
    formulas: list[dict[str, Any]] = field(default_factory=list)
    conditional_formats: list[dict[str, Any]] = field(default_factory=list)
    protected_ranges: list[dict[str, Any]] = field(default_factory=list)

    @property
    def selector_span(self) -> str:
        if self.selector_start_row is None or self.selector_end_row is None:
            return ""
        return f"B{self.selector_start_row}:B{self.selector_end_row}"


def _validation_texts(entry: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for value in entry.get("values") or []:
        text = value.get("userEnteredValue") if isinstance(value, dict) else value
        texts.append(str(text or ""))
    rule = entry.get("rule")
    condition = rule.get("condition") if isinstance(rule, dict) else None
    if isinstance(condition, dict):
        for value in condition.get("values") or []:
            text = value.get("userEnteredValue") if isinstance(value, dict) else value
            texts.append(str(text or ""))
    unique: list[str] = []
    for text in texts:
        if text not in unique:
            unique.append(text)
    return unique


def _selector_from_validations(
    validations: Sequence[dict[str, Any]],
) -> tuple[int | None, int | None]:
    """Vùng chọn lấy từ dropdown K38, không lấy từ công thức chéo sheet của J40."""

    for entry in validations:
        for text in _validation_texts(entry):
            match = _SPAN.search(text)
            if match is not None:
                return int(match.group("start")), int(match.group("end"))
    return None, None


def _first_product_row(values: Sequence[Sequence[Any]], after_row: int) -> int | None:
    for row_number, row in enumerate(values, start=1):
        if row_number <= after_row:
            continue
        label = str(row[0] if row else "").strip()
        if _PRODUCT_ID.match(label):
            return row_number
    return None


def read_layout_snapshot(
    gateway: Any,
    spreadsheet_id: str,
    headers: Sequence[str],
    *,
    tab: str = MENU_TAB,
) -> MenuLayoutSnapshot | None:
    """Đọc cấu trúc MENU. Gateway không hỗ trợ thì trả ``None`` (không mở rộng)."""

    reader = getattr(gateway, "get_sheet_structure", None)
    if not callable(reader):
        return None
    structure = reader(spreadsheet_id, tab)
    if not isinstance(structure, dict):
        return None
    baseline = menu_baseline_from_structure(structure, headers)
    capacity = MenuCapacity.from_baseline(baseline)
    validations = [
        item
        for item in (structure.get("data_validations") or [])
        if isinstance(item, dict)
    ]
    selector_start, selector_end = _selector_from_validations(validations)
    if selector_start is None or selector_end is None:
        selector_start = capacity.selector_min_row
        selector_end = capacity.selector_max_row
    values = [row for row in (structure.get("values") or []) if isinstance(row, list)]
    product_start = _first_product_row(values, baseline.header_row or 0)
    if product_start is None:
        product_start = selector_start
    grid_properties = structure.get("grid_properties") or {}
    row_count = grid_properties.get("rowCount")
    return MenuLayoutSnapshot(
        sheet_id=baseline.sheet_id,
        row_count=int(row_count) if isinstance(row_count, int) else None,
        selector_start_row=selector_start,
        selector_end_row=selector_end,
        panel_start_row=capacity.panel_start_row,
        product_start_row=product_start,
        header_row=baseline.header_row,
        validations=validations,
        formulas=[
            item for item in (structure.get("formulas") or []) if isinstance(item, dict)
        ],
        conditional_formats=[
            item
            for item in (structure.get("conditional_formats") or [])
            if isinstance(item, dict)
        ],
        protected_ranges=[
            item
            for item in (
                structure.get("protected_ranges_raw")
                or structure.get("protected_ranges")
                or []
            )
            if isinstance(item, dict)
        ],
    )


@dataclass(frozen=True)
class MenuExpansionPlan:
    """Kế hoạch chèn dòng: số dòng, điểm chèn, vùng chọn mới và requests đã dựng."""

    rows: int
    insert_at_row: int
    insert_index: int
    product_count: int
    zone_rows_before: int
    old_selector_start_row: int
    old_selector_end_row: int
    new_selector_end_row: int
    old_panel_start_row: int | None
    new_panel_start_row: int | None
    requests: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def new_zone_rows(self) -> int:
        return self.zone_rows_before + self.rows

    def summary(self) -> str:
        """Một dòng mô tả để ghi vào warning/Telegram, không chứa giá bán."""

        panel = ""
        if self.old_panel_start_row is not None and self.new_panel_start_row is not None:
            panel = (
                f", panel chi tiết dời dòng {self.old_panel_start_row}"
                f" → {self.new_panel_start_row}"
            )
        return (
            f"MENU tự mở rộng +{self.rows} dòng tại dòng {self.insert_at_row} "
            f"({self.zone_rows_before} → {self.new_zone_rows} chỗ sản phẩm): "
            f"vùng chọn B{self.old_selector_start_row}:B{self.old_selector_end_row} "
            f"→ B{self.old_selector_start_row}:B{self.new_selector_end_row}{panel}."
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "insert_at_row": self.insert_at_row,
            "insert_index": self.insert_index,
            "product_count": self.product_count,
            "zone_rows_before": self.zone_rows_before,
            "zone_rows_after": self.new_zone_rows,
            "old_selector_end_row": self.old_selector_end_row,
            "new_selector_end_row": self.new_selector_end_row,
            "old_panel_start_row": self.old_panel_start_row,
            "new_panel_start_row": self.new_panel_start_row,
            "request_count": len(self.requests),
            "summary": self.summary(),
        }


@dataclass(frozen=True)
class MenuCapacityManager:
    """Đếm chỗ sản phẩm đang có, so với số sản phẩm cần hiển thị, và lập kế hoạch mở rộng.

    ``grid`` là ảnh chụp A1:ZZ của MENU, dùng để biết dòng nào đang có sản phẩm.
    Vùng sản phẩm = ``product_start_row`` … ``selector_end_row`` (dòng cuối mà
    dropdown K38 còn đọc tới). Hàng trống *sau* dòng cuối vùng chọn không tính,
    vì sản phẩm ghi vào đó sẽ không chọn được trên MENU.
    """

    snapshot: MenuLayoutSnapshot
    grid: Sequence[Sequence[Any]] = ()

    def _row(self, row_number: int) -> Sequence[Any]:
        index = row_number - 1
        if 0 <= index < len(self.grid):
            return self.grid[index]
        return ()

    @property
    def zone_start_row(self) -> int | None:
        return self.snapshot.product_start_row

    @property
    def zone_end_row(self) -> int | None:
        return self.snapshot.selector_end_row

    @property
    def zone_rows(self) -> int:
        start = self.zone_start_row
        end = self.zone_end_row
        if start is None or end is None or end < start:
            return 0
        return end - start + 1

    def rows_in_zone(self) -> list[int]:
        start = self.zone_start_row
        end = self.zone_end_row
        if start is None or end is None or end < start:
            return []
        return list(range(start, end + 1))

    def product_rows(self) -> list[int]:
        """Dòng đang có sản phẩm: cột A có mã hoặc cột B có tên."""

        found: list[int] = []
        for row_number in self.rows_in_zone():
            row = self._row(row_number)
            label = str(row[0] if len(row) > 0 else "").strip()
            name = str(row[1] if len(row) > 1 else "").strip()
            if label or name:
                found.append(row_number)
        return found

    def blank_rows(self) -> list[int]:
        used = set(self.product_rows())
        return [row for row in self.rows_in_zone() if row not in used]

    def retired_rows(self) -> list[int]:
        """Dòng đã ngừng bán (SOLD OUT, STOCK 0) nên dùng lại được cho sản phẩm mới."""

        found: list[int] = []
        for row_number in self.product_rows():
            row = self._row(row_number)
            status = str(
                row[_STATUS_COLUMN] if len(row) > _STATUS_COLUMN else ""
            ).strip().upper()
            if status != "SOLD OUT":
                continue
            raw_stock = row[_STOCK_COLUMN] if len(row) > _STOCK_COLUMN else ""
            text = str(raw_stock).strip()
            if text and text not in {"0", "0.0"}:
                continue
            found.append(row_number)
        return found

    @property
    def used_rows(self) -> int:
        return len(self.product_rows())

    @property
    def free_rows(self) -> int:
        return len(self.blank_rows())

    def needed_rows(self, product_count: int) -> int:
        try:
            return max(0, int(product_count))
        except (TypeError, ValueError):
            return 0

    def deficit(self, product_count: int) -> int:
        """Số chỗ còn thiếu so với số sản phẩm cần hiển thị (0 nếu đủ)."""

        return max(0, self.needed_rows(product_count) - self.zone_rows)

    def describe(self, product_count: int | None = None) -> str:
        text = (
            f"Vùng sản phẩm dòng {self.zone_start_row}–{self.zone_end_row}: "
            f"{self.zone_rows} chỗ, đang dùng {self.used_rows}, trống {self.free_rows}, "
            f"ngừng bán {len(self.retired_rows())}."
        )
        if product_count is not None:
            text += (
                f" Cần hiển thị {self.needed_rows(product_count)} sản phẩm, "
                f"thiếu {self.deficit(product_count)} chỗ."
            )
        return text

    def insert_index(self) -> int | None:
        """Chỉ số 0-based của dòng đầu tiên bị dời xuống (ngay sau vùng sản phẩm)."""

        end = self.zone_end_row
        if end is None:
            return None
        panel = self.snapshot.panel_start_row
        limit = panel - 1 if panel is not None else end
        return max(0, min(end, limit))

    def _row_cap(self, rows: int) -> int:
        row_count = self.snapshot.row_count
        insert_index = self.insert_index()
        if row_count is None or insert_index is None:
            return rows
        headroom = row_count - insert_index - _GRID_TAIL_MARGIN
        return max(0, min(rows, headroom))

    def plan(
        self,
        product_count: int,
        *,
        step: int | None = None,
        max_rows: int | None = None,
        reason: str = "",
    ) -> MenuExpansionPlan | None:
        """Trả kế hoạch mở rộng khi thiếu chỗ, ``None`` khi còn đủ hoặc không đọc được layout."""

        if self.zone_start_row is None or self.zone_end_row is None:
            return None
        if self.snapshot.sheet_id is None:
            return None
        deficit = self.deficit(product_count)
        if deficit <= 0:
            return None
        size = max(1, int(step if step else expand_step()))
        limit = max(1, int(max_rows if max_rows else expand_max_rows()))
        rows = min(((deficit + size - 1) // size) * size, limit)
        rows = self._row_cap(rows)
        insert_index = self.insert_index()
        if rows <= 0 or insert_index is None:
            return None
        notes: list[str] = []
        if reason:
            notes.append(reason)
        if rows < deficit:
            notes.append(
                f"Chỉ chèn thêm {rows} dòng dù thiếu {deficit} chỗ; phần còn lại xử lý ở "
                "lần đồng bộ sau hoặc tăng MENU_EXPAND_MAX_ROWS."
            )
        old_start = int(self.snapshot.selector_start_row or self.zone_start_row)
        old_end = int(self.zone_end_row)
        new_end = old_end + rows
        panel = self.snapshot.panel_start_row
        requests = build_expansion_requests(
            self.snapshot,
            rows=rows,
            insert_index=insert_index,
            old_selector_start_row=old_start,
            old_selector_end_row=old_end,
            new_selector_end_row=new_end,
        )
        return MenuExpansionPlan(
            rows=rows,
            insert_at_row=insert_index + 1,
            insert_index=insert_index,
            product_count=self.needed_rows(product_count),
            zone_rows_before=self.zone_rows,
            old_selector_start_row=old_start,
            old_selector_end_row=old_end,
            new_selector_end_row=new_end,
            old_panel_start_row=panel,
            new_panel_start_row=panel + rows if panel is not None else None,
            requests=requests,
            notes=notes,
        )


def _replace_span(text: str, old_start: int, old_end: int, new_end: int) -> str:
    """Chỉ đổi đúng vùng ``B<đầu>:<cột><cuối>`` của MENU, giữ nguyên ``$`` và cột cuối."""

    def _swap(match: re.Match[str]) -> str:
        if int(match.group("start")) != old_start or int(match.group("end")) != old_end:
            return match.group(0)
        return (
            f"{match.group('prefix')}{match.group('start')}:"
            f"{match.group('end_column')}{new_end}"
        )

    return _SPAN.sub(_swap, text)


_SELECTOR_KEY_REF = re.compile(r"(?<![A-Za-z0-9_$])\$?K\$?(\d+)(?!\d)")


def _selector_ref_row(entries: Iterable[dict[str, Any]]) -> int | None:
    """Dòng ô chọn sản phẩm (dropdown cột K) trong validations, nếu có."""

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        position = _cell_position(entry.get("cell"))
        if position is not None and position[1] == column_index("K"):
            return position[0]
        rows = [int(row) for row in (entry.get("rows") or []) if isinstance(row, int)]
        head = str(entry.get("range") or "").split(":", 1)[0]
        if rows and head.upper().startswith("K"):
            return rows[0]
    return None


def _shift_selector_refs(formula: str, old_row: int | None, new_row: int | None) -> str:
    """Dời tham chiếu ô chọn ``K<dòng>`` trong công thức khi panel bị dời chỗ.

    Google tự dời tham chiếu khi chèn dòng, nhưng tool ghi lại công thức bằng chuỗi
    tường minh nên phải tự dời, nếu không VLOOKUP của panel vẫn trỏ ô chọn cũ.
    """

    if not formula or old_row is None or new_row is None or old_row == new_row:
        return formula

    def swap(match: re.Match[str]) -> str:
        if int(match.group(1)) != old_row:
            return match.group(0)
        return match.group(0).replace(str(old_row), str(new_row))

    return _SELECTOR_KEY_REF.sub(swap, formula)


def _cell_position(cell: object) -> tuple[int, int] | None:
    match = _CELL.match(str(cell or "").strip())
    if match is None:
        return None
    return int(match.group(2)), column_index(match.group(1))


def _shift_grid_range(
    grid_range: dict[str, Any], insert_index: int, rows: int
) -> dict[str, Any]:
    """Dời/căng GridRange theo dòng vừa chèn. Range nằm trên điểm chèn giữ nguyên."""

    start = grid_range.get("startRowIndex")
    end = grid_range.get("endRowIndex")
    if not isinstance(start, int) or not isinstance(end, int):
        return grid_range
    if end <= insert_index:
        return grid_range
    if start >= insert_index:
        return {**grid_range, "startRowIndex": start + rows, "endRowIndex": end + rows}
    return {**grid_range, "endRowIndex": end + rows}


def _insert_rows_request(sheet_id: int, insert_index: int, rows: int) -> dict[str, Any]:
    return {
        "insertDimension": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "ROWS",
                "startIndex": insert_index,
                "endIndex": insert_index + rows,
            },
            "inheritFromBefore": True,
        }
    }


def _validation_requests(
    snapshot: MenuLayoutSnapshot,
    *,
    sheet_id: int,
    rows: int,
    insert_at_row: int,
    old_start: int,
    old_end: int,
    new_end: int,
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for entry in snapshot.validations:
        texts = _validation_texts(entry)
        replaced = [_replace_span(text, old_start, old_end, new_end) for text in texts]
        if replaced == texts:
            continue
        position = _cell_position(entry.get("cell"))
        if position is None:
            continue
        row_number, column = position
        new_row = row_number + rows if row_number >= insert_at_row else row_number
        raw_rule = entry.get("rule") if isinstance(entry.get("rule"), dict) else None
        if raw_rule is None:
            rule: dict[str, Any] = {
                "condition": {
                    "type": str(entry.get("type") or "ONE_OF_RANGE"),
                    "values": [{"userEnteredValue": text} for text in replaced],
                },
                "showCustomUi": True,
            }
        else:
            rule = json.loads(json.dumps(raw_rule))
            condition = rule.get("condition")
            if not isinstance(condition, dict):
                condition = {}
            values = list(condition.get("values") or [])
            for item in values:
                if isinstance(item, dict) and isinstance(item.get("userEnteredValue"), str):
                    item["userEnteredValue"] = _replace_span(
                        item["userEnteredValue"], old_start, old_end, new_end
                    )
            rule["condition"] = {**condition, "values": values}
        requests.append(
            {
                "setDataValidation": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": new_row - 1,
                        "endRowIndex": new_row,
                        "startColumnIndex": column,
                        "endColumnIndex": column + 1,
                    },
                    "rule": rule,
                }
            }
        )
    return requests


def _formula_requests(
    snapshot: MenuLayoutSnapshot,
    *,
    sheet_id: int,
    rows: int,
    insert_at_row: int,
    old_start: int,
    old_end: int,
    new_end: int,
    selector_row: int | None = None,
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    new_selector_row = (
        selector_row + rows
        if selector_row is not None and selector_row >= insert_at_row
        else selector_row
    )
    for item in snapshot.formulas:
        formula = str(item.get("formula") or "")
        replaced = _replace_span(formula, old_start, old_end, new_end)
        replaced = _shift_selector_refs(replaced, selector_row, new_selector_row)
        if not formula or replaced == formula:
            continue
        position = _cell_position(item.get("cell"))
        if position is None:
            continue
        row_number, column = position
        new_row = row_number + rows if row_number >= insert_at_row else row_number
        requests.append(
            {
                "updateCells": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": new_row - 1,
                        "endRowIndex": new_row,
                        "startColumnIndex": column,
                        "endColumnIndex": column + 1,
                    },
                    "rows": [
                        {"values": [{"userEnteredValue": {"formulaValue": replaced}}]}
                    ],
                    "fields": "userEnteredValue.formulaValue",
                }
            }
        )
    return requests


def _conditional_requests(
    snapshot: MenuLayoutSnapshot,
    *,
    sheet_id: int,
    rows: int,
    insert_index: int,
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for index, rule in enumerate(snapshot.conditional_formats):
        ranges = [item for item in (rule.get("ranges") or []) if isinstance(item, dict)]
        shifted = [_shift_grid_range(item, insert_index, rows) for item in ranges]
        if shifted == ranges:
            continue
        requests.append(
            {
                "updateConditionalFormatRule": {
                    "sheetId": sheet_id,
                    "index": index,
                    "rule": {**rule, "ranges": shifted},
                }
            }
        )
    return requests


def _protected_requests(
    snapshot: MenuLayoutSnapshot,
    *,
    rows: int,
    insert_index: int,
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for item in snapshot.protected_ranges:
        protected_id = item.get("protectedRangeId")
        grid_range = item.get("range")
        if not isinstance(protected_id, int) or not isinstance(grid_range, dict):
            continue
        shifted = _shift_grid_range(grid_range, insert_index, rows)
        if shifted == grid_range:
            continue
        requests.append(
            {
                "updateProtectedRange": {
                    "protectedRange": {
                        "protectedRangeId": protected_id,
                        "range": shifted,
                        "warningOnly": bool(item.get("warningOnly")),
                        **(
                            {"description": str(item["description"])}
                            if item.get("description")
                            else {}
                        ),
                    },
                    "fields": "range",
                }
            }
        )
    return requests


def build_expansion_requests(
    snapshot: MenuLayoutSnapshot,
    *,
    rows: int,
    insert_index: int,
    old_selector_start_row: int,
    old_selector_end_row: int,
    new_selector_end_row: int,
) -> list[dict[str, Any]]:
    """Dựng batchUpdate: chèn dòng trước, rồi cập nhật theo toạ độ *sau* khi chèn."""

    sheet_id = snapshot.sheet_id
    if sheet_id is None or rows <= 0:
        return []
    insert_at_row = insert_index + 1
    requests = [_insert_rows_request(sheet_id, insert_index, rows)]
    selector_row = _selector_ref_row(snapshot.validations)
    requests.extend(
        _validation_requests(
            snapshot,
            sheet_id=sheet_id,
            rows=rows,
            insert_at_row=insert_at_row,
            old_start=old_selector_start_row,
            old_end=old_selector_end_row,
            new_end=new_selector_end_row,
        )
    )
    requests.extend(
        _formula_requests(
            snapshot,
            sheet_id=sheet_id,
            rows=rows,
            insert_at_row=insert_at_row,
            old_start=old_selector_start_row,
            old_end=old_selector_end_row,
            new_end=new_selector_end_row,
            selector_row=selector_row,
        )
    )
    requests.extend(
        _conditional_requests(
            snapshot, sheet_id=sheet_id, rows=rows, insert_index=insert_index
        )
    )
    requests.extend(_protected_requests(snapshot, rows=rows, insert_index=insert_index))
    return requests


def apply_menu_expansion(gateway: Any, spreadsheet_id: str, plan: MenuExpansionPlan) -> int:
    """Ghi batchUpdate mở rộng. Không ghi giá trị ô sản phẩm nào."""

    if not plan.requests:
        return 0
    gateway.batch_update(spreadsheet_id, plan.requests)
    return len(plan.requests)


def _shift_cell_name(cell: str, insert_at_row: int, rows: int) -> str:
    match = _CELL.match(str(cell or "").strip())
    if match is None:
        return cell
    row_number = int(match.group(2))
    if row_number < insert_at_row:
        return cell
    return f"{match.group(1)}{row_number + rows}"


def _shift_validation(
    item: dict[str, Any],
    *,
    insert_at_row: int,
    rows: int,
    old_start: int,
    old_end: int,
    new_end: int,
) -> dict[str, Any]:
    head = str(item.get("range") or "").split(":", 1)[0]
    match = _CELL.match(head)
    current_rows = [int(row) for row in (item.get("rows") or []) if isinstance(row, int)]
    if match is None or not current_rows:
        return item
    letter = match.group(1)
    shifted_rows = sorted(
        row + rows if row >= insert_at_row else row for row in current_rows
    )
    values: list[Any] = []
    for value in item.get("values") or []:
        if isinstance(value, dict):
            values.append(
                {
                    **value,
                    "userEnteredValue": _replace_span(
                        str(value.get("userEnteredValue") or ""),
                        old_start,
                        old_end,
                        new_end,
                    ),
                }
            )
        else:
            values.append(_replace_span(str(value), old_start, old_end, new_end))
    return {
        **item,
        "range": f"{letter}{shifted_rows[0]}:{letter}{shifted_rows[-1]}",
        "rows": shifted_rows,
        "values": values,
    }


def shift_menu_baseline(
    baseline: MenuBaseline,
    *,
    insert_index: int,
    rows: int,
    old_selector_start_row: int,
    old_selector_end_row: int,
    new_selector_end_row: int,
) -> MenuBaseline:
    """Baseline sau khi chèn ``rows`` dòng tại ``insert_index`` — để so với Google."""

    if rows <= 0:
        return baseline
    insert_at_row = insert_index + 1
    selector_row = _selector_ref_row(baseline.data_validations)
    new_selector_row = (
        selector_row + rows
        if selector_row is not None and selector_row >= insert_at_row
        else selector_row
    )
    merges = [
        {
            **merge,
            "startRowIndex": (
                merge["startRowIndex"] + rows
                if merge["startRowIndex"] >= insert_index
                else merge["startRowIndex"]
            ),
            "endRowIndex": (
                merge["endRowIndex"] + rows
                if merge["endRowIndex"] > insert_index
                else merge["endRowIndex"]
            ),
        }
        for merge in baseline.merged_ranges
    ]
    formulas = [
        {
            "cell": _shift_cell_name(str(item.get("cell") or ""), insert_at_row, rows),
            "formula": _shift_selector_refs(
                _replace_span(
                    str(item.get("formula") or ""),
                    old_selector_start_row,
                    old_selector_end_row,
                    new_selector_end_row,
                ),
                selector_row,
                new_selector_row,
            ),
        }
        for item in baseline.formulas
    ]
    validations = [
        _shift_validation(
            item,
            insert_at_row=insert_at_row,
            rows=rows,
            old_start=old_selector_start_row,
            old_end=old_selector_end_row,
            new_end=new_selector_end_row,
        )
        for item in baseline.data_validations
    ]
    heights: dict[str, int] = {}
    for key, value in baseline.row_heights.items():
        try:
            row_number = int(key)
        except (TypeError, ValueError):
            heights[str(key)] = value
            continue
        shifted = row_number + rows if row_number >= insert_at_row else row_number
        heights[str(shifted)] = value
    keys = ("startRowIndex", "endRowIndex", "startColumnIndex", "endColumnIndex")
    protected_ranges = [
        {
            **item,
            **_shift_grid_range(
                {key: item[key] for key in keys if key in item}, insert_index, rows
            ),
        }
        for item in baseline.protected_ranges
    ]
    return replace(
        baseline,
        merged_ranges=merges,
        formulas=formulas,
        data_validations=validations,
        row_heights=heights,
        protected_ranges=protected_ranges,
        data_rows=(
            baseline.data_rows + rows
            if baseline.data_rows >= insert_at_row
            else baseline.data_rows
        ),
    )


def _read_grid(gateway: Any, spreadsheet_id: str) -> list[list[Any]]:
    """Ảnh chụp A1:ZZ của MENU. Lỗi đọc chỉ làm mất phần đếm dòng đang dùng."""

    reader = getattr(gateway, "get_values", None)
    if not callable(reader):
        return []
    try:
        raw = reader(spreadsheet_id, f"'{MENU_TAB}'!A1:ZZ")
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    return [row for row in raw if isinstance(row, list)]


def auto_expand_menu(
    gateway: Any,
    spreadsheet_id: str,
    product_count: int,
    *,
    headers: Sequence[str],
    notes: list[str] | None = None,
    apply: bool = True,
) -> MenuExpansionPlan | None:
    """Đọc layout MENU, lập kế hoạch mở rộng rồi (mặc định) chèn dòng ngay.

    Trả ``None`` khi đang đủ chỗ, khi không đọc được cấu trúc, hoặc khi bị tắt
    bằng ``MENU_AUTO_EXPAND=false``. Không ghi giá trị ô sản phẩm nào: chỉ chèn
    dòng rỗng và căng đúng những vùng phụ thuộc dòng cuối vùng sản phẩm.
    """

    if not auto_expand_enabled():
        if notes is not None:
            notes.append("MENU_AUTO_EXPAND=false: giữ nguyên vùng sản phẩm.")
        return None
    snapshot = read_layout_snapshot(gateway, spreadsheet_id, headers)
    if snapshot is None:
        if notes is not None:
            notes.append(
                "Không đọc được cấu trúc MENU để tự mở rộng. Giữ nguyên vùng sản phẩm."
            )
        return None
    manager = MenuCapacityManager(snapshot, _read_grid(gateway, spreadsheet_id))
    plan = manager.plan(product_count, reason=f"Nguồn có {product_count} sản phẩm.")
    if plan is None:
        if notes is not None:
            notes.append(manager.describe(product_count))
        return None
    if not apply:
        if notes is not None:
            notes.append(
                "MENU cần mở rộng nhưng lượt này chỉ chạy thử: " + plan.summary()
            )
        return plan
    apply_menu_expansion(gateway, spreadsheet_id, plan)
    if notes is not None:
        notes.extend(plan.notes)
        notes.append(plan.summary())
    return plan


def menu_selector_span(
    baseline: MenuBaseline,
) -> tuple[int | None, int | None]:
    """Vùng chọn của dropdown trong baseline (không lấy ``B2:E26`` của J40)."""

    entries: list[dict[str, Any]] = [
        {"values": item.get("values") or []} for item in baseline.data_validations
    ]
    return _selector_from_validations(entries)


def menu_expansion_shift(
    baseline: MenuBaseline,
    current: MenuBaseline,
    *,
    max_rows: int = 200,
) -> int | None:
    """Số dòng MENU đã tự mở rộng nếu ``current`` chỉ là ``baseline`` dịch xuống.

    Trả ``None`` khi layout đổi khác, tức lần mở rộng trước không phải do tool này.
    """

    old_start, old_end = menu_selector_span(baseline)
    new_start, new_end = menu_selector_span(current)
    if old_start is None or old_end is None or new_start is None or new_end is None:
        return None
    if new_start != old_start:
        return None
    rows = new_end - old_end
    if rows <= 0 or rows > max_rows:
        return None
    capacity = MenuCapacity.from_baseline(baseline)
    panel = capacity.panel_start_row
    insert_index = min(old_end, panel - 1) if panel is not None else old_end
    shifted = shift_menu_baseline(
        baseline,
        insert_index=insert_index,
        rows=rows,
        old_selector_start_row=old_start,
        old_selector_end_row=old_end,
        new_selector_end_row=new_end,
    )
    return rows if not menu_structure_diff(shifted, current) else None
