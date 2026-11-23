"""Hai adapter độc lập. Mỗi adapter kiểm tra dữ liệu của riêng nhà cung cấp."""

from __future__ import annotations

from typing import Any

from shop_integration.errors import ConfigError, IntegrationError
from shop_integration.money import MoneyFormatError, parse_optional_count, parse_vnd
from shop_integration.product_service import ProductService
from shop_integration.suppliers.models import (
    SUPPLIER_NAMES,
    SUPPLIER_NASTELE,
    SUPPLIER_SHOP,
    NormalizedOffer,
    ProviderResult,
    WholesaleTier,
)
from shop_integration.text_normalize import decode_escaped_whitespace, normalize_text, public_description

NO_VARIANT = "API không có biến thể"


class ShopXoaiNonProvider:
    supplier_id = SUPPLIER_SHOP

    def __init__(self, client: Any, *, products: ProductService | None = None, fetched_at: str) -> None:
        self._client = client
        self._products = products or ProductService()
        self._fetched_at = fetched_at
        self.warnings: list[str] = []

    def fetch(self) -> list[NormalizedOffer]:
        raw_items = self._client.list_all_products()
        parsed, parse_warnings = self._products.parse_many(raw_items)
        self.warnings.extend(item.message for item in parse_warnings)
        self.warnings.extend(getattr(self._client, "notices", []))
        offers: list[NormalizedOffer] = []
        for product in parsed:
            if "|" in product.id:
                self.warnings.append(f"Bỏ qua sản phẩm {product.id} vì mã chứa ký tự |.")
                continue
            if not product.variants:
                offers.append(self._without_variant(product))
                continue
            for variant in product.variants:
                if "|" in variant.id:
                    self.warnings.append(
                        f"Bỏ qua biến thể {product.id}/{variant.id} vì mã chứa ký tự |."
                    )
                    continue
                offers.append(self._from_variant(product, variant))
        return offers

    def _without_variant(self, product: Any) -> NormalizedOffer:
        raw_name = product.title_raw or ""
        available = product.in_stock if product.in_stock_presence == "value" else None
        return self._offer(
            product_id=product.id,
            variant_id="",
            raw_name=raw_name,
            description="",
            cost=None,
            available=available,
            available_presence=product.in_stock_presence,
            quantity=None,
            quantity_presence="absent",
            attributes={"bien_the": "Sản phẩm không có biến thể"},
            warnings=["Sản phẩm không có biến thể."],
        )

    def _from_variant(self, product: Any, variant: Any) -> NormalizedOffer:
        parts: list[str] = []
        if product.title_raw:
            parts.append(product.title_raw)
        if variant.title_presence == "value" and variant.title_raw:
            parts.append(variant.title_raw)
        raw_name = " — ".join(parts)
        description = ""
        if variant.description_presence == "value" and variant.description_raw:
            description = _clean(variant.description_raw, multiline=True)
        cost = variant.price_vnd if isinstance(variant.price_vnd, int) and variant.price_vnd > 0 else None
        warnings: list[str] = []
        if cost is None:
            warnings.append(variant.price_issue or "Thiếu giá gốc")
        available = variant.in_stock if variant.in_stock_presence == "value" else None
        attributes: dict[str, str] = {}
        if variant.manual_upgrade_presence == "value":
            if variant.manual_upgrade is True:
                attributes["manual_upgrade"] = "Có"
            elif variant.manual_upgrade is False:
                attributes["manual_upgrade"] = "Không"
            else:
                attributes["manual_upgrade"] = _clean(variant.manual_upgrade, multiline=False)
        return self._offer(
            product_id=product.id,
            variant_id=variant.id,
            raw_name=raw_name,
            description=description,
            cost=cost,
            available=available,
            available_presence=variant.in_stock_presence,
            quantity=variant.available_quantity if variant.quantity_presence == "value" else None,
            quantity_presence=variant.quantity_presence,
            attributes=attributes,
            warnings=warnings,
        )

    def _offer(
        self,
        *,
        product_id: str,
        variant_id: str,
        raw_name: str,
        description: str,
        cost: int | None,
        available: bool | None,
        available_presence: str,
        quantity: int | None,
        quantity_presence: str,
        attributes: dict[str, str],
        warnings: list[str],
    ) -> NormalizedOffer:
        return NormalizedOffer(
            supplier_id=SUPPLIER_SHOP,
            supplier_name=SUPPLIER_NAMES[SUPPLIER_SHOP],
            source_product_id=product_id,
            source_variant_id=variant_id,
            source_product_name=raw_name,
            normalized_product_name=_clean(raw_name, multiline=False),
            description=description,
            product_attributes=attributes,
            cost_price_vnd=cost,
            available=available,
            available_quantity=quantity,
            quantity_presence=quantity_presence,
            available_presence=available_presence,
            wholesale_tiers=[],
            fetched_at=self._fetched_at,
            raw_data_reference=f"shop_xoai_non:product:{product_id}:variant:{variant_id}",
            warnings=warnings,
        )


