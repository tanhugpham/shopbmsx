"""Đọc và ghi tab Google Sheets dùng chung cho mọi module.

Một chỗ duy nhất cho A1 range, đọc SheetTable và tạo tab. Không chứa nghiệp vụ.
"""

from __future__ import annotations

from typing import Any, Protocol

from shop_integration.sheet_layout import SheetTable, records_from_grid


class TabGateway(Protocol):
    def list_sheet_titles(self, spreadsheet_id: str) -> list[str]: ...

    def add_sheet(self, spreadsheet_id: str, title: str) -> None: ...

    def get_values(self, spreadsheet_id: str, range_a1: str) -> list[list[Any]]: ...

    def update_values(
        self, spreadsheet_id: str, range_a1: str, values: list[list[Any]]
    ) -> None: ...


def column_letter(index: int) -> str:
    """Chỉ số cột 0-based thành chữ cái A1. Không dùng cho cột âm."""

    if index < 0:
        raise ValueError("Chỉ số cột không được âm.")
    number = index + 1
    name = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        name = chr(65 + remainder) + name
    return name


def cell_range(tab: str, column: int, row: int) -> str:
    return f"'{tab}'!{column_letter(column)}{row}"


def tab_range(tab: str, columns: str = "A:ZZ") -> str:
    return f"'{tab}'!{columns}"


def list_titles(gateway: TabGateway, spreadsheet_id: str) -> set[str]:
    return set(gateway.list_sheet_titles(spreadsheet_id))


def read_grid(
    gateway: TabGateway,
    spreadsheet_id: str,
    tab: str,
    *,
    columns: str = "A:ZZ",
) -> list[list[Any]]:
    grid = gateway.get_values(spreadsheet_id, tab_range(tab, columns))
    if not isinstance(grid, list):
        raise ValueError(f"Tab {tab} trả về dữ liệu không hợp lệ.")
    return grid


def read_table(gateway: TabGateway, spreadsheet_id: str, tab: str) -> SheetTable:
    """Tab chưa có thì trả SheetTable rỗng. Không tự tạo tab khi chỉ đọc."""

    if tab not in list_titles(gateway, spreadsheet_id):
        return SheetTable()
    return records_from_grid(read_grid(gateway, spreadsheet_id, tab))


def ensure_tabs(
    gateway: TabGateway,
    spreadsheet_id: str,
    tabs: tuple[str, ...] | list[str],
    *,
    dry_run: bool,
) -> list[str]:
    """Trả danh sách tab còn thiếu. Dry-run chỉ liệt kê, không gọi addSheet."""

    titles = list_titles(gateway, spreadsheet_id)
    missing: list[str] = []
    for title in tabs:
        if title in titles:
            continue
        missing.append(title)
        if dry_run:
            continue
        gateway.add_sheet(spreadsheet_id, title)
        titles.add(title)
    return missing
