"""Chuyển JSON sản phẩm đã xác nhận thành dòng bảng. Không bịa mô tả."""

from __future__ import annotations

from typing import Any

from shop_integration.models import CatalogSummary, ParseWarning, Product, Variant
from shop_integration.money import MoneyFormatError, parse_optional_count, parse_vnd
from shop_integration.pricing import PricingPolicy, quote_cost
from shop_integration.text_normalize import normalize_text, shorten_text

MISSING_TEXT = "Chưa có dữ liệu"
SHORT_DESCRIPTION_LIMIT = 180

PRODUCT_HEADERS = [
    "STT",
    "Product ID",
    "Tên sản phẩm đã decode",
    "Mô tả ngắn",
    "Số biến thể",
    "Giá gốc thấp nhất",
    "Giá gốc cao nhất",
    "Giá bán thấp nhất",
    "Giá bán cao nhất",
    "Trạng thái hàng",
    "Ngày cập nhật",
    "Trạng thái đồng bộ",
    "Tên gốc",
]

VARIANT_HEADERS = [
    "STT",
    "Product ID",
    "Variant ID",
    "Tên sản phẩm",
    "Tên biến thể",
    "Mô tả",
    "Giá gốc",
    "Tỷ lệ lợi nhuận (%)",
    "Lợi nhuận (VND)",
    "Giá bán đề xuất (VND)",
    "Trạng thái còn hàng",
    "Số lượng tồn kho",
    "Manual upgrade",
    "Ngày cập nhật",
    "Trạng thái đồng bộ",
    "Tên gốc",
    "Mô tả gốc",
]

CUSTOMER_PRICE_HEADERS = [
    "STT",
    "Tên sản phẩm",
    "Tên biến thể",
    "Giá bán",
    "Tình trạng hàng",
    "Ngày cập nhật",
]

ABSENT_LABEL = "Không được cung cấp"
NULL_LABEL = "null"


def stock_label(in_stock: bool | None, variant_flags: list[bool] | None = None) -> str:
    if in_stock is True:
        return "Còn hàng"
    if in_stock is False:
        return "Hết hàng"
    flags = variant_flags or []
    if not flags:
        return "Không rõ"
    return "Còn hàng" if any(flags) else "Hết hàng"


def product_stock_label(product: Product) -> str:
    if product.in_stock_presence == "value":
        return "Còn hàng" if product.in_stock else "Hết hàng"
    if product.in_stock_presence == "null":
        return NULL_LABEL
    if product.in_stock_presence == "invalid":
        return "Không rõ"
    flags = [
        variant.in_stock
        for variant in product.variants
        if variant.in_stock_presence == "value" and variant.in_stock is not None
    ]
    if not flags:
        return ABSENT_LABEL
    return "Còn hàng" if any(flags) else "Hết hàng"


def variant_stock_label(variant: Variant) -> str:
    if variant.in_stock_presence == "value":
        return "Còn hàng" if variant.in_stock else "Hết hàng"
    if variant.in_stock_presence == "null":
        return NULL_LABEL
    if variant.in_stock_presence == "invalid":
        return "Không rõ"
    return ABSENT_LABEL


def quantity_cell(variant: Variant) -> Any:
    if variant.quantity_presence == "value":
        return variant.available_quantity
    if variant.quantity_presence == "null":
        return NULL_LABEL
    if variant.quantity_presence == "invalid":
        return "Không rõ"
    return ABSENT_LABEL


def resolve_variant_headers(existing_headers: list[str], products: list[Product]) -> list[str]:
    """Schema BIEN_THE cố định. Cột thiếu dữ liệu ghi 'Chưa có dữ liệu', không bỏ cột."""

    del existing_headers, products
    return list(VARIANT_HEADERS)


def price_bounds(product: Product) -> tuple[int | None, int | None]:
    prices = [
        variant.price_vnd
        for variant in product.variants
        if variant.price_vnd is not None and variant.price_vnd > 0
    ]
    if not prices:
        return None, None
    return min(prices), max(prices)


def selling_bounds(
    product: Product, policy: PricingPolicy | None = None
) -> tuple[int | None, int | None]:
    rules = policy or PricingPolicy()
    prices = [
        quote.selling
        for quote in (quote_cost(variant.price_vnd, rules) for variant in product.variants)
        if quote.selling is not None
    ]
    if not prices:
        return None, None
    return min(prices), max(prices)