class NasteleProvider:
    supplier_id = SUPPLIER_NASTELE

    def __init__(self, client: Any, *, fetched_at: str) -> None:
        self._client = client
        self._fetched_at = fetched_at
        self.warnings: list[str] = []
        self.catalog_status = ""

    def fetch(self) -> list[NormalizedOffer]:
        """Dùng danh sách. Không gọi chi tiết từng mã vì trần 60 request/phút."""

        items = self._client.list_products()
        self.catalog_status = str(getattr(self._client, "catalog_status", "") or "")
        self.warnings.extend(getattr(self._client, "notices", []))
        offers: list[NormalizedOffer] = []
        index: dict[str, int] = {}
        for item in items:
            if not isinstance(item, dict):
                self.warnings.append("Bỏ qua phần tử Nastele không phải đối tượng.")
                continue
            offer = self._one(item)
            if offer is None:
                continue
            previous = index.get(offer.source_product_id)
            if previous is not None:
                self.warnings.append(
                    f"Mã Nastele {offer.source_product_id} xuất hiện hơn một lần. Giữ bản ghi cuối."
                )
                offer.warnings.append("Trùng mã nguồn. Giữ bản ghi cuối.")
                offers[previous] = offer
                continue
            index[offer.source_product_id] = len(offers)
            offers.append(offer)
        return offers

    def _one(self, item: dict[str, Any]) -> NormalizedOffer | None:
        product_id = _nastele_id(item.get("id"))
        if product_id is None:
            self.warnings.append("Bỏ qua sản phẩm Nastele thiếu id hợp lệ.")
            return None
        warnings: list[str] = []
        raw_name = item.get("name")
        if isinstance(raw_name, str):
            source_name = raw_name
        elif raw_name is None:
            source_name = ""
            warnings.append("Thiếu tên.")
        else:
            source_name = str(raw_name)
            warnings.append("Tên sản phẩm không phải chuỗi.")
        raw_description = item.get("description")
        if isinstance(raw_description, str):
            description = _clean(raw_description, multiline=True)
        elif raw_description is None:
            description = ""
        else:
            description = _clean(str(raw_description), multiline=True)
            warnings.append("Mô tả không phải chuỗi.")
        cost = _price(item, product_id, warnings)
        available, quantity, presence, stock_warning = _stock(item)
        if stock_warning:
            warnings.append(stock_warning)
        category = _category(item, warnings)
        return NormalizedOffer(
            supplier_id=SUPPLIER_NASTELE,
            supplier_name=SUPPLIER_NAMES[SUPPLIER_NASTELE],
            source_product_id=product_id,
            source_variant_id="",
            source_product_name=source_name,
            normalized_product_name=_clean(source_name, multiline=False),
            description=description,
            product_attributes={"bien_the": NO_VARIANT},
            cost_price_vnd=cost,
            available=available,
            available_quantity=quantity,
            quantity_presence=presence,
            available_presence=presence,
            wholesale_tiers=_tiers(item.get("wholesale_tiers"), product_id, warnings),
            fetched_at=self._fetched_at,
            raw_data_reference=f"nastele:product:{product_id}",
            category_id=category,
            warnings=warnings,
        )


