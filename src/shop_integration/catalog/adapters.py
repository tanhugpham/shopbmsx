"""Adapter nhà cung cấp cho catalog nhiều nguồn.

Mỗi adapter tự chịu trách nhiệm gọi API và chuẩn hóa dữ liệu thành
``SupplierProduct``. Phần còn lại của hệ thống không đọc cấu trúc JSON riêng
của bất kỳ API nào.

Không có adapter nào trong module này ghi Google hoặc tạo đơn.
"""

from __future__ import annotations

from typing import Any, Iterable, Protocol, Sequence, runtime_checkable

from shop_integration.errors import CatalogValidationError, ConfigError, IntegrationError
from shop_integration.suppliers.models import NormalizedOffer
from shop_integration.catalog.models import SupplierFetch, SupplierProduct


@runtime_checkable
class SupplierAdapter(Protocol):
    supplier_id: str
    supplier_name: str

    def fetch(self) -> SupplierFetch: ...


class AdapterRegistry:
    """Đăng ký nhiều API độc lập; trùng supplier_id là lỗi cấu hình."""

    def __init__(self, adapters: Iterable[Any] = ()) -> None:
        self._adapters: dict[str, Any] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: Any) -> None:
        supplier_id = str(getattr(adapter, "supplier_id", "") or "").strip()
        if not supplier_id:
            raise CatalogValidationError("Adapter thiếu supplier_id.")
        if supplier_id in self._adapters:
            raise CatalogValidationError(f"Trùng adapter supplier_id '{supplier_id}'.")
        if not callable(getattr(adapter, "fetch", None)):
            raise CatalogValidationError(f"Adapter '{supplier_id}' thiếu fetch().")
        self._adapters[supplier_id] = adapter

    @property
    def supplier_ids(self) -> tuple[str, ...]:
        return tuple(self._adapters)

    def fetch_all(self) -> list[SupplierFetch]:
        return [safe_fetch_adapter(adapter) for adapter in self._adapters.values()]


class ExistingProviderAdapter:
    """Bọc provider Phase 4.2 hiện có mà không đổi client HTTP của nó."""

    def __init__(
        self,
        provider: Any,
        *,
        supplier_id: str | None = None,
        supplier_name: str | None = None,
        source_key_for: Any = None,
    ) -> None:
        self._provider = provider
        self.supplier_id = supplier_id or str(getattr(provider, "supplier_id", "") or "").strip()
        self.supplier_name = supplier_name or str(
            getattr(provider, "supplier_name", self.supplier_id) or self.supplier_id
        )
        self._source_key_for = source_key_for

    def fetch(self) -> SupplierFetch:
        offers = self._provider.fetch()
        products: list[SupplierProduct] = []
        for offer in offers or ():
            if not isinstance(offer, NormalizedOffer):
                raise CatalogValidationError(
                    f"Provider {self.supplier_id} trả về phần tử không phải NormalizedOffer."
                )
            source_key = ""
            if callable(self._source_key_for):
                source_key = str(self._source_key_for(offer) or "").strip()
            products.append(SupplierProduct.from_offer(offer, source_key=source_key))
        return SupplierFetch(
            supplier_id=self.supplier_id,
            supplier_name=self.supplier_name,
            products=tuple(products),
            warnings=tuple(getattr(self._provider, "warnings", ()) or ()),
            catalog_status=str(getattr(self._provider, "catalog_status", "") or ""),
        )


class StaticSupplierAdapter:
    """Adapter cho snapshot local/test. Không gọi mạng."""

    def __init__(
        self,
        supplier_id: str,
        supplier_name: str,
        products: Sequence[SupplierProduct] = (),
        *,
        error: str = "",
        blocked: bool = False,
        warnings: Sequence[str] = (),
        catalog_status: str = "",
    ) -> None:
        self.supplier_id = supplier_id
        self.supplier_name = supplier_name
        self._products = tuple(products)
        self._error = error
        self._blocked = blocked
        self._warnings = tuple(warnings)
        self._catalog_status = catalog_status

    def fetch(self) -> SupplierFetch:
        if self._error or self._blocked:
            return SupplierFetch(
                supplier_id=self.supplier_id,
                supplier_name=self.supplier_name,
                error=self._error,
                blocked=self._blocked,
                warnings=self._warnings,
                catalog_status=self._catalog_status,
            )
        return SupplierFetch(
            supplier_id=self.supplier_id,
            supplier_name=self.supplier_name,
            products=self._products,
            warnings=self._warnings,
            catalog_status=self._catalog_status,
        )


def safe_fetch_adapter(adapter: Any) -> SupplierFetch:
    """Một API lỗi không làm mất dữ liệu của API khác."""

    supplier_id = str(getattr(adapter, "supplier_id", "") or "").strip()
    supplier_name = str(getattr(adapter, "supplier_name", supplier_id) or supplier_id)
    try:
        result = adapter.fetch()
    except ConfigError as exc:
        text = str(exc)
        return SupplierFetch(
            supplier_id=supplier_id,
            supplier_name=supplier_name,
            error=text,
            blocked="API_KEY" in text,
        )
    except IntegrationError as exc:
        return SupplierFetch(supplier_id=supplier_id, supplier_name=supplier_name, error=str(exc))
    except Exception:
        return SupplierFetch(
            supplier_id=supplier_id,
            supplier_name=supplier_name,
            error=f"Lỗi không xác định khi đọc {supplier_id}.",
        )
    if not isinstance(result, SupplierFetch):
        return SupplierFetch(
            supplier_id=supplier_id,
            supplier_name=supplier_name,
            error=f"Adapter {supplier_id} trả về kiểu dữ liệu không hợp lệ.",
        )
    return result