def product_sync_status(product: Product) -> str:
    issues: list[str] = []
    if not product.title:
        issues.append("Thiếu tên")
    if not product.variants:
        issues.append("Thiếu biến thể")
    elif not any(
        variant.price_vnd is not None and variant.price_vnd > 0 for variant in product.variants
    ):
        issues.append("Thiếu giá")
    return ", ".join(issues) if issues else "Thành công"


def variant_sync_status(variant: Variant) -> str:
    if variant.price_vnd is None or variant.price_vnd <= 0:
        return "Thiếu giá"
    return "Thành công"


def short_description(product: Product) -> str:
    """Một mô tả biến thể duy nhất thì rút gọn. Nhiều nội dung khác nhau thì không chọn hộ."""

    unique: list[str] = []
    for variant in product.variants:
        text = (variant.description or "").strip()
        if variant.description_presence != "value" or not text:
            continue
        if text not in unique:
            unique.append(text)
    if len(unique) != 1:
        return MISSING_TEXT
    shortened = shorten_text(unique[0], SHORT_DESCRIPTION_LIMIT)
    return shortened or MISSING_TEXT


class ProductService:
    def parse_many(self, raw_items: list[Any]) -> tuple[list[Product], list[ParseWarning]]:
        products: list[Product] = []
        warnings: list[ParseWarning] = []
        seen: set[str] = set()
        for item in raw_items:
            product, item_warnings = self.parse_one(item)
            warnings.extend(item_warnings)
            if product is None:
                continue
            if product.id in seen:
                warnings.append(
                    ParseWarning(
                        product_id=product.id,
                        message=f"Mã sản phẩm {product.id} xuất hiện hơn một lần. Giữ bản ghi cuối.",
                    )
                )
                products = [current for current in products if current.id != product.id]
            seen.add(product.id)
            products.append(product)
        return products, warnings

    def parse_one(self, item: Any) -> tuple[Product | None, list[ParseWarning]]:
        warnings: list[ParseWarning] = []
        if not isinstance(item, dict):
            return None, [ParseWarning(message="Bỏ qua phần tử sản phẩm không phải đối tượng JSON.")]
        raw_id = item.get("id")
        if raw_id is None or (isinstance(raw_id, str) and not raw_id.strip()):
            return None, [ParseWarning(message="Bỏ qua sản phẩm thiếu id.")]
        if not isinstance(raw_id, str):
            product_id = str(raw_id).strip()
            warnings.append(
                ParseWarning(
                    product_id=product_id,
                    message=f"Mã sản phẩm {product_id} không phải chuỗi. Đã giữ dạng chữ.",
                )
            )
        else:
            product_id = raw_id.strip()
        raw_title = item.get("title")
        title_raw: str | None
        if raw_title is None:
            title_raw = None
            title_text = None
        else:
            if isinstance(raw_title, str):
                title_raw = raw_title
            else:
                title_raw = str(raw_title)
                warnings.append(
                    ParseWarning(product_id=product_id, message="Tên sản phẩm không phải chuỗi.")
                )
            title_text = normalize_text(title_raw, multiline=False) or None
        in_stock_presence, in_stock, stock_issue = _read_flag(item, "in_stock")
        if stock_issue:
            warnings.append(ParseWarning(product_id=product_id, message=stock_issue))
        variants, variant_warnings = self._parse_variants(product_id, item.get("variants"))
        warnings.extend(variant_warnings)
        return (
            Product(
                id=product_id,
                title=title_text,
                title_raw=title_raw,
                in_stock=in_stock,
                in_stock_presence=in_stock_presence,
                variants=variants,
            ),
            warnings,
        )

    def _parse_variants(
        self, product_id: str, raw_variants: Any
    ) -> tuple[list[Variant], list[ParseWarning]]:
        warnings: list[ParseWarning] = []
        if raw_variants is None:
            warnings.append(
                ParseWarning(product_id=product_id, message="Sản phẩm không có mảng variants.")
            )
            return [], warnings
        if not isinstance(raw_variants, list):
            warnings.append(
                ParseWarning(product_id=product_id, message="Trường variants không phải mảng.")
            )
            return [], warnings
        variants: list[Variant] = []
        seen: set[str] = set()
        for raw in raw_variants:
            variant, warning = self._parse_variant(product_id, raw)
            if warning is not None:
                warnings.append(warning)
            if variant is None:
                continue
            if variant.id in seen:
                warnings.append(
                    ParseWarning(
                        product_id=product_id,
                        message=f"Mã biến thể {variant.id} bị trùng. Giữ bản ghi cuối.",
                    )
                )
                variants = [current for current in variants if current.id != variant.id]
            seen.add(variant.id)
            variants.append(variant)
        return variants, warnings

    def _parse_variant(
        self, product_id: str, raw: Any
    ) -> tuple[Variant | None, ParseWarning | None]:
        if not isinstance(raw, dict):
            return None, ParseWarning(
                product_id=product_id,
                message="Bỏ qua biến thể không phải đối tượng JSON.",
            )
        raw_id = raw.get("id")
        if raw_id is None or (isinstance(raw_id, str) and not raw_id.strip()):
            return None, ParseWarning(product_id=product_id, message="Bỏ qua biến thể thiếu id.")
        variant_id = raw_id.strip() if isinstance(raw_id, str) else str(raw_id).strip()
        price, price_issue = _read_price(raw.get("prices"), variant_id)
        in_stock_presence, in_stock, stock_issue = _read_flag(raw, "in_stock")
        quantity_presence, quantity, quantity_issue = _read_quantity(raw, variant_id)
        title_presence, title_raw = _read_text(raw, "title")
        description_presence, description_raw = _read_text(raw, "description")
        title = _cleaned_text(title_presence, title_raw, multiline=False)
        description = _cleaned_text(description_presence, description_raw, multiline=True)
        manual_presence, manual = _read_manual(raw)
        warning_text = price_issue or quantity_issue or stock_issue
        warning = (
            ParseWarning(product_id=product_id, message=warning_text) if warning_text else None
        )
        return (
            Variant(
                id=variant_id,
                price_vnd=price,
                in_stock=in_stock,
                in_stock_presence=in_stock_presence,
                available_quantity=quantity,
                quantity_presence=quantity_presence,
                price_issue=price_issue,
                title=title,
                title_raw=title_raw,
                title_presence=title_presence,
                description=description,
                description_raw=description_raw,
                description_presence=description_presence,
                manual_upgrade=manual,
                manual_upgrade_presence=manual_presence,
            ),
            warning,
        )

    def build_product_records(
        self,
        products: list[Product],
        updated_at: str,
        policy: PricingPolicy | None = None,
    ) -> list[dict[str, Any]]:
        rules = policy or PricingPolicy()
        records: list[dict[str, Any]] = []
        for product in products:
            low, high = price_bounds(product)
            sell_low, sell_high = selling_bounds(product, rules)
            records.append(
                _ordered(
                    PRODUCT_HEADERS,
                    {
                        "Product ID": product.id,
                        "Tên sản phẩm đã decode": product.title or MISSING_TEXT,
                        "Mô tả ngắn": short_description(product),
                        "Số biến thể": len(product.variants),
                        "Giá gốc thấp nhất": _blank_money(low),
                        "Giá gốc cao nhất": _blank_money(high),
                        "Giá bán thấp nhất": _blank_money(sell_low),
                        "Giá bán cao nhất": _blank_money(sell_high),
                        "Trạng thái hàng": product_stock_label(product),
                        "Ngày cập nhật": updated_at,
                        "Trạng thái đồng bộ": product_sync_status(product),
                        "Tên gốc": product.title_raw or "",
                    },
                )
            )
        return records

    def build_variant_records(
        self,
        products: list[Product],
        updated_at: str,
        headers: list[str] | None = None,
        policy: PricingPolicy | None = None,
    ) -> list[dict[str, Any]]:
        del headers
        rules = policy or PricingPolicy()
        records: list[dict[str, Any]] = []
        for product in products:
            product_name = product.title or MISSING_TEXT
            for variant in product.variants:
                quote = quote_cost(variant.price_vnd, rules)
                records.append(
                    _ordered(
                        VARIANT_HEADERS,
                        {
                            "Product ID": product.id,
                            "Variant ID": variant.id,
                            "Tên sản phẩm": product_name,
                            "Tên biến thể": _display_text(variant.title_presence, variant.title),
                            "Mô tả": _display_text(
                                variant.description_presence, variant.description
                            ),
                            "Giá gốc": _blank_money(quote.cost),
                            "Tỷ lệ lợi nhuận (%)": quote.markup_percent,
                            "Lợi nhuận (VND)": _blank_money(quote.profit),
                            "Giá bán đề xuất (VND)": _blank_money(quote.selling),
                            "Trạng thái còn hàng": variant_stock_label(variant),
                            "Số lượng tồn kho": quantity_cell(variant),
                            "Manual upgrade": _manual_display(
                                variant.manual_upgrade_presence, variant.manual_upgrade
                            ),
                            "Ngày cập nhật": updated_at,
                            "Trạng thái đồng bộ": variant_sync_status(variant),
                            "Tên gốc": _raw_cell(variant.title_presence, variant.title_raw),
                            "Mô tả gốc": _raw_cell(
                                variant.description_presence, variant.description_raw
                            ),
                        },
                    )
                )
        return records

    def build_customer_records(
        self,
        products: list[Product],
        updated_at: str,
        policy: PricingPolicy | None = None,
    ) -> list[dict[str, Any]]:
        """Bảng giá khách. Bỏ biến thể thiếu giá gốc. Không có giá vốn hay lợi nhuận."""

        rules = policy or PricingPolicy()
        records: list[dict[str, Any]] = []
        for product in products:
            product_name = product.title or MISSING_TEXT
            for variant in product.variants:
                quote = quote_cost(variant.price_vnd, rules)
                if quote.selling is None:
                    continue
                records.append(
                    _ordered(
                        CUSTOMER_PRICE_HEADERS,
                        {
                            "Tên sản phẩm": product_name,
                            "Tên biến thể": _display_text(variant.title_presence, variant.title),
                            "Giá bán": quote.selling,
                            "Tình trạng hàng": variant_stock_label(variant),
                            "Ngày cập nhật": updated_at,
                        },
                    )
                )
        return records

    def summarize(self, products: list[Product]) -> CatalogSummary:
        summary = CatalogSummary(product_count=len(products))
        for product in products:
            summary.variant_count += len(product.variants)
            label = product_stock_label(product)
            if label == "Còn hàng":
                summary.in_stock += 1
            elif label == "Hết hàng":
                summary.out_of_stock += 1
            else:
                summary.unknown_stock += 1
            if price_bounds(product) == (None, None):
                summary.missing_price += 1
        return summary


