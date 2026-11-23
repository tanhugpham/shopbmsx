"""Quy tắc ghép dòng Google Sheets. Hàm thuần, không gọi mạng."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shop_integration.money import MoneyFormatError, parse_vnd
from shop_integration.text_normalize import FORMULA_PREFIX

PRICE_HISTORY_HEADERS = [
    "Thời điểm",
    "Product ID",
    "Variant ID",
    "Giá cũ (VND)",
    "Giá mới (VND)",
]

SYNC_LOG_HEADERS = [
    "Thời điểm",
    "Môi trường",
    "Dry run",
    "Số sản phẩm",
    "Số biến thể",
    "Số thay đổi giá",
    "Kết quả",
    "Ghi chú",
    "Số thêm mới",
    "Số cập nhật",
    "Số không đổi",
    "Lỗi",
]

MISSING_API_STATUS = "Không còn trong API"

CONFIG_HEADERS = ["Khóa", "Giá trị"]

TAB_PRODUCTS = "SAN_PHAM"
TAB_VARIANTS = "BIEN_THE"
TAB_CUSTOMER_PRICES = "BANG_GIA_BAN"
TAB_PRICE_HISTORY = "LICH_SU_GIA"
TAB_SYNC_LOG = "NHAT_KY_DONG_BO"
TAB_CONFIG = "CAU_HINH"
ALL_TABS = [
    TAB_PRODUCTS,
    TAB_VARIANTS,
    TAB_CUSTOMER_PRICES,
    TAB_PRICE_HISTORY,
    TAB_SYNC_LOG,
    TAB_CONFIG,
]

# Cột schema cũ được đổi tên khi ghép. Giá trị cột mới thắng nếu cả hai cùng có.
PRODUCT_HEADER_MAP = {
    "Tên sản phẩm": "Tên sản phẩm đã decode",
    "Trạng thái còn hàng": "Trạng thái hàng",
    "Giá thấp nhất (VND)": "Giá gốc thấp nhất",
    "Giá cao nhất (VND)": "Giá gốc cao nhất",
}
VARIANT_HEADER_MAP = {
    "Giá (VND)": "Giá gốc",
    "Số lượng khả dụng": "Số lượng tồn kho",
    "Mô tả biến thể": "Mô tả",
    "Nâng cấp thủ công": "Manual upgrade",
}
_COMPARE_SKIP = {"Ngày cập nhật", "STT"}
_COST_KEYS = ("Giá gốc", "Giá (VND)")


@dataclass
class SheetTable:
    headers: list[str] = field(default_factory=list)
    records: list[dict[str, Any]] = field(default_factory=list)
    row_count: int = 0


def records_from_grid(grid: list[list[Any]] | None) -> SheetTable:
    if not grid:
        return SheetTable(row_count=0)
    headers: list[str] = []
    for cell in grid[0]:
        headers.append(str(cell).strip())
    records: list[dict[str, Any]] = []
    for raw in grid[1:]:
        if not any(_non_empty(cell) for cell in raw):
            continue
        record: dict[str, Any] = {}
        for index, header in enumerate(headers):
            if not header or header in record:
                continue
            record[header] = raw[index] if index < len(raw) else ""
        records.append(record)
    return SheetTable(headers=headers, records=records, row_count=len(grid))


def grid_from_records(headers: list[str], records: list[dict[str, Any]]) -> list[list[Any]]:
    grid: list[list[Any]] = [list(headers)]
    for record in records:
        row: list[Any] = []
        for header in headers:
            row.append(_cell(record.get(header, "")))
        grid.append(row)
    return grid


def merge_by_keys(
    preferred_headers: list[str],
    existing: SheetTable,
    new_records: list[dict[str, Any]],
    key_fields: list[str],
    *,
    stamp_header: str,
    stamp_value: str,
    missing_status: str = MISSING_API_STATUS,
    preferred_first: bool = False,
) -> list[list[Any]]:
    headers = _extend_headers(
        existing.headers, preferred_headers, preferred_first=preferred_first
    )
    fresh: dict[tuple[str, ...], dict[str, Any]] = {}
    for record in new_records:
        fresh[_key(record, key_fields)] = record

    ordered: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for old in existing.records:
        key = _key(old, key_fields)
        if _meaningful(key) and key in fresh and key not in seen:
            combined = dict(old)
            combined.update(fresh[key])
            ordered.append(combined)
            seen.add(key)
        elif _meaningful(key) and key in seen:
            duplicate = dict(old)
            duplicate["Trạng thái đồng bộ"] = "Trùng khóa"
            duplicate[stamp_header] = stamp_value
            ordered.append(duplicate)
        elif _meaningful(key) and key not in fresh:
            kept = dict(old)
            kept["Trạng thái đồng bộ"] = missing_status
            kept[stamp_header] = stamp_value
            ordered.append(kept)
        else:
            ordered.append(dict(old))

    for record in new_records:
        key = _key(record, key_fields)
        if key not in seen:
            ordered.append(dict(record))
            seen.add(key)

    grid = grid_from_records(headers, ordered)
    width = len(headers)
    if existing.row_count > len(grid):
        grid.extend([[""] * width for _ in range(existing.row_count - len(grid))])
    return grid


@dataclass
class RowDiff:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    added_ids: list[str] = field(default_factory=list)
    updated_ids: list[str] = field(default_factory=list)
    missing_ids: list[str] = field(default_factory=list)


def classify_rows(
    existing_records: list[dict[str, Any]],
    new_records: list[dict[str, Any]],
    key_fields: list[str],
) -> RowDiff:
    """So sánh dòng theo khóa. Bỏ qua Ngày cập nhật. Không coi dòng thiếu là hết hàng."""

    old_map: dict[tuple[str, ...], dict[str, Any]] = {}
    for record in existing_records:
        key = _key(record, key_fields)
        if _meaningful(key) and key not in old_map:
            old_map[key] = record
    diff = RowDiff()
    fresh_keys: set[tuple[str, ...]] = set()
    for record in new_records:
        key = _key(record, key_fields)
        if not _meaningful(key) or key in fresh_keys:
            continue
        fresh_keys.add(key)
        label = key[0] if len(key) == 1 else " / ".join(key)
        old = old_map.get(key)
        if old is None:
            diff.added += 1
            diff.added_ids.append(label)
            continue
        if _business_equal(old, record):
            diff.unchanged += 1
        else:
            diff.updated += 1
            diff.updated_ids.append(label)
    for key in old_map:
        if key not in fresh_keys:
            label = key[0] if len(key) == 1 else " / ".join(key)
            diff.missing_ids.append(label)
    return diff


def diff_variant_prices(
    existing_records: list[dict[str, Any]],
    new_records: list[dict[str, Any]],
    timestamp: str,
) -> list[dict[str, Any]]:
    """So sánh giá gốc. Đọc cả cột cũ 'Giá (VND)' để lần đổi tên cột không tạo lịch sử giả."""

    old_prices: dict[tuple[str, str], int] = {}
    for record in existing_records:
        key = (_norm(record.get("Product ID")), _norm(record.get("Variant ID")))
        price = _source_cost(record)
        if key[0] and key[1] and price is not None:
            old_prices[key] = price
    changes: list[dict[str, Any]] = []
    for record in new_records:
        key = (_norm(record.get("Product ID")), _norm(record.get("Variant ID")))
        new_price = _source_cost(record)
        old_price = old_prices.get(key)
        if old_price is None or new_price is None or old_price == new_price:
            continue
        changes.append(
            {
                "Thời điểm": timestamp,
                "Product ID": key[0],
                "Variant ID": key[1],
                "Giá cũ (VND)": old_price,
                "Giá mới (VND)": new_price,
            }
        )
    return changes


def merge_catalog_tab(
    preferred_headers: list[str],
    header_map: dict[str, str],
    existing: SheetTable,
    new_records: list[dict[str, Any]],
    key_fields: list[str],
    *,
    stamp_header: str,
    stamp_value: str,
    missing_status: str = MISSING_API_STATUS,
) -> list[list[Any]]:
    """Đổi tên cột cũ rồi ghép. Cột người dùng lạ nằm cuối. STT đánh lại sau khi ghép."""

    remapped = SheetTable(
        headers=_remap_headers(existing.headers, header_map),
        records=[_remap_record(record, header_map) for record in existing.records],
        row_count=existing.row_count,
    )
    grid = merge_by_keys(
        preferred_headers,
        remapped,
        new_records,
        key_fields,
        stamp_header=stamp_header,
        stamp_value=stamp_value,
        missing_status=missing_status,
        preferred_first=True,
    )
    return stamp_stt(grid)


def rewrite_table(
    preferred_headers: list[str],
    existing: SheetTable,
    records: list[dict[str, Any]],
) -> list[list[Any]]:
    """Ghi lại cả tab từ danh sách mới. Giữ tên cột lạ, không ghép ghi chú theo dòng vì tab không có ID."""

    headers = list(preferred_headers)
    for header in existing.headers:
        if header and header not in headers:
            headers.append(header)
    grid = grid_from_records(headers, records)
    width = len(headers)
    if existing.row_count > len(grid):
        grid.extend([[""] * width for _ in range(existing.row_count - len(grid))])
    return stamp_stt(grid)


def stamp_stt(grid: list[list[Any]]) -> list[list[Any]]:
    if not grid:
        return grid
    headers = [str(cell).strip() for cell in grid[0]]
    if "STT" not in headers:
        return grid
    index = headers.index("STT")
    number = 1
    for row in grid[1:]:
        others = [cell for offset, cell in enumerate(row) if offset != index]
        if not any(_non_empty(cell) for cell in others):
            continue
        while len(row) <= index:
            row.append("")
        row[index] = number
        number += 1
    return grid


def merge_config(existing: SheetTable, pairs: list[tuple[str, str]]) -> list[list[Any]]:
    headers = _extend_headers(existing.headers, CONFIG_HEADERS)
    by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for record in existing.records:
        key = str(record.get("Khóa", "")).strip()
        if not key:
            continue
        if key not in by_key:
            order.append(key)
        by_key[key] = dict(record)
    for key, value in pairs:
        current = by_key.get(key, {})
        current["Khóa"] = key
        current["Giá trị"] = value
        if key not in by_key:
            order.append(key)
        by_key[key] = current
    records = [by_key[key] for key in order]
    grid = grid_from_records(headers, records)
    width = len(headers)
    if existing.row_count > len(grid):
        grid.extend([[""] * width for _ in range(existing.row_count - len(grid))])
    return grid


def _extend_headers(
    existing: list[str], preferred: list[str], *, preferred_first: bool = False
) -> list[str]:
    if not existing:
        return list(preferred)
    if preferred_first:
        headers = list(preferred)
        for header in existing:
            if header not in headers:
                headers.append(header)
        return headers
    headers = list(existing)
    for header in preferred:
        if header not in headers:
            headers.append(header)
    return headers


def _remap_headers(existing: list[str], header_map: dict[str, str]) -> list[str]:
    headers: list[str] = []
    for header in existing:
        canonical = header_map.get(header, header)
        if canonical not in headers:
            headers.append(canonical)
    return headers


def _remap_record(record: dict[str, Any], header_map: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in record.items():
        if key in header_map:
            continue
        result[key] = value
    for key, value in record.items():
        canonical = header_map.get(key)
        if canonical is None or canonical in result:
            continue
        result[canonical] = value
    return result


def _key(record: dict[str, Any], key_fields: list[str]) -> tuple[str, ...]:
    return tuple(_norm(record.get(field, "")) for field in key_fields)


def _meaningful(key: tuple[str, ...]) -> bool:
    return any(part for part in key)


def _business_equal(old: dict[str, Any], new: dict[str, Any]) -> bool:
    for header, value in new.items():
        if header in _COMPARE_SKIP:
            continue
        if _norm(old.get(header, "")) != _norm(value):
            return False
    return True


def _norm(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.startswith(FORMULA_PREFIX):
        text = text[1:].strip()
    return text


def _source_cost(record: dict[str, Any]) -> int | None:
    for key in _COST_KEYS:
        if key not in record:
            continue
        price = _optional_price(record.get(key))
        if price is not None:
            return price
    return None


def _non_empty(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _optional_price(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return parse_vnd(value)
    except MoneyFormatError:
        return None


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Có" if value else "Không"
    return value
