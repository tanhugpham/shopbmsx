"""Mô hình dữ liệu.

Chỉ lưu trường API thực sự trả về. Không bịa mô tả.
title, description và manual_upgrade của biến thể chỉ được điền khi khóa đó có trong JSON.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Variant(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    price_vnd: int | None = None
    in_stock: bool | None = None
    in_stock_presence: str = "absent"
    available_quantity: int | None = None
    quantity_presence: str = "absent"
    price_issue: str | None = None
    title: str | None = None
    title_raw: str | None = None
    title_presence: str = "absent"
    description: str | None = None
    description_raw: str | None = None
    description_presence: str = "absent"
    manual_upgrade: str | bool | None = None
    manual_upgrade_presence: str = "absent"


class Product(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    title: str | None = None
    title_raw: str | None = None
    in_stock: bool | None = None
    in_stock_presence: str = "absent"
    variants: list[Variant] = Field(default_factory=list)


class ParseWarning(BaseModel):
    product_id: str | None = None
    message: str


class Quote(BaseModel):
    """Các trường trong ví dụ POST /products/{id}/quote."""

    model_config = ConfigDict(extra="ignore")

    product_id: str | None = None
    quantity: int | None = None
    unit_price: int
    total: int
    available_quantity: int | None = None
    can_purchase: bool | None = None


class OrderView(BaseModel):
    """Chỉ giữ mã đơn và trạng thái. Mọi trường giao hàng bị loại."""

    model_config = ConfigDict(extra="ignore")

    order_id: str | None = None
    status: str | None = None


class MeSummary(BaseModel):
    """GET /me chưa có schema. Chỉ lưu tên khóa, không lưu giá trị."""

    top_level_keys: list[str]
    data_keys: list[str]


class WalletSummary(BaseModel):
    """GET /wallet/balance chưa có schema chính thức.

    amount chỉ được điền khi phản hồi có đúng một số nguyên. Đây là suy đoán
    tạm thời và luôn được đánh dấu confirmed=False.
    """

    amount: int | None = None
    amount_key: str | None = None
    data_keys: list[str]
    confirmed: bool = False


class PriceChange(BaseModel):
    model_config = ConfigDict(extra="ignore")

    product_id: str
    variant_id: str
    old_price: int
    new_price: int
    changed_at: str


class CatalogSummary(BaseModel):
    product_count: int = 0
    variant_count: int = 0
    in_stock: int = 0
    out_of_stock: int = 0
    unknown_stock: int = 0
    missing_price: int = 0


class SyncReport(BaseModel):
    started_at: str
    finished_at: str
    environment: str
    dry_run: bool
    summary: CatalogSummary
    price_changes: list[PriceChange] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    sheets_written: bool = False
    doc_written: bool = False
    sheets_note: str = ""
    doc_note: str = ""
    text: str = ""
    content_id: str = ""
    product_lines: list[str] = Field(default_factory=list)
    added_products: list[str] = Field(default_factory=list)
    missing_products: list[str] = Field(default_factory=list)
    added_count: int = 0
    updated_count: int = 0
    unchanged_count: int = 0

    def public_dict(self) -> dict[str, Any]:
        return self.model_dump()