def _ordered(headers: list[str], values: dict[str, Any]) -> dict[str, Any]:
    return {header: values.get(header, "") for header in headers}


def _blank_money(value: int | None) -> int | str:
    return "" if value is None else value


def _cleaned_text(presence: str, raw: str | None, *, multiline: bool) -> str | None:
    if presence != "value" or raw is None:
        return None
    return normalize_text(raw, multiline=multiline) or None


def _display_text(presence: str, cleaned: str | None) -> str:
    if presence == "value" and cleaned and cleaned.strip():
        return cleaned
    return MISSING_TEXT


def _raw_cell(presence: str, raw: str | None) -> str:
    if presence == "value" and isinstance(raw, str):
        return raw
    return ""


def _manual_display(presence: str, value: str | bool | None) -> str:
    if presence == "value" and isinstance(value, bool):
        return "Có" if value else "Không"
    if presence == "value" and isinstance(value, str) and value.strip():
        return value.strip()
    return MISSING_TEXT


def _read_flag(raw: dict[str, Any], key: str) -> tuple[str, bool | None, str | None]:
    if key not in raw:
        return "absent", None, None
    value = raw.get(key)
    if value is None:
        return "null", None, None
    if isinstance(value, bool):
        return "value", value, None
    return "invalid", None, f"Trường {key} không phải boolean. Trạng thái tồn kho để Không rõ."


