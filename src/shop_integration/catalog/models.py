"""Catalog hai tầng cho người bán lại nhiều nhà cung cấp.

``SupplierProduct`` là dữ liệu thô đã chuẩn hóa của một adapter. ``StoreProduct``
là sản phẩm bán cho khách, độc lập với ProductID/VariantID của bất kỳ nhà cung
cấp nào. Mapping chỉ là thông tin nội bộ, không đi vào MENU.

Module này không gọi mạng và không ghi Google.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from shop_integration.errors import CatalogValidationError
from shop_integration.suppliers.models import NormalizedOffer, WholesaleTier

SALES_CONTACT = "@tanhung929"
PUBLISHED = "PUBLISHED"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
PUBLICATION_STATES = frozenset({PUBLISHED, REVIEW_REQUIRED})
CUSTOMER_MENU_HEADERS = (
    "ID",
    "PRODUCT",
    "DESCRIPTION",
    "DURATION",
    "WARRANTY",
    "PRICE",
    "STOCK",
    "STATUS",
)
# Chỉ các trường này được người vận hành khóa. Bảo hành luôn do nguồn quyết định
# để không vô tình thay đổi điều kiện kích hoạt/bảo hành bắt buộc.
LOCKABLE_CONTENT_FIELDS = frozenset(
    {"display_name", "display_description", "duration", "sales_content", "contact"}
)
_MENU_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _text(value: object) -> str:
    return str(value or "").strip()


def _optional_money(value: object, *, field_name: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CatalogValidationError(f"{field_name} phải là số nguyên VND không âm hoặc bỏ trống.")
    return value


def _optional_quantity(value: object, *, field_name: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CatalogValidationError(f"{field_name} phải là số nguyên không âm hoặc bỏ trống.")
    return value


@dataclass(frozen=True)
class SupplierProduct:
    """Một sản phẩm/biến thể đã chuẩn hóa từ một adapter nhà cung cấp."""

    supplier_id: str
    supplier_name: str
    supplier_product_id: str
    supplier_variant_id: str
    raw_product_name: str
    raw_description: str
    normalized_product_name: str
    cost_price_vnd: int | None
    stock_quantity: int | None
    availability: bool | None
    source_key: str = ""
    quantity_presence: str = "absent"
    available_presence: str = "absent"
    product_attributes: Mapping[str, str] = field(default_factory=dict)
    wholesale_tiers: tuple[WholesaleTier, ...] = ()
    fetched_at: str = ""
    raw_data_reference: str = ""
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        supplier_id = _text(self.supplier_id)
        supplier_name = _text(self.supplier_name)
        if not supplier_id:
            raise CatalogValidationError("SupplierProduct thiếu supplier_id.")
        if not supplier_name:
            raise CatalogValidationError("SupplierProduct thiếu supplier_name.")
        for name in ("supplier_product_id", "supplier_variant_id", "source_key"):
            object.__setattr__(self, name, _text(getattr(self, name)))
        object.__setattr__(self, "raw_product_name", _text(self.raw_product_name))
        object.__setattr__(self, "raw_description", str(self.raw_description or ""))
        object.__setattr__(
            self,
            "normalized_product_name",
            _text(self.normalized_product_name) or _text(self.raw_product_name),
        )
        object.__setattr__(
            self,
            "cost_price_vnd",
            _optional_money(self.cost_price_vnd, field_name="cost_price_vnd"),
        )
        object.__setattr__(
            self,
            "stock_quantity",
            _optional_quantity(self.stock_quantity, field_name="stock_quantity"),
        )
        if self.availability is not None and not isinstance(self.availability, bool):
            raise CatalogValidationError("availability phải là True, False hoặc None.")
        object.__setattr__(self, "product_attributes", dict(self.product_attributes or {}))
        object.__setattr__(self, "wholesale_tiers", tuple(self.wholesale_tiers or ()))
        object.__setattr__(self, "warnings", tuple(_text(item) for item in self.warnings if _text(item)))

    @property
    def identity_key(self) -> str:
        """Khóa nội bộ. Không dùng tên sản phẩm làm khóa duy nhất."""

        if self.supplier_product_id:
            return f"{self.supplier_id}|id|{self.supplier_product_id}|{self.supplier_variant_id}"
        if self.source_key:
            return f"{self.supplier_id}|key|{self.source_key}"
        return ""

    @property
    def identity_status(self) -> str:
        if self.supplier_product_id:
            return "external_id"
        if self.source_key:
            return "manual_key"
        return "unmapped"

    @property
    def is_identified(self) -> bool:
        return bool(self.identity_key)

    def to_offer(self) -> NormalizedOffer:
        """Chuyển sang mô hình cũ để tái dùng đối chiếu/chọn nguồn hiện có."""

        source_id = self.supplier_product_id or self.source_key
        return NormalizedOffer(
            supplier_id=self.supplier_id,
            supplier_name=self.supplier_name,
            source_product_id=source_id,
            source_variant_id=self.supplier_variant_id,
            source_product_name=self.raw_product_name or self.normalized_product_name,
            normalized_product_name=self.normalized_product_name,
            description=self.raw_description,
            product_attributes=dict(self.product_attributes),
            cost_price_vnd=self.cost_price_vnd,
            available=self.availability,
            available_quantity=self.stock_quantity,
            quantity_presence=self.quantity_presence,
            available_presence=self.available_presence,
            wholesale_tiers=list(self.wholesale_tiers),
            fetched_at=self.fetched_at,
            raw_data_reference=self.raw_data_reference,
            warnings=list(self.warnings),
        )

    @classmethod
    def from_offer(
        cls,
        offer: NormalizedOffer,
        *,
        source_key: str = "",
    ) -> "SupplierProduct":
        return cls(
            supplier_id=offer.supplier_id,
            supplier_name=offer.supplier_name,
            supplier_product_id=offer.source_product_id,
            supplier_variant_id=offer.source_variant_id,
            source_key=source_key,
            raw_product_name=offer.source_product_name,
            raw_description=offer.description,
            normalized_product_name=offer.normalized_product_name,
            cost_price_vnd=offer.cost_price_vnd,
            stock_quantity=offer.available_quantity,
            availability=offer.available,
            quantity_presence=offer.quantity_presence,
            available_presence=offer.available_presence,
            product_attributes=offer.product_attributes,
            wholesale_tiers=tuple(offer.wholesale_tiers),
            fetched_at=offer.fetched_at,
            raw_data_reference=offer.raw_data_reference,
            warnings=tuple(offer.warnings),
        )


@dataclass(frozen=True)
class SupplierMapping:
    """Mapping nội bộ từ nguồn sang store product.

    Phải có ID nguồn hoặc ``source_key`` do người vận hành/adapter kiểm soát.
    Tên sản phẩm không bao giờ là khóa duy nhất.
    """

    store_product_id: str
    supplier_id: str
    supplier_product_id: str = ""
    supplier_variant_id: str = ""
    source_key: str = ""
    approved: bool = False
    note: str = ""

    def __post_init__(self) -> None:
        store_id = _text(self.store_product_id)
        supplier_id = _text(self.supplier_id)
        product_id = _text(self.supplier_product_id)
        variant_id = _text(self.supplier_variant_id)
        source_key = _text(self.source_key)
        if not store_id:
            raise CatalogValidationError("SupplierMapping thiếu store_product_id.")
        if not supplier_id:
            raise CatalogValidationError("SupplierMapping thiếu supplier_id.")
        if bool(product_id) == bool(source_key):
            raise CatalogValidationError(
                "SupplierMapping phải có đúng một khóa: supplier_product_id hoặc source_key."
            )
        if variant_id and not product_id:
            raise CatalogValidationError("supplier_variant_id chỉ dùng khi có supplier_product_id.")
        object.__setattr__(self, "store_product_id", store_id)
        object.__setattr__(self, "supplier_id", supplier_id)
        object.__setattr__(self, "supplier_product_id", product_id)
        object.__setattr__(self, "supplier_variant_id", variant_id)
        object.__setattr__(self, "source_key", source_key)
        object.__setattr__(self, "note", _text(self.note))

    def matches(self, product: SupplierProduct) -> bool:
        if product.supplier_id != self.supplier_id:
            return False
        if self.supplier_product_id:
            if product.supplier_product_id != self.supplier_product_id:
                return False
            # Mapping một sản phẩm không được tự nhận mọi biến thể.
            return product.supplier_variant_id == self.supplier_variant_id
        return product.source_key == self.source_key


@dataclass(frozen=True)
class ContentOverride:
    """Nội dung người vận hành sở hữu. Trường đã khóa không bị API ghi đè."""

    locked_fields: frozenset[str] = frozenset()
    display_name: str | None = None
    display_description: str | None = None
    duration: str | None = None
    sales_content: str | None = None
    contact: str | None = None

    def __post_init__(self) -> None:
        locked = frozenset(_text(item) for item in self.locked_fields)
        unknown = locked - LOCKABLE_CONTENT_FIELDS
        if unknown:
            raise CatalogValidationError(
                "Trường khóa không hợp lệ: " + ", ".join(sorted(unknown))
            )
        object.__setattr__(self, "locked_fields", locked)
        for name in ("display_name", "display_description", "duration", "sales_content", "contact"):
            value = getattr(self, name)
            object.__setattr__(self, name, None if value is None else str(value).strip())

    def resolve(self, field_name: str, api_value: object, previous_value: object) -> str:
        """Trả giá trị cuối. Trường khóa giữ nội dung người vận hành."""

        if field_name not in LOCKABLE_CONTENT_FIELDS:
            raise CatalogValidationError(f"Không được override trường '{field_name}'.")
        previous = _text(previous_value)
        if field_name not in self.locked_fields:
            return _text(api_value) or previous
        locked_value = getattr(self, field_name)
        return _text(locked_value) or previous


@dataclass(frozen=True)
class StoreProduct:
    """Sản phẩm bán cho khách. Không chứa ProductID/VariantID của nhà cung cấp."""

    store_product_id: str
    display_name: str
    display_description: str = ""
    duration: str = ""
    warranty: str = ""
    selling_price: int | None = None
    stock_status: str = "Chưa có dữ liệu"
    stock_quantity: int | None = None
    contact: str = SALES_CONTACT
    sales_content: str = ""
    content_override: ContentOverride = field(default_factory=ContentOverride)
    mappings: tuple[SupplierMapping, ...] = ()
    publication_status: str = PUBLISHED
    menu_code: str = ""

    def __post_init__(self) -> None:
        store_id = _text(self.store_product_id)
        if not store_id:
            raise CatalogValidationError("StoreProduct thiếu store_product_id.")
        name = _text(self.display_name)
        if not name:
            raise CatalogValidationError("StoreProduct thiếu display_name.")
        menu_code = _text(self.menu_code)
        if menu_code and not _MENU_CODE.match(menu_code):
            raise CatalogValidationError(f"menu_code '{menu_code}' không hợp lệ.")
        status = _text(self.publication_status).upper() or PUBLISHED
        if status not in PUBLICATION_STATES:
            raise CatalogValidationError(f"publication_status '{status}' không hợp lệ.")
        object.__setattr__(self, "store_product_id", store_id)
        object.__setattr__(self, "display_name", name)
        object.__setattr__(self, "display_description", str(self.display_description or ""))
        object.__setattr__(self, "duration", _text(self.duration))
        object.__setattr__(self, "warranty", _text(self.warranty))
        object.__setattr__(
            self, "selling_price", _optional_money(self.selling_price, field_name="selling_price")
        )
        object.__setattr__(self, "stock_status", _text(self.stock_status) or "Chưa có dữ liệu")
        object.__setattr__(
            self,
            "stock_quantity",
            _optional_quantity(self.stock_quantity, field_name="stock_quantity"),
        )
        object.__setattr__(self, "contact", _text(self.contact) or SALES_CONTACT)
        object.__setattr__(self, "sales_content", str(self.sales_content or ""))
        object.__setattr__(self, "mappings", tuple(self.mappings or ()))
        object.__setattr__(self, "publication_status", status)
        object.__setattr__(self, "menu_code", menu_code)

    @property
    def public_id(self) -> str:
        return self.menu_code or self.store_product_id

    def customer_profile(self) -> dict[str, Any]:
        """Bản dành cho khách, không có mapping hay giá vốn."""

        return {
            "Display Name": self.display_name,
            "Description": self.display_description,
            "Duration": self.duration,
            "Warranty": self.warranty,
            "Selling Price": self.selling_price,
            "Stock Status": self.stock_status,
            "Contact": self.contact,
            "Sales Content": self.sales_content,
        }

    def customer_row(self) -> dict[str, Any]:
        """Đúng 8 cột MENU hiện tại. Không thêm cột nội bộ."""

        stock = self.stock_status
        quantity = self.stock_quantity
        unknown = "xác minh" in stock.casefold() or "chưa" in stock.casefold()
        if unknown:
            stock_cell: Any = ""
        elif "Hết" in stock:
            stock_cell = 0
        elif isinstance(quantity, int) and not isinstance(quantity, bool):
            stock_cell = quantity
        elif "Còn" in stock:
            stock_cell = "Còn hàng"
        else:
            stock_cell = ""
        qty = quantity if isinstance(quantity, int) and not isinstance(quantity, bool) else None
        if unknown:
            status = "CONTACT"
        elif "Hết" in stock or qty == 0:
            status = "SOLD OUT"
        elif qty is not None and qty > 5:
            status = "IN STOCK"
        elif qty is not None and 0 < qty <= 5:
            status = "LOW STOCK"
        elif "Còn" in stock:
            status = "IN STOCK"
        else:
            status = "CONTACT"
        return {
            "ID": self.public_id,
            "PRODUCT": self.display_name,
            "DESCRIPTION": self.display_description,
            "DURATION": self.duration,
            "WARRANTY": self.warranty,
            "PRICE": self.selling_price if self.selling_price is not None else "",
            "STOCK": stock_cell,
            "STATUS": status,
        }

@dataclass(frozen=True)
class SupplierFetch:
    supplier_id: str
    supplier_name: str
    products: tuple[SupplierProduct, ...] = ()
    error: str = ""
    blocked: bool = False
    warnings: tuple[str, ...] = ()
    catalog_status: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "supplier_id", _text(self.supplier_id))
        object.__setattr__(self, "supplier_name", _text(self.supplier_name) or self.supplier_id)
        object.__setattr__(self, "products", tuple(self.products or ()))
        object.__setattr__(self, "warnings", tuple(_text(item) for item in self.warnings if _text(item)))
        object.__setattr__(self, "error", _text(self.error))
        object.__setattr__(self, "catalog_status", _text(self.catalog_status))

    @property
    def ok(self) -> bool:
        return not self.error and not self.blocked


@dataclass(frozen=True)
class CatalogIssue:
    code: str
    message: str
    severity: str = "WARNING"
    store_product_id: str = ""
    supplier_id: str = ""
    supplier_product_id: str = ""
    supplier_variant_id: str = ""

    def __post_init__(self) -> None:
        severity = _text(self.severity).upper() or "WARNING"
        if severity not in {"INFO", "WARNING", "ERROR"}:
            raise CatalogValidationError(f"CatalogIssue severity '{severity}' không hợp lệ.")
        object.__setattr__(self, "code", _text(self.code).upper() or "UNKNOWN")
        object.__setattr__(self, "message", _text(self.message))
        object.__setattr__(self, "severity", severity)

    @property
    def blocking(self) -> bool:
        return self.severity == "ERROR"

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "store_product_id": self.store_product_id,
            "supplier_id": self.supplier_id,
            "supplier_product_id": self.supplier_product_id,
            "supplier_variant_id": self.supplier_variant_id,
        }


@dataclass
class CatalogPlan:
    """Kết quả một lượt smart sync. Chưa có thao tác ghi Google."""

    store_products: list[StoreProduct]
    supplier_products: list[SupplierProduct]
    issues: list[CatalogIssue] = field(default_factory=list)
    selected_sources: dict[str, SupplierProduct] = field(default_factory=dict)
    backup_sources: dict[str, SupplierProduct] = field(default_factory=dict)
    new_supplier_products: list[SupplierProduct] = field(default_factory=list)
    supplier_fetches: list[SupplierFetch] = field(default_factory=list)

    @property
    def published_products(self) -> list[StoreProduct]:
        return [item for item in self.store_products if item.publication_status == PUBLISHED]

    @property
    def review_products(self) -> list[StoreProduct]:
        return [item for item in self.store_products if item.publication_status == REVIEW_REQUIRED]

    @property
    def blocking_issues(self) -> list[CatalogIssue]:
        return [issue for issue in self.issues if issue.blocking]

    def customer_menu_rows(self, *, include_review: bool = False) -> list[dict[str, Any]]:
        from shop_integration.catalog.menu_projection import customer_menu_rows

        return customer_menu_rows(self.store_products, include_review=include_review)

    def text(self) -> str:
        parts = [
            f"catalog store={len(self.store_products)} supplier={len(self.supplier_products)}",
            f"published={len(self.published_products)} review={len(self.review_products)}",
            f"selected={len(self.selected_sources)} new={len(self.new_supplier_products)}",
            f"issues={len(self.issues)} blocking={len(self.blocking_issues)}",
        ]
        for fetch in self.supplier_fetches:
            if fetch.error:
                parts.append(f"{fetch.supplier_id}: lỗi {fetch.error}")
        for issue in self.issues[:40]:
            parts.append(f"{issue.code}: {issue.message}")
        return " ".join(part for part in parts if part)


def opaque_store_id(identity_key: str) -> str:
    """ID cửa hàng ổn định, không lộ supplier id hay tên sản phẩm."""

    digest = hashlib.sha256(identity_key.encode("utf-8")).hexdigest()[:12]
    return f"sp_{digest}"
