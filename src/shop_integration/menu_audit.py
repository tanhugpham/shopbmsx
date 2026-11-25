"""Audit chỉ đọc MENU: vùng mở rộng A:H, dòng legacy ngoài K38, ProductID/VariantID.

Module này không ghi Google Sheets. Dùng cho `menu-audit` và test hồi quy Phase 4.3D.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable, Sequence

from shop_integration.combined_quote import (
    MENU_COLUMNS,
    SOURCE_MANUAL,
    SOURCE_SHOP,
    TAB_AUTO,
    is_category_header_row,
    is_menu_product_row,
    quote_layout_problems,
)
from shop_integration.errors import MenuLayoutError
from shop_integration.menu_layout import (
    MenuBaseline,
    MenuCapacity,
    menu_baseline_from_structure,
)

_SOURCE_LABELS = {SOURCE_SHOP.casefold(), SOURCE_MANUAL.casefold()}


def normalize_product_name(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def cell(row: Sequence[Any], column: int) -> str:
    if column >= len(row):
        return ""
    value = row[column]
    return "" if value is None else str(value).strip()


def data_empty(row: Sequence[Any]) -> bool:
    return all(not cell(row, column) for column in range(MENU_COLUMNS))


def price_number(value: object) -> int | None:
    text = (
        str(value or "")
        .replace("₫", "")
        .replace(".", "")
        .replace(",", "")
        .replace(" ", "")
        .strip()
    )
    return int(text) if text.isdigit() else None


def name_tokens(value: object) -> set[str]:
    return {token for token in normalize_product_name(value).split() if len(token) > 1}


def same_source_product(
    name: str,
    price: object,
    menu_entries: Sequence[tuple[str, int | None]],
) -> bool:
    """Khớp tên chính xác, hoặc tên đã đổi nhẹ + cùng giá. Không chỉ dựa vào tên."""

    wanted = normalize_product_name(name)
    wanted_tokens = name_tokens(name)
    wanted_price = price_number(price)
    for menu_name, menu_price in menu_entries:
        if wanted == menu_name:
            return True
        menu_tokens = name_tokens(menu_name)
        if (
            len(wanted_tokens) >= 3
            and wanted_tokens
            and wanted_tokens <= menu_tokens
            and (
                wanted_price is None
                or menu_price is None
                or wanted_price == menu_price
            )
        ):
            return True
        if (
            wanted_price is not None
            and menu_price == wanted_price
            and SequenceMatcher(None, wanted, menu_name).ratio() >= 0.55
        ):
            return True
    return False


def short_text(value: str, limit: int = 200) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


@dataclass
class LegacyMenuRow:
    row_number: int
    menu_id: str
    product_cell: str
    description_cell: str
    duration_cell: str
    warranty_cell: str
    price: str
    status: str
    matched_product_id: str = ""
    matched_variant_id: str = ""
    matched_name: str = ""
    duplicate_of: str = ""
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "row_number": self.row_number,
            "menu_id": self.menu_id,
            "product_cell": self.product_cell,
            "description_cell": self.description_cell,
            "duration_cell": self.duration_cell,
            "warranty_cell": short_text(self.warranty_cell),
            "price": self.price,
            "status": self.status,
            "matched_product_id": self.matched_product_id,
            "matched_variant_id": self.matched_variant_id,
            "matched_name": self.matched_name,
            "duplicate_of": self.duplicate_of,
            "reason": self.reason,
        }


@dataclass
class MissingSource:
    product_id: str
    variant_id: str
    name: str
    price: Any
    suggested_row: int | None
    reason: str = "Chưa có dòng MENU cùng tên sản phẩm."

    def as_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "variant_id": self.variant_id,
            "name": self.name,
            "price": self.price,
            "suggested_row": self.suggested_row,
            "reason": self.reason,
        }


@dataclass
class MenuAuditReport:
    header_row: int | None
    data_rows: int
    product_count: int
    last_product_row: int
    selector_span: str
    selector_max_row: int | None
    panel_start_row: int | None
    last_writable_row: int | None
    extension_rows: list[int] = field(default_factory=list)
    legacy_rows: list[LegacyMenuRow] = field(default_factory=list)
    missing_sources: list[MissingSource] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "header_row": self.header_row,
            "data_rows": self.data_rows,
            "product_count": self.product_count,
            "last_product_row": self.last_product_row,
            "selector_span": self.selector_span,
            "selector_max_row": self.selector_max_row,
            "panel_start_row": self.panel_start_row,
            "last_writable_row": self.last_writable_row,
            "extension_rows": self.extension_rows,
            "legacy_rows": [item.as_dict() for item in self.legacy_rows],
            "missing_sources": [item.as_dict() for item in self.missing_sources],
            "warnings": self.warnings,
        }

    def text(self) -> str:
        legacy_note = (
            f", trong đó {len(self.legacy_rows)} dòng legacy ngoài vùng chọn"
            if self.legacy_rows
            else ""
        )
        parts = [
            f"MENU audit: header dòng {self.header_row}, {self.product_count} dòng sản phẩm"
            f"{legacy_note}, dòng cuối {self.last_product_row}.",
            f"Vùng chọn K38/J39/J40: {self.selector_span or 'chưa đọc được'}.",
            f"Panel J:M bắt đầu dòng {self.panel_start_row}; A:H dùng được tới dòng "
            f"{self.last_writable_row}.",
            f"Hàng A:H còn trống trước panel: {self.extension_rows or 'không có'}.",
        ]
        for item in self.legacy_rows:
            parts.append(
                f"Dòng {item.row_number} {item.menu_id}: PRODUCT='{item.product_cell}', "
                f"DESCRIPTION='{item.description_cell}'. "
                f"Khớp {item.matched_product_id}/{item.matched_variant_id} "
                f"'{item.matched_name}', trùng {item.duplicate_of or 'không rõ'}. {item.reason}"
            )
        for item in self.missing_sources:
            parts.append(
                f"Thiếu trong MENU: {item.product_id}/{item.variant_id} "
                f"'{item.name}', giá {item.price}, gợi ý dòng {item.suggested_row}."
            )
        parts.extend(self.warnings)
        return " ".join(parts)


def audit_menu_grid(
    grid: list[list[Any]],
    header_row: int | None,
    capacity: MenuCapacity | None,
    source_rows: Iterable[dict[str, Any]],
) -> MenuAuditReport:
    """Đối chiếu MENU với dòng nguồn có ProductID/VariantID. Không ghi gì."""

    header = header_row or 0
    product_rows: list[int] = []
    for row_number in range(header + 1, len(grid) + 1):
        raw = grid[row_number - 1]
        if is_category_header_row(raw) or not is_menu_product_row(raw):
            continue
        product_rows.append(row_number)
    last_product = max(product_rows, default=0)
    selector_max = capacity.selector_max_row if capacity is not None else None
    last_writable = capacity.last_writable_row if capacity is not None else None
    extension: list[int] = []
    limit = last_writable if last_writable is not None else len(grid)
    start_row = (selector_max or header) + 1
    for row_number in range(start_row, min(len(grid), limit) + 1):
        raw = grid[row_number - 1]
        if is_menu_product_row(raw) or is_category_header_row(raw):
            continue
        if data_empty(raw) and (
            capacity is None
            or not any(
                capacity.cell_blocked(column, row_number) for column in range(MENU_COLUMNS)
            )
        ):
            extension.append(row_number)

    source_by_name: dict[str, list[dict[str, Any]]] = {}
    for row in source_rows:
        source_by_name.setdefault(normalize_product_name(row.get("PRODUCT")), []).append(row)

    menu_entries: list[tuple[str, int | None]] = []
    for row_number in product_rows:
        raw = grid[row_number - 1]
        name = normalize_product_name(cell(raw, 1))
        if name:
            menu_entries.append((name, price_number(cell(raw, 5))))

    legacy: list[LegacyMenuRow] = []
    legacy_names: set[str] = set()
    if selector_max is not None:
        for row_number in product_rows:
            if row_number <= selector_max:
                continue
            raw = grid[row_number - 1]
            product_cell = cell(raw, 1)
            description_cell = cell(raw, 2)
            misaligned = normalize_product_name(product_cell) in _SOURCE_LABELS
            candidate_key = normalize_product_name(
                description_cell if misaligned else product_cell
            )
            if candidate_key:
                legacy_names.add(candidate_key)
            matches = source_by_name.get(candidate_key, [])
            match = matches[0] if len(matches) == 1 else {}
            duplicate = next(
                (
                    cell(grid[other - 1], 0)
                    for other in product_rows
                    if other < row_number
                    and normalize_product_name(cell(grid[other - 1], 1)) == candidate_key
                ),
                "",
            )
            reason = ""
            if misaligned:
                reason = (
                    "Cột B đang là tên nguồn nên PRODUCT bị đẩy sang C; "
                    "đây là dòng lệch cột, không phải sản phẩm mới."
                )
            elif not match:
                reason = "Không đối chiếu được ProductID/VariantID từ tên hiển thị."
            legacy.append(
                LegacyMenuRow(
                    row_number=row_number,
                    menu_id=cell(raw, 0),
                    product_cell=product_cell,
                    description_cell=description_cell,
                    duration_cell=cell(raw, 3),
                    warranty_cell=cell(raw, 4),
                    price=cell(raw, 5),
                    status=cell(raw, 7),
                    matched_product_id=str(match.get("ProductID") or ""),
                    matched_variant_id=str(match.get("VariantID") or ""),
                    matched_name=str(match.get("PRODUCT") or ""),
                    duplicate_of=duplicate,
                    reason=reason,
                )
            )

    missing: list[MissingSource] = []
    for name, rows in source_by_name.items():
        if not name or name in legacy_names:
            continue
        for row in rows:
            if same_source_product(str(row.get("PRODUCT") or ""), row.get("PRICE"), menu_entries):
                continue
            missing.append(
                MissingSource(
                    product_id=str(row.get("ProductID") or ""),
                    variant_id=str(row.get("VariantID") or ""),
                    name=str(row.get("PRODUCT") or ""),
                    price=row.get("PRICE"),
                    suggested_row=extension[0] if extension else None,
                )
            )

    warnings: list[str] = []
    if extension and selector_max is not None and extension[0] > selector_max:
        span = capacity.selector_span if capacity is not None else ""
        warnings.append(
            f"Hàng trống {extension} nằm ngoài vùng chọn K38 {span}; "
            "phải mở rộng dropdown và công thức J39/J40 trước khi ghi."
        )
    return MenuAuditReport(
        header_row=header_row,
        data_rows=max(len(grid), 0),
        product_count=len(product_rows),
        last_product_row=last_product,
        selector_span=capacity.selector_span if capacity is not None else "",
        selector_max_row=selector_max,
        panel_start_row=capacity.panel_start_row if capacity is not None else None,
        last_writable_row=last_writable,
        extension_rows=extension,
        legacy_rows=legacy,
        missing_sources=missing,
        warnings=warnings,
    )


def read_menu_audit(
    gateway: Any,
    spreadsheet_id: str,
    headers: Sequence[str],
    source_rows: Iterable[dict[str, Any]],
) -> MenuAuditReport:
    """Đọc cấu trúc MENU qua gateway (read-only) rồi audit."""

    reader = getattr(gateway, "get_sheet_structure", None)
    if not callable(reader):
        raise MenuLayoutError("Gateway không đọc được cấu trúc MENU (thiếu get_sheet_structure).")
    structure = reader(spreadsheet_id, "MENU")
    baseline: MenuBaseline = menu_baseline_from_structure(structure, headers)
    capacity = MenuCapacity.from_baseline(baseline)
    report = audit_menu_grid(
        [list(row) for row in (structure.get("values") or [])],
        baseline.header_row,
        capacity,
        source_rows,
    )
    try:
        quote_grid = gateway.get_values(spreadsheet_id, f"'{TAB_AUTO}'!A1:ZZ")
    except Exception:
        quote_grid = None
    if quote_grid:
        report.warnings.extend(quote_layout_problems(quote_grid, tab=TAB_AUTO))
    return report
