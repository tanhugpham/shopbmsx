"""Chiếu StoreProduct sang MENU khách, chỉ đúng các cột hiện có.

Không có ProductID, VariantID, giá vốn, mapping, JSON hay cấu hình backend.
Module này không ghi Google; người gọi chỉ nhận dict để kiểm tra/ghi qua guard.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from shop_integration.catalog.models import (
    CUSTOMER_MENU_HEADERS,
    PUBLISHED,
    StoreProduct,
)
from shop_integration.errors import CatalogValidationError


def customer_menu_rows(
    products: Sequence[StoreProduct],
    *,
    include_review: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for product in products:
        if not include_review and product.publication_status != PUBLISHED:
            continue
        row = product.customer_row()
        assert_customer_safe(row)
        menu_id = str(row["ID"]).strip()
        if menu_id in seen_ids:
            raise CatalogValidationError(
                f"Trùng ID MENU '{menu_id}' sau khi chiếu catalog. Không ghi để tránh đè dòng."
            )
        seen_ids.add(menu_id)
        rows.append(row)
    return rows


def assert_customer_safe(
    row: dict[str, Any],
    *,
    forbidden_values: Iterable[str] = (),
) -> None:
    """Chặn cột nội bộ và giá trị nguồn bị lộ vào MENU."""

    keys = tuple(row)
    if keys != CUSTOMER_MENU_HEADERS:
        extra = [key for key in keys if key not in CUSTOMER_MENU_HEADERS]
        missing = [key for key in CUSTOMER_MENU_HEADERS if key not in row]
        raise CatalogValidationError(
            f"MENU row sai cột. Thừa={extra or 'không'}, thiếu={missing or 'không'}."
        )
    forbidden = [str(item).strip() for item in forbidden_values if str(item).strip()]
    for key, value in row.items():
        if isinstance(value, (dict, list, tuple, set)):
            raise CatalogValidationError(f"MENU row cột {key} chứa cấu trúc nội bộ.")
        if isinstance(value, str):
            # Khóa nội bộ dạng supplier|product|variant không được xuất hiện ở MENU.
            if value.count("|") >= 2 or "shop_xoai_non|" in value or "nastele|" in value:
                raise CatalogValidationError(
                    f"MENU row cột {key} chứa khóa định danh nhà cung cấp. Không ghi."
                )
            for token in forbidden:
                if token in value:
                    raise CatalogValidationError(
                        f"MENU row cột {key} làm lộ giá trị nội bộ. Không ghi."
                    )


def validate_customer_rows(rows: Sequence[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for row in rows:
        assert_customer_safe(row)
        menu_id = str(row.get("ID") or "").strip()
        if not menu_id:
            raise CatalogValidationError("MENU row thiếu ID.")
        if menu_id in seen:
            raise CatalogValidationError(f"Trùng ID MENU '{menu_id}'.")
        seen.add(menu_id)
