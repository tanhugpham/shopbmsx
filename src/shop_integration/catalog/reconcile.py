"""Đối chiếu nhiều nguồn thành catalog bán cho khách.

Thứ tự: nhận dữ liệu đã chuẩn hóa -> đối chiếu mapping nội bộ -> kiểm tra tương
đương -> áp content override -> tính giá -> cập nhật tồn kho/trạng thái.
Không gọi mạng, không ghi Google, không tạo đơn.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Iterable, Sequence

from shop_integration.catalog.models import (
    PUBLISHED,
    REVIEW_REQUIRED,
    SALES_CONTACT,
    CatalogIssue,
    CatalogPlan,
    SupplierFetch,
    SupplierMapping,
    SupplierProduct,
    StoreProduct,
    opaque_store_id,
)
from shop_integration.combined_quote import quote_duration, quote_warranty
from shop_integration.errors import CatalogValidationError
from shop_integration.pricing import PricingPolicy, quote_cost, round_pretty_vnd
from shop_integration.suppliers.matching import classify_pair
from shop_integration.suppliers.models import (
    DIFFERENT_PRODUCT,
    EXACT_MATCH,
    NASTELE_CATALOG_EMPTY,
    REVIEW_REQUIRED as MATCH_REVIEW_REQUIRED,
)
from shop_integration.suppliers.selection import select_group
from shop_integration.text_normalize import public_description

SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
NO_SOURCE_THIS_READ = "NO_SOURCE_THIS_READ"
NEW_SUPPLIER_PRODUCT = "NEW_SUPPLIER_PRODUCT"
UNMAPPED_SUPPLIER_PRODUCT = "UNMAPPED_SUPPLIER_PRODUCT"
MAPPING_NOT_APPROVED = "MAPPING_NOT_APPROVED"
MAPPING_UNKNOWN_STORE = "MAPPING_UNKNOWN_STORE"
MAPPING_CONFLICT = "MAPPING_CONFLICT"
DUPLICATE_SUPPLIER_IDENTITY = "DUPLICATE_SUPPLIER_IDENTITY"
DUPLICATE_STORE_ID = "DUPLICATE_STORE_ID"
SOURCE_CONDITION_CONFLICT = "SOURCE_CONDITION_CONFLICT"
EQUIVALENCE_REVIEW_REQUIRED = "EQUIVALENCE_REVIEW_REQUIRED"
NO_VERIFIED_SOURCE = "NO_VERIFIED_SOURCE"
PRICE_MISSING = "PRICE_MISSING"
STOCK_UNKNOWN = "STOCK_UNKNOWN"
SUPPLIER_ERROR = "SUPPLIER_ERROR"
SUPPLIER_CATALOG_EMPTY = "SUPPLIER_CATALOG_EMPTY"
_WARRANTY_TEXT = re.compile(r"(?i)\b(BHF|KBH|BH\s*\d+\s*(?:ngày|ngay|[dnh]))\b")
def build_catalog_plan(
    store_products: Sequence[StoreProduct],
    supplier_products: Sequence[SupplierProduct],
    *,
    policy: PricingPolicy | None = None,
    quantity: int = 1,
    preferred_supplier_id: str = "",
    supplier_fetches: Sequence[SupplierFetch] = (),
    failed_supplier_ids: Iterable[str] = (),
    empty_catalog_supplier_ids: Iterable[str] = (),
    auto_publish_new: bool = False,
) -> CatalogPlan:
    """Dựng catalog bán từ nhiều nguồn. Hàm thuần, không có side effect."""

    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
        raise CatalogValidationError("quantity phải là số nguyên lớn hơn hoặc bằng 1.")
    rules = policy or PricingPolicy()
    issues: list[CatalogIssue] = []
    fetches = list(supplier_fetches)
    failed = {str(item).strip() for item in failed_supplier_ids if str(item).strip()}
    empty = {str(item).strip() for item in empty_catalog_supplier_ids if str(item).strip()}

    for fetch in fetches:
        if fetch.error:
            failed.add(fetch.supplier_id)
            issues.append(
                CatalogIssue(
                    SUPPLIER_ERROR,
                    f"{fetch.supplier_name}: {fetch.error}",
                    "WARNING",
                    supplier_id=fetch.supplier_id,
                )
            )
        if fetch.catalog_status in {NASTELE_CATALOG_EMPTY, "CATALOG_EMPTY"}:
            empty.add(fetch.supplier_id)
            issues.append(
                CatalogIssue(
                    SUPPLIER_CATALOG_EMPTY,
                    f"{fetch.supplier_name}: catalog rỗng. Giữ dữ liệu cũ, không ghi tồn kho 0.",
                    "WARNING",
                    supplier_id=fetch.supplier_id,
                )
            )

    stores = _unique_stores(store_products, issues)
    products = _unique_supplier_products(supplier_products, issues)
    index = _MappingIndex(stores, issues)
    assigned: dict[str, list[SupplierProduct]] = {store.store_product_id: [] for store in stores}
    drafts: list[StoreProduct] = []
    new_products: list[SupplierProduct] = []

    for product in products:
        if not product.is_identified:
            new_products.append(product)
            issues.append(
                CatalogIssue(
                    UNMAPPED_SUPPLIER_PRODUCT,
                    f"{product.supplier_name}: sản phẩm '{product.normalized_product_name}' "
                    "không có ID ổn định hoặc source_key. Không tự đoán mapping.",
                    "WARNING",
                    supplier_id=product.supplier_id,
                    supplier_product_id=product.supplier_product_id,
                    supplier_variant_id=product.supplier_variant_id,
                )
            )
            continue
        targets = index.lookup(product)
        if len(targets) > 1:
            issues.append(
                CatalogIssue(
                    MAPPING_CONFLICT,
                    f"{product.supplier_id}/{product.supplier_product_id} đang map tới "
                    "nhiều store product. Không tự chọn.",
                    "ERROR",
                    supplier_id=product.supplier_id,
                    supplier_product_id=product.supplier_product_id,
                    supplier_variant_id=product.supplier_variant_id,
                )
            )
            continue
        if not targets:
            new_products.append(product)
            draft = _draft_from_source(product, auto_publish_new, rules, issues)
            if draft is not None:
                drafts.append(draft)
            continue
        assigned[targets[0].store_product_id].append(product)

    results: list[StoreProduct] = []
    selected_sources: dict[str, SupplierProduct] = {}
    backup_sources: dict[str, SupplierProduct] = {}
    for store in stores:
        result, selected, backup = _reconcile_store(
            store,
            assigned[store.store_product_id],
            failed=failed,
            empty=empty,
            policy=rules,
            quantity=quantity,
            preferred_supplier_id=preferred_supplier_id,
            issues=issues,
        )
        results.append(result)
        if selected is not None:
            selected_sources[store.store_product_id] = selected
        if backup is not None:
            backup_sources[store.store_product_id] = backup
    results.extend(drafts)
    return CatalogPlan(
        store_products=results,
        supplier_products=products,
        issues=issues,
        selected_sources=selected_sources,
        backup_sources=backup_sources,
        new_supplier_products=new_products,
        supplier_fetches=fetches,
    )

def _unique_stores(
    stores: Sequence[StoreProduct],
    issues: list[CatalogIssue],
) -> list[StoreProduct]:
    seen: set[str] = set()
    result: list[StoreProduct] = []
    for store in stores:
        if store.store_product_id in seen:
            issues.append(
                CatalogIssue(
                    DUPLICATE_STORE_ID,
                    f"Trùng store_product_id '{store.store_product_id}'. Không ghi để tránh mất dữ liệu.",
                    "ERROR",
                    store_product_id=store.store_product_id,
                )
            )
            continue
        seen.add(store.store_product_id)
        result.append(store)
    return result


def _unique_supplier_products(
    products: Sequence[SupplierProduct],
    issues: list[CatalogIssue],
) -> list[SupplierProduct]:
    result: list[SupplierProduct] = []
    by_key: dict[str, SupplierProduct] = {}
    for product in products:
        key = product.identity_key
        if not key:
            result.append(product)
            continue
        if key not in by_key:
            by_key[key] = product
            result.append(product)
            continue
        issues.append(
            CatalogIssue(
                DUPLICATE_SUPPLIER_IDENTITY,
                f"{product.supplier_id}/{product.supplier_product_id}/"
                f"{product.supplier_variant_id} xuất hiện hơn một lần. Giữ bản ghi cuối.",
                "WARNING",
                supplier_id=product.supplier_id,
                supplier_product_id=product.supplier_product_id,
                supplier_variant_id=product.supplier_variant_id,
            )
        )
        previous = by_key[key]
        index = result.index(previous)
        result[index] = product
        by_key[key] = product
    return result


class _MappingIndex:
    """Index mapping theo ID nguồn hoặc source_key, không theo tên."""

    def __init__(self, stores: Sequence[StoreProduct], issues: list[CatalogIssue]) -> None:
        self._stores = {store.store_product_id: store for store in stores}
        self._by_external: dict[tuple[str, str, str], list[StoreProduct]] = {}
        self._by_key: dict[tuple[str, str], list[StoreProduct]] = {}
        for store in stores:
            for mapping in store.mappings:
                self._add(store, mapping, issues)

    def _add(
        self,
        store: StoreProduct,
        mapping: SupplierMapping,
        issues: list[CatalogIssue],
    ) -> None:
        if mapping.store_product_id not in self._stores:
            issues.append(
                CatalogIssue(
                    MAPPING_UNKNOWN_STORE,
                    f"{mapping.supplier_id} map tới store '{mapping.store_product_id}' không tồn tại.",
                    "ERROR",
                    store_product_id=mapping.store_product_id,
                    supplier_id=mapping.supplier_id,
                    supplier_product_id=mapping.supplier_product_id,
                    supplier_variant_id=mapping.supplier_variant_id,
                )
            )
            return
        if mapping.store_product_id != store.store_product_id:
            issues.append(
                CatalogIssue(
                    MAPPING_CONFLICT,
                    f"Mapping của store '{store.store_product_id}' lại trỏ tới "
                    f"store '{mapping.store_product_id}'. Không tự chọn.",
                    "ERROR",
                    store_product_id=store.store_product_id,
                    supplier_id=mapping.supplier_id,
                    supplier_product_id=mapping.supplier_product_id,
                    supplier_variant_id=mapping.supplier_variant_id,
                )
            )
            return
        if not mapping.approved:
            issues.append(
                CatalogIssue(
                    MAPPING_NOT_APPROVED,
                    f"{mapping.supplier_id}/{mapping.supplier_product_id or mapping.source_key} "
                    "chưa được duyệt mapping. Không tự ghép.",
                    "WARNING",
                    store_product_id=mapping.store_product_id,
                    supplier_id=mapping.supplier_id,
                    supplier_product_id=mapping.supplier_product_id,
                    supplier_variant_id=mapping.supplier_variant_id,
                )
            )
            return
        if mapping.supplier_product_id:
            key = (
                mapping.supplier_id,
                mapping.supplier_product_id,
                mapping.supplier_variant_id,
            )
            targets = self._by_external.setdefault(key, [])
        else:
            key = (mapping.supplier_id, mapping.source_key)
            targets = self._by_key.setdefault(key, [])
        if store not in targets:
            targets.append(store)

    def lookup(self, product: SupplierProduct) -> list[StoreProduct]:
        if product.supplier_product_id:
            key = (
                product.supplier_id,
                product.supplier_product_id,
                product.supplier_variant_id,
            )
            return list(self._by_external.get(key, ()))
        if product.source_key:
            return list(self._by_key.get((product.supplier_id, product.source_key), ()))
        return []

def _reconcile_store(
    store: StoreProduct,
    sources: list[SupplierProduct],
    *,
    failed: set[str],
    empty: set[str],
    policy: PricingPolicy,
    quantity: int,
    preferred_supplier_id: str,
    issues: list[CatalogIssue],
) -> tuple[StoreProduct, SupplierProduct | None, SupplierProduct | None]:
    mapped_suppliers = {mapping.supplier_id for mapping in store.mappings}
    if mapped_suppliers & failed:
        issues.append(
            CatalogIssue(
                SOURCE_UNAVAILABLE,
                f"Store '{store.store_product_id}' có nguồn lỗi. Giữ giá/tên/tồn cũ và đánh dấu xác minh.",
                "WARNING",
                store_product_id=store.store_product_id,
                supplier_id=",".join(sorted(mapped_suppliers & failed)),
            )
        )
        return _preserve(store, "Cần xác minh"), None, None
    if mapped_suppliers & empty:
        issues.append(
            CatalogIssue(
                SUPPLIER_CATALOG_EMPTY,
                f"Store '{store.store_product_id}' có nguồn catalog rỗng. Không ghi tồn kho 0.",
                "WARNING",
                store_product_id=store.store_product_id,
                supplier_id=",".join(sorted(mapped_suppliers & empty)),
            )
        )
        return _preserve(store, "Cần xác minh"), None, None
    if not store.mappings:
        return store, None, None
    if not sources:
        issues.append(
            CatalogIssue(
                NO_SOURCE_THIS_READ,
                f"Store '{store.store_product_id}' không thấy nguồn trong lần đọc này. Giữ dữ liệu cũ.",
                "WARNING",
                store_product_id=store.store_product_id,
            )
        )
        return _preserve(store, "Cần xác minh"), None, None
    if len(sources) > 1 and not _sources_equivalent(
        store,
        sources,
        issues,
    ):
        return _preserve(store, "Cần xác minh"), None, None

    by_composite = {product.to_offer().composite_id: product for product in sources}
    choice = select_group(
        [product.to_offer() for product in sources],
        quantity=quantity,
        preferred_supplier_id=preferred_supplier_id,
        policy=policy,
    )
    if choice.selected is None:
        status = "Hết hàng" if all(product.availability is False for product in sources) else "Cần xác minh"
        issues.append(
            CatalogIssue(
                NO_VERIFIED_SOURCE,
                f"Store '{store.store_product_id}' không có nguồn còn hàng với giá hợp lệ. Giữ giá cũ.",
                "WARNING",
                store_product_id=store.store_product_id,
            )
        )
        return _preserve(store, status), None, None
    selected = by_composite.get(choice.selected.composite_id)
    if selected is None:
        raise CatalogValidationError("Không map ngược được nguồn đã chọn về SupplierProduct.")
    backup = by_composite.get(choice.backup.composite_id) if choice.backup is not None else None

    selling = _selling_price(selected, store, policy, issues)
    stock_status, stock_quantity = _stock_state(selected, store, issues)
    content = _resolved_content(store, selected)
    updated = replace(
        store,
        display_name=content["display_name"],
        display_description=content["display_description"],
        duration=content["duration"],
        warranty=content["warranty"],
        sales_content=content["sales_content"],
        contact=content["contact"],
        selling_price=selling,
        stock_status=stock_status,
        stock_quantity=stock_quantity,
    )
    return updated, selected, backup


def _sources_equivalent(
    store: StoreProduct,
    sources: list[SupplierProduct],
    issues: list[CatalogIssue],
) -> bool:
    """Chỉ chọn nguồn khi các nguồn được mapping thực sự tương đương."""

    for index, left in enumerate(sources):
        for right in sources[index + 1 :]:
            left_warranty = _warranty_key(_source_text(left))
            right_warranty = _warranty_key(_source_text(right))
            if left_warranty and right_warranty and left_warranty != right_warranty:
                issues.append(
                    CatalogIssue(
                        SOURCE_CONDITION_CONFLICT,
                        f"Store '{store.store_product_id}': bảo hành nguồn khác nhau "
                        f"({left_warranty}!={right_warranty}). Không tự gộp.",
                        "ERROR",
                        store_product_id=store.store_product_id,
                        supplier_id=f"{left.supplier_id},{right.supplier_id}",
                        supplier_product_id=f"{left.supplier_product_id},{right.supplier_product_id}",
                    )
                )
                return False
            if bool(left_warranty) != bool(right_warranty):
                issues.append(
                    CatalogIssue(
                        EQUIVALENCE_REVIEW_REQUIRED,
                        f"Store '{store.store_product_id}': chỉ một nguồn nêu bảo hành. "
                        "Không đủ bằng chứng tương đương.",
                        "WARNING",
                        store_product_id=store.store_product_id,
                        supplier_id=f"{left.supplier_id},{right.supplier_id}",
                        supplier_product_id=f"{left.supplier_product_id},{right.supplier_product_id}",
                    )
                )
                return False
            decision = classify_pair(left.to_offer(), right.to_offer())
            if decision.kind == EXACT_MATCH:
                continue
            code = SOURCE_CONDITION_CONFLICT if decision.kind == DIFFERENT_PRODUCT else EQUIVALENCE_REVIEW_REQUIRED
            issues.append(
                CatalogIssue(
                    code,
                    f"Store '{store.store_product_id}': {left.supplier_id}/"
                    f"{left.supplier_product_id} và {right.supplier_id}/"
                    f"{right.supplier_product_id} không đủ bằng chứng tương đương "
                    f"({decision.reason}). Không tự chọn nguồn.",
                    "WARNING" if decision.kind == MATCH_REVIEW_REQUIRED else "ERROR",
                    store_product_id=store.store_product_id,
                    supplier_id=f"{left.supplier_id},{right.supplier_id}",
                    supplier_product_id=f"{left.supplier_product_id},{right.supplier_product_id}",
                )
            )
            return False
    return True


def _selling_price(
    product: SupplierProduct,
    store: StoreProduct,
    policy: PricingPolicy,
    issues: list[CatalogIssue],
) -> int | None:
    if product.cost_price_vnd is None:
        issues.append(
            CatalogIssue(
                PRICE_MISSING,
                f"Store '{store.store_product_id}' thiếu giá vốn từ nguồn. Giữ giá bán cũ.",
                "WARNING",
                store_product_id=store.store_product_id,
                supplier_id=product.supplier_id,
                supplier_product_id=product.supplier_product_id,
                supplier_variant_id=product.supplier_variant_id,
            )
        )
        return store.selling_price
    quote = quote_cost(product.cost_price_vnd, policy)
    if quote.selling is None:
        issues.append(
            CatalogIssue(
                PRICE_MISSING,
                f"Store '{store.store_product_id}' có giá vốn không hợp lệ. Giữ giá bán cũ.",
                "WARNING",
                store_product_id=store.store_product_id,
                supplier_id=product.supplier_id,
                supplier_product_id=product.supplier_product_id,
                supplier_variant_id=product.supplier_variant_id,
            )
        )
        return store.selling_price
    return round_pretty_vnd(quote.selling)


def _stock_state(
    product: SupplierProduct,
    store: StoreProduct,
    issues: list[CatalogIssue],
) -> tuple[str, int | None]:
    if product.availability is True:
        return "Còn hàng", product.stock_quantity
    if product.availability is False:
        return "Hết hàng", 0
    issues.append(
        CatalogIssue(
            STOCK_UNKNOWN,
            f"Store '{store.store_product_id}' chưa xác nhận được tồn kho. Không suy thành hết hàng.",
            "WARNING",
            store_product_id=store.store_product_id,
            supplier_id=product.supplier_id,
            supplier_product_id=product.supplier_product_id,
            supplier_variant_id=product.supplier_variant_id,
        )
    )
    return "Cần xác minh", None


def _source_text(product: SupplierProduct) -> str:
    return f"{product.raw_product_name} {product.raw_description}"


def _warranty_key(text: str) -> str:
    match = _WARRANTY_TEXT.search(text)
    if match is None:
        return ""
    compact = re.sub(r"\s+", "", match.group(1)).upper()
    return compact.replace("NGÀY", "N").replace("NGAY", "N")


def _resolved_content(store: StoreProduct, product: SupplierProduct) -> dict[str, str]:
    text = _source_text(product)
    api_name = product.normalized_product_name or product.raw_product_name
    api_description = public_description(product.raw_description)
    api_duration = quote_duration(text)
    # Bảo hành là điều kiện bắt buộc của nguồn, không nằm trong ContentOverride.
    # Không tự đổi sang mức mặc định khi nguồn không nêu bảo hành.
    warranty = quote_warranty(text) if _warranty_key(text) else store.warranty
    override = store.content_override
    return {
        "display_name": override.resolve("display_name", api_name, store.display_name),
        "display_description": override.resolve(
            "display_description", api_description, store.display_description
        ),
        "duration": override.resolve("duration", api_duration, store.duration),
        "warranty": warranty,
        "sales_content": override.resolve("sales_content", "", store.sales_content),
        "contact": override.resolve("contact", "", store.contact),
    }


def _preserve(store: StoreProduct, stock_status: str) -> StoreProduct:
    return replace(store, stock_status=stock_status)


def _draft_from_source(
    product: SupplierProduct,
    auto_publish_new: bool,
    policy: PricingPolicy,
    issues: list[CatalogIssue],
) -> StoreProduct | None:
    if not product.is_identified:
        return None
    store_id = opaque_store_id(product.identity_key)
    name = product.normalized_product_name or product.raw_product_name or "Chưa có tên"
    description = public_description(product.raw_description)
    text = f"{product.raw_product_name} {product.raw_description}"
    quote = quote_cost(product.cost_price_vnd, policy)
    selling = round_pretty_vnd(quote.selling) if quote.selling is not None else None
    if product.availability is True:
        stock_status: str = "Còn hàng"
        stock_quantity = product.stock_quantity
    elif product.availability is False:
        stock_status = "Hết hàng"
        stock_quantity = 0
    else:
        stock_status = "Cần xác minh"
        stock_quantity = None
    mapping = SupplierMapping(
        store_product_id=store_id,
        supplier_id=product.supplier_id,
        supplier_product_id=product.supplier_product_id,
        supplier_variant_id=product.supplier_variant_id,
        source_key=product.source_key,
        approved=True,
        note="Tạo nháp từ nguồn; cần duyệt nội dung trước khi bán.",
    )
    publication = PUBLISHED if auto_publish_new and selling is not None else REVIEW_REQUIRED
    issues.append(
        CatalogIssue(
            NEW_SUPPLIER_PRODUCT,
            f"{product.supplier_name}: sản phẩm mới '{name}' đã được đưa vào danh sách "
            f"{'bán' if publication == PUBLISHED else 'cần duyệt'}, không bị bỏ qua âm thầm.",
            "INFO" if publication == PUBLISHED else "WARNING",
            store_product_id=store_id,
            supplier_id=product.supplier_id,
            supplier_product_id=product.supplier_product_id,
            supplier_variant_id=product.supplier_variant_id,
        )
    )
    return StoreProduct(
        store_product_id=store_id,
        display_name=name,
        display_description=description,
        duration=quote_duration(text),
        warranty=quote_warranty(text),
        selling_price=selling,
        stock_status=stock_status,
        stock_quantity=stock_quantity,
        contact=SALES_CONTACT,
        mappings=(mapping,),
        publication_status=publication,
    )
