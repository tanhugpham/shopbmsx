"""Mô hình chung sau khi mỗi nhà cung cấp đã được kiểm tra riêng."""

from __future__ import annotations

from dataclasses import dataclass, field

SUPPLIER_SHOP = "shop_xoai_non"
SUPPLIER_NASTELE = "nastele"
SUPPLIER_NAMES = {
    SUPPLIER_SHOP: "Shop Xoài Non",
    SUPPLIER_NASTELE: "Nastele",
}

EXACT_MATCH = "EXACT_MATCH"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
DIFFERENT_PRODUCT = "DIFFERENT_PRODUCT"
NASTELE_CATALOG_EMPTY = "NASTELE_CATALOG_EMPTY"


def composite_id(supplier_id: str, product_id: str, variant_id: str) -> str:
    """Khóa toàn cục. Không dùng product id của một nhà cung cấp làm khóa duy nhất."""

    return f"{supplier_id}|{product_id}|{variant_id}"


@dataclass(frozen=True)
class WholesaleTier:
    min_quantity: int
    unit_price_vnd: int


@dataclass
class NormalizedOffer:
    supplier_id: str
    supplier_name: str
    source_product_id: str
    source_variant_id: str
    source_product_name: str
    normalized_product_name: str
    description: str
    product_attributes: dict[str, str]
    cost_price_vnd: int | None
    available: bool | None
    available_quantity: int | None
    quantity_presence: str
    wholesale_tiers: list[WholesaleTier] = field(default_factory=list)
    fetched_at: str = ""
    raw_data_reference: str = ""
    category_id: str | None = None
    warnings: list[str] = field(default_factory=list)
    available_presence: str = "absent"

    @property
    def composite_id(self) -> str:
        return composite_id(self.supplier_id, self.source_product_id, self.source_variant_id)


@dataclass
class ProviderResult:
    supplier_id: str
    offers: list[NormalizedOffer]
    error: str | None = None
    blocked: bool = False
    warnings: list[str] = field(default_factory=list)
    catalog_status: str = ""


@dataclass(frozen=True)
class ApprovedMatch:
    left_id: str
    right_id: str
    decision: str
    note: str = ""
    canonical_id: str = ""

    def pair_key(self) -> tuple[str, str]:
        left, right = sorted((self.left_id, self.right_id))
        return left, right


@dataclass(frozen=True)
class MatchDecision:
    left_id: str
    right_id: str
    kind: str
    reason: str
    score: str = ""


@dataclass
class SourceChoice:
    selected: NormalizedOffer | None
    selected_cost: int | None
    backup: NormalizedOffer | None
    backup_cost: int | None
    cost_difference: int | None
    selling_price: int | None
    reason: str
    rejected_supplier_id: str = ""
    rejected_cost: int | None = None
    applied_tier_quantity: int | None = None
