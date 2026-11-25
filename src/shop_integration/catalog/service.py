"""Smart sync đọc nhiều API và dựng catalog khách. Không ghi Google."""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from shop_integration.catalog.adapters import AdapterRegistry
from shop_integration.catalog.models import CatalogPlan, StoreProduct
from shop_integration.catalog.reconcile import build_catalog_plan
from shop_integration.pricing import PricingPolicy


class CatalogSyncService:
    """Điều phối bước 1–7 của Phase 4.3F ở chế độ chỉ đọc."""

    def __init__(
        self,
        adapters: Iterable[Any] | AdapterRegistry,
        store_products: Sequence[StoreProduct],
        *,
        policy: PricingPolicy | None = None,
        quantity: int = 1,
        preferred_supplier_id: str = "",
        auto_publish_new: bool = False,
    ) -> None:
        self.registry = (
            adapters if isinstance(adapters, AdapterRegistry) else AdapterRegistry(adapters)
        )
        self.store_products = list(store_products)
        self.policy = policy
        self.quantity = quantity
        self.preferred_supplier_id = preferred_supplier_id
        self.auto_publish_new = auto_publish_new

    def build_plan(self) -> CatalogPlan:
        fetches = self.registry.fetch_all()
        products = [product for fetch in fetches for product in fetch.products]
        failed = {fetch.supplier_id for fetch in fetches if fetch.error or fetch.blocked}
        empty = {
            fetch.supplier_id
            for fetch in fetches
            if fetch.catalog_status in {"CATALOG_EMPTY", "NASTELE_CATALOG_EMPTY"}
        }
        return build_catalog_plan(
            self.store_products,
            products,
            policy=self.policy,
            quantity=self.quantity,
            preferred_supplier_id=self.preferred_supplier_id,
            supplier_fetches=fetches,
            failed_supplier_ids=failed,
            empty_catalog_supplier_ids=empty,
            auto_publish_new=self.auto_publish_new,
        )