def _read_quantity(raw: dict[str, Any], variant_id: str) -> tuple[str, int | None, str | None]:
    if "available_quantity" not in raw:
        return "absent", None, None
    value = raw.get("available_quantity")
    if value is None:
        return "null", None, None
    try:
        return "value", parse_optional_count(value), None
    except MoneyFormatError:
        return "invalid", None, f"Biến thể {variant_id} có số lượng không hợp lệ."


def _read_text(raw: dict[str, Any], key: str) -> tuple[str, str | None]:
    if key not in raw:
        return "absent", None
    value = raw.get(key)
    if value is None:
        return "null", None
    if isinstance(value, str):
        return "value", value
    return "value", str(value)


def _read_manual(raw: dict[str, Any]) -> tuple[str, str | bool | None]:
    if "manual_upgrade" not in raw:
        return "absent", None
    value = raw.get("manual_upgrade")
    if value is None:
        return "null", None
    if isinstance(value, bool) or isinstance(value, str):
        return "value", value
    return "value", str(value)


def _read_price(prices: Any, variant_id: str) -> tuple[int | None, str | None]:
    if prices is None:
        return None, f"Biến thể {variant_id} thiếu prices."
    if not isinstance(prices, dict):
        return None, f"Biến thể {variant_id} có prices không hợp lệ."
    if "vnd" not in prices:
        return None, f"Biến thể {variant_id} thiếu giá vnd."
    try:
        return parse_vnd(prices.get("vnd")), None
    except MoneyFormatError as exc:
        return None, f"Biến thể {variant_id}: {exc}"