def safe_fetch(provider: Any) -> ProviderResult:
    """Lỗi của một adapter không xóa dữ liệu adapter kia. Người gọi gộp kết quả."""

    supplier_id = str(getattr(provider, "supplier_id", ""))
    try:
        offers = provider.fetch()
    except ConfigError as exc:
        text = str(exc)
        return ProviderResult(
            supplier_id,
            [],
            error=text,
            blocked="API_KEY" in text,
            warnings=list(getattr(provider, "warnings", [])),
        )
    except IntegrationError as exc:
        return ProviderResult(
            supplier_id,
            [],
            error=str(exc),
            blocked=False,
            warnings=list(getattr(provider, "warnings", [])),
        )
    except Exception:
        return ProviderResult(
            supplier_id,
            [],
            error=f"Lỗi không xác định khi đọc {supplier_id}.",
            blocked=False,
        )
    return ProviderResult(
        supplier_id,
        offers,
        warnings=list(getattr(provider, "warnings", [])),
        catalog_status=str(getattr(provider, "catalog_status", "") or ""),
    )


def _clean(value: object, *, multiline: bool) -> str:
    return public_description(normalize_text(decode_escaped_whitespace(value), multiline=multiline))


def _nastele_id(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        if value < 0:
            return None
        return str(value)
    if isinstance(value, float):
        if value < 0 or not value.is_integer():
            return None
        return str(int(value))
    if isinstance(value, str) and value.strip().isdigit():
        return str(int(value.strip()))
    return None


def _price(item: dict[str, Any], product_id: str, warnings: list[str]) -> int | None:
    if "price" not in item or item.get("price") is None:
        warnings.append(f"Sản phẩm {product_id} thiếu giá.")
        return None
    try:
        amount = parse_vnd(item.get("price"))
    except MoneyFormatError:
        warnings.append(f"Sản phẩm {product_id} có giá không hợp lệ.")
        return None
    if amount <= 0:
        warnings.append(f"Sản phẩm {product_id} có giá không hợp lệ.")
        return None
    return amount


def _stock(item: dict[str, Any]) -> tuple[bool | None, int | None, str, str | None]:
    if "stock" not in item:
        return None, None, "absent", None
    raw = item.get("stock")
    if raw is None:
        return None, None, "null", None
    try:
        quantity = parse_optional_count(raw)
    except MoneyFormatError:
        return None, None, "invalid", "Số lượng stock không hợp lệ."
    if quantity is None:
        return None, None, "null", None
    return quantity > 0, quantity, "value", None


def _category(item: dict[str, Any], warnings: list[str]) -> str | None:
    if "category_id" not in item or item.get("category_id") is None:
        return None
    value = item.get("category_id")
    if isinstance(value, bool):
        warnings.append("category_id không hợp lệ.")
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    warnings.append("category_id không hợp lệ.")
    return None


def _tiers(raw: object, product_id: str, warnings: list[str]) -> list[WholesaleTier]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        warnings.append(f"Sản phẩm {product_id} có wholesale_tiers không phải mảng.")
        return []
    tiers: list[WholesaleTier] = []
    for item in raw:
        if not isinstance(item, dict) or "minQuantity" not in item or "unitPrice" not in item:
            warnings.append(f"Bỏ bậc sỉ không hợp lệ của sản phẩm {product_id}.")
            continue
        try:
            minimum = parse_vnd(item.get("minQuantity"))
            unit = parse_vnd(item.get("unitPrice"))
        except MoneyFormatError:
            warnings.append(f"Bỏ bậc sỉ không hợp lệ của sản phẩm {product_id}.")
            continue
        if minimum < 1 or unit <= 0:
            warnings.append(f"Bỏ bậc sỉ không hợp lệ của sản phẩm {product_id}.")
            continue
        tiers.append(WholesaleTier(minimum, unit))
    return tiers
