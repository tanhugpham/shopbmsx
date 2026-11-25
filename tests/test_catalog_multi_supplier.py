"""Phase 4.3F: catalog hai tầng, nhiều API và MENU khách an toàn.

Không test nào gọi Google, ghi file Google hay tạo đơn. Adapter/gateway đều giả.
"""

from __future__ import annotations

import pytest

from shop_integration.catalog.adapters import (
    AdapterRegistry,
    ExistingProviderAdapter,
    StaticSupplierAdapter,
)
from shop_integration.catalog.menu_projection import assert_customer_safe
from shop_integration.catalog.models import (
    CUSTOMER_MENU_HEADERS,
    PUBLISHED,
    REVIEW_REQUIRED,
    SALES_CONTACT,
    ContentOverride,
    SupplierMapping,
    SupplierProduct,
    StoreProduct,
)
from shop_integration.catalog.reconcile import (
    DUPLICATE_SUPPLIER_IDENTITY,
    MAPPING_CONFLICT,
    NEW_SUPPLIER_PRODUCT,
    NO_SOURCE_THIS_READ,
    SOURCE_CONDITION_CONFLICT,
    SOURCE_UNAVAILABLE,
    SUPPLIER_ERROR,
    UNMAPPED_SUPPLIER_PRODUCT,
    build_catalog_plan,
)
from shop_integration.catalog.service import CatalogSyncService
from shop_integration.combined_quote import MENU_HEADERS, plan_menu_cells
from shop_integration.errors import CatalogValidationError
from shop_integration.pricing import quote_cost, round_pretty_vnd
from shop_integration.suppliers.models import NormalizedOffer


def product(
    supplier: str = "shop_xoai_non",
    product_id: str = "1",
    variant_id: str = "v1",
    name: str = "CANVA PRO 1 NĂM",
    *,
    description: str = "Mô tả nguồn",
    cost: int | None = 30_000,
    stock: int | None = 4,
    available: bool | None = True,
    source_key: str = "",
) -> SupplierProduct:
    return SupplierProduct(
        supplier_id=supplier,
        supplier_name=supplier,
        supplier_product_id=product_id,
        supplier_variant_id=variant_id,
        source_key=source_key,
        raw_product_name=name,
        raw_description=description,
        normalized_product_name=name,
        cost_price_vnd=cost,
        stock_quantity=stock,
        availability=available,
    )


def mapping(
    store_id: str,
    supplier: str,
    product_id: str = "1",
    variant_id: str = "v1",
    *,
    source_key: str = "",
    approved: bool = True,
) -> SupplierMapping:
    return SupplierMapping(
        store_product_id=store_id,
        supplier_id=supplier,
        supplier_product_id=product_id,
        supplier_variant_id=variant_id,
        source_key=source_key,
        approved=approved,
    )


def store(
    store_id: str = "SP-1",
    name: str = "CANVA PRO 1 NĂM",
    *,
    menu_code: str = "SXN-01",
    mappings: tuple[SupplierMapping, ...] = (),
    **kwargs: object,
) -> StoreProduct:
    values: dict[str, object] = {
        "store_product_id": store_id,
        "display_name": name,
        "display_description": "",
        "duration": "1 Năm",
        "warranty": "BHF",
        "selling_price": 99_000,
        "stock_status": "Còn hàng",
        "stock_quantity": 3,
        "menu_code": menu_code,
        "mappings": mappings,
    }
    values.update(kwargs)
    return StoreProduct(**values)  # type: ignore[arg-type]


def issue_codes(plan) -> set[str]:
    return {issue.code for issue in plan.issues}
def test_customer_menu_headers_match_existing_layout() -> None:
    assert CUSTOMER_MENU_HEADERS == tuple(MENU_HEADERS)


def test_two_layers_keep_supplier_data_out_of_store_and_customer_view() -> None:
    item = product()
    mapped_store = store(mappings=(mapping("SP-1", "shop_xoai_non"),))
    plan = build_catalog_plan([mapped_store], [item])
    assert len(plan.store_products) == 1
    assert plan.selected_sources["SP-1"].supplier_product_id == "1"
    assert plan.store_products[0].display_name == "CANVA PRO 1 NĂM"
    assert plan.store_products[0].contact == SALES_CONTACT
    row = plan.customer_menu_rows()[0]
    assert tuple(row) == CUSTOMER_MENU_HEADERS
    assert "ProductID" not in row
    assert "VariantID" not in row
    assert "supplier_id" not in row
    assert "cost" not in str(row).casefold()
    assert plan.store_products[0].customer_profile()["Contact"] == "@tanhung929"
    assert plan.customer_menu_rows()[0]["PRICE"] == round_pretty_vnd(quote_cost(30_000).selling)


def test_adapter_registry_isolates_one_failed_api() -> None:
    class Broken:
        supplier_id = "broken"
        supplier_name = "Broken API"

        def fetch(self) -> None:
            raise RuntimeError("không được lộ chi tiết")

    registry = AdapterRegistry(
        [
            StaticSupplierAdapter("shop_xoai_non", "Shop", (product(),)),
            Broken(),
        ]
    )
    plan = CatalogSyncService(
        registry,
        [store(mappings=(mapping("SP-1", "shop_xoai_non"),))],
    ).build_plan()
    assert len(plan.supplier_products) == 1
    assert plan.selected_sources["SP-1"].supplier_id == "shop_xoai_non"
    assert SUPPLIER_ERROR in issue_codes(plan)
    assert "không được lộ chi tiết" not in plan.text()


def test_same_numeric_id_from_two_apis_is_not_a_global_id() -> None:
    shop_item = product("shop_xoai_non", "43", "v", "NETFLIX 1 THÁNG")
    other_item = product("api_2", "43", "", "SPOTIFY 1 THÁNG")
    shop_store = store(
        "SP-1",
        "NETFLIX 1 THÁNG",
        menu_code="SXN-01",
        mappings=(mapping("SP-1", "shop_xoai_non", "43", "v"),),
    )
    other_store = store(
        "SP-2",
        "SPOTIFY 1 THÁNG",
        menu_code="API2-01",
        mappings=(mapping("SP-2", "api_2", "43", ""),),
    )
    plan = build_catalog_plan([shop_store, other_store], [shop_item, other_item])
    assert len(plan.store_products) == 2
    assert plan.selected_sources["SP-1"].supplier_id == "shop_xoai_non"
    assert plan.selected_sources["SP-2"].supplier_id == "api_2"
    assert len(plan.blocking_issues) == 0


def test_api_without_id_requires_source_key_and_manual_mapping() -> None:
    unidentified = product("api_2", "", "", "SẢN PHẨM KHÔNG ID", source_key="")
    plan = build_catalog_plan([], [unidentified])
    assert UNMAPPED_SUPPLIER_PRODUCT in issue_codes(plan)
    assert plan.new_supplier_products == [unidentified]
    assert plan.store_products == []

    manual = product("api_2", "", "", "SẢN PHẨM KHÔNG ID", source_key="api2-slot-01")
    manual_store = store(
        "SP-2",
        "SẢN PHẨM KHÔNG ID",
        menu_code="API2-01",
        mappings=(
            SupplierMapping(
                "SP-2",
                "api_2",
                source_key="api2-slot-01",
                approved=True,
            ),
        ),
    )
    plan = build_catalog_plan([manual_store], [manual])
    assert plan.selected_sources["SP-2"].source_key == "api2-slot-01"
    assert plan.customer_menu_rows()[0]["PRODUCT"] == "SẢN PHẨM KHÔNG ID"

def test_duplicate_names_are_not_merged() -> None:
    first = product("shop_xoai_non", "1", "v", "GÓI GIỐNG NHAU")
    second = product("api_2", "1", "", "GÓI GIỐNG NHAU")
    first_store = store(
        "SP-1",
        "GÓI GIỐNG NHAU",
        menu_code="SXN-01",
        mappings=(mapping("SP-1", "shop_xoai_non", "1", "v"),),
    )
    second_store = store(
        "SP-2",
        "GÓI GIỐNG NHAU",
        menu_code="API2-01",
        mappings=(mapping("SP-2", "api_2", "1", ""),),
    )
    plan = build_catalog_plan([first_store, second_store], [first, second])
    assert len(plan.store_products) == 2
    assert {item.public_id for item in plan.store_products} == {"SXN-01", "API2-01"}
    assert len(plan.customer_menu_rows()) == 2


def test_same_name_different_duration_is_a_conflict_not_a_merge() -> None:
    yearly = product("shop_xoai_non", "1", "v", "CANVA PRO 1 NĂM")
    monthly = product("api_2", "2", "", "CANVA PRO 1 THÁNG")
    shared_store = store(
        "SP-1",
        "CANVA PRO 1 NĂM",
        selling_price=123_000,
        mappings=(
            mapping("SP-1", "shop_xoai_non", "1", "v"),
            mapping("SP-1", "api_2", "2", ""),
        ),
    )
    plan = build_catalog_plan([shared_store], [yearly, monthly])
    assert SOURCE_CONDITION_CONFLICT in issue_codes(plan)
    assert plan.selected_sources == {}
    assert plan.store_products[0].selling_price == 123_000
    assert "xác minh" in plan.store_products[0].stock_status.casefold()


def test_equivalent_sources_choose_cheapest_and_keep_backup() -> None:
    shop_item = product("shop_xoai_non", "1", "v", "CANVA PRO 1 NĂM", cost=40_000)
    other_item = product("api_2", "2", "", "CANVA PRO 1 NĂM", cost=25_000)
    shared_store = store(
        "SP-1",
        mappings=(
            mapping("SP-1", "shop_xoai_non", "1", "v"),
            mapping("SP-1", "api_2", "2", ""),
        ),
    )
    plan = build_catalog_plan(
        [shared_store],
        [shop_item, other_item],
        preferred_supplier_id="shop_xoai_non",
    )
    assert plan.selected_sources["SP-1"].supplier_id == "api_2"
    assert plan.backup_sources["SP-1"].supplier_id == "shop_xoai_non"
    assert plan.store_products[0].selling_price == round_pretty_vnd(quote_cost(25_000).selling)
    assert len(plan.blocking_issues) == 0


def test_locked_content_survives_api_change() -> None:
    item = product(name="TÊN API MỚI", description="Mô tả API mới")
    override = ContentOverride(
        locked_fields=frozenset({"display_name", "display_description"}),
        display_name="TÊN KHÁCH ĐÃ CHỐT",
        display_description="MÔ TẢ KHÁCH ĐÃ CHỐT",
    )
    mapped_store = store(
        mappings=(mapping("SP-1", "shop_xoai_non"),),
        content_override=override,
        display_name="TÊN CŨ",
        display_description="MÔ TẢ CŨ",
    )
    plan = build_catalog_plan([mapped_store], [item])
    result = plan.store_products[0]
    assert result.display_name == "TÊN KHÁCH ĐÃ CHỐT"
    assert result.display_description == "MÔ TẢ KHÁCH ĐÃ CHỐT"
    assert result.duration == "1 Năm"
    row = plan.customer_menu_rows()[0]
    assert row["PRODUCT"] == "TÊN KHÁCH ĐÃ CHỐT"
    assert row["DESCRIPTION"] == "MÔ TẢ KHÁCH ĐÃ CHỐT"


def test_warranty_is_supplier_owned_not_content_override() -> None:
    with pytest.raises(CatalogValidationError):
        ContentOverride(locked_fields=frozenset({"warranty"}))
    item = product(name="CANVA PRO 1 NĂM BHF")
    mapped_store = store(mappings=(mapping("SP-1", "shop_xoai_non"),))
    plan = build_catalog_plan([mapped_store], [item])
    assert plan.store_products[0].warranty == "BHF"

def test_price_and_stock_are_recomputed_each_sync() -> None:
    mapped_store = store(mappings=(mapping("SP-1", "shop_xoai_non"),))
    first = build_catalog_plan(
        [mapped_store],
        [product(cost=10_000, stock=7, available=True)],
    ).store_products[0]
    second = build_catalog_plan(
        [first],
        [product(cost=20_000, stock=2, available=True)],
    ).store_products[0]
    third = build_catalog_plan(
        [second],
        [product(cost=20_000, stock=0, available=False)],
    ).store_products[0]
    assert first.selling_price == round_pretty_vnd(quote_cost(10_000).selling)
    assert second.selling_price == round_pretty_vnd(quote_cost(20_000).selling)
    assert second.stock_status == "Còn hàng"
    assert second.stock_quantity == 2
    assert third.stock_status == "Hết hàng"
    row = third.customer_row()
    assert row["STOCK"] == 0
    assert row["STATUS"] == "SOLD OUT"


def test_customer_menu_has_no_internal_values() -> None:
    item = product("shop_xoai_non", "local_41", "local_41", "AI PRO")
    mapped_store = store(
        mappings=(mapping("SP-1", "shop_xoai_non", "local_41", "local_41"),),
        menu_code="SXN-24",
    )
    plan = build_catalog_plan([mapped_store], [item])
    row = plan.customer_menu_rows()[0]
    assert_customer_safe(
        row,
        forbidden_values=("local_41", "shop_xoai_non", "SupplierMapping", "cost_price"),
    )
    with pytest.raises(CatalogValidationError):
        assert_customer_safe({**row, "Supplier Product ID": "local_41"})
    with pytest.raises(CatalogValidationError):
        assert_customer_safe({**row, "PRODUCT": "shop_xoai_non|local_41|local_41"})


def test_new_supplier_product_is_reported_and_not_silently_dropped() -> None:
    item = product("api_2", "new-1", "", "SẢN PHẨM MỚI")
    plan = build_catalog_plan([], [item])
    assert NEW_SUPPLIER_PRODUCT in issue_codes(plan)
    assert plan.new_supplier_products == [item]
    assert len(plan.store_products) == 1
    assert plan.store_products[0].publication_status == REVIEW_REQUIRED
    assert plan.customer_menu_rows() == []
    assert len(plan.review_products) == 1

    published = build_catalog_plan([], [item], auto_publish_new=True)
    assert published.store_products[0].publication_status == PUBLISHED
    assert len(published.customer_menu_rows()) == 1
    assert published.customer_menu_rows()[0]["PRODUCT"] == "SẢN PHẨM MỚI"

def test_failed_supplier_preserves_price_name_and_stock_view() -> None:
    existing = store(
        name="TÊN ĐANG BÁN",
        selling_price=155_000,
        stock_quantity=9,
        mappings=(mapping("SP-1", "api_2"),),
    )
    failed = StaticSupplierAdapter("api_2", "API 2", error="HTTP 503")
    plan = CatalogSyncService([failed], [existing]).build_plan()
    result = plan.store_products[0]
    assert result.display_name == "TÊN ĐANG BÁN"
    assert result.selling_price == 155_000
    assert "xác minh" in result.stock_status.casefold()
    assert result.customer_row()["STOCK"] == ""
    assert result.customer_row()["STATUS"] == "CONTACT"
    assert SOURCE_UNAVAILABLE in issue_codes(plan)
    assert SUPPLIER_ERROR in issue_codes(plan)


def test_empty_catalog_does_not_zero_stock_or_drop_product() -> None:
    existing = store(
        selling_price=88_000,
        stock_quantity=12,
        mappings=(mapping("SP-1", "api_2"),),
    )
    empty = StaticSupplierAdapter(
        "api_2",
        "API 2",
        catalog_status="NASTELE_CATALOG_EMPTY",
    )
    plan = CatalogSyncService([empty], [existing]).build_plan()
    result = plan.store_products[0]
    assert result.selling_price == 88_000
    assert result.stock_status == "Cần xác minh"
    assert result.customer_row()["STOCK"] == ""
    assert "SUPPLIER_CATALOG_EMPTY" in issue_codes(plan)


def test_missing_source_read_does_not_delete_store_product() -> None:
    existing = store(mappings=(mapping("SP-1", "api_2"),))
    plan = build_catalog_plan([existing], [])
    assert len(plan.store_products) == 1
    assert NO_SOURCE_THIS_READ in issue_codes(plan)
    assert plan.store_products[0].stock_status == "Cần xác minh"


def test_duplicate_supplier_identity_is_reported_and_last_wins() -> None:
    first = product("api_2", "1", "", "BẢN ĐẦU")
    last = product("api_2", "1", "", "BẢN CUỐI")
    plan = build_catalog_plan([], [first, last])
    assert DUPLICATE_SUPPLIER_IDENTITY in issue_codes(plan)
    assert len(plan.supplier_products) == 1
    assert plan.supplier_products[0].normalized_product_name == "BẢN CUỐI"


def test_mapping_requires_controlled_key_not_name_only() -> None:
    with pytest.raises(CatalogValidationError):
        SupplierMapping("SP-1", "api_2")
    with pytest.raises(CatalogValidationError):
        SupplierMapping("SP-1", "api_2", supplier_variant_id="v1")
    with pytest.raises(CatalogValidationError):
        SupplierMapping("SP-1", "api_2", "1", "v1", source_key="also")

def test_one_source_mapped_to_two_store_products_is_a_conflict() -> None:
    item = product("api_2", "1", "", "SẢN PHẨM")
    first = store(
        "SP-1",
        "SẢN PHẨM",
        menu_code="SXN-01",
        mappings=(mapping("SP-1", "api_2", "1", ""),),
    )
    second = store(
        "SP-2",
        "SẢN PHẨM KHÁC",
        menu_code="SXN-02",
        mappings=(mapping("SP-2", "api_2", "1", ""),),
    )
    plan = build_catalog_plan([first, second], [item])
    assert MAPPING_CONFLICT in issue_codes(plan)
    assert plan.selected_sources == {}


def test_projected_rows_only_touch_menu_columns_A_to_H() -> None:
    grid = [
        list(MENU_HEADERS),
        ["SXN-01", "SẢN PHẨM CŨ", "mô tả", "1 Năm", "BHF", 100_000, 1, "LOW STOCK"],
        ["", "", "", "", "", "", "", ""],
    ]
    mapped_store = store(mappings=(mapping("SP-1", "shop_xoai_non"),))
    plan = build_catalog_plan([mapped_store], [product()])
    customer_row = plan.customer_menu_rows()[0]
    # `Nguồn` chỉ là metadata của engine MENU hiện tại, không nằm trong customer row.
    engine_row = {**customer_row, "Nguồn": "Shop Xoài Non"}
    cells, placed, _notes = plan_menu_cells(grid, [engine_row], [], 0)
    assert placed == 1
    assert cells
    for cell_range, _value in cells:
        column = "".join(char for char in cell_range.split("!")[-1] if char.isalpha())
        assert column in {"A", "B", "C", "D", "E", "F", "G", "H"}


def test_existing_provider_adapter_reuses_phase42_offer_model() -> None:
    class Provider:
        supplier_id = "shop_xoai_non"
        supplier_name = "Shop Xoài Non"
        warnings = ["ghi chú provider"]
        catalog_status = ""

        def fetch(self) -> list[NormalizedOffer]:
            return [
                NormalizedOffer(
                    supplier_id="shop_xoai_non",
                    supplier_name="Shop Xoài Non",
                    source_product_id="local_41",
                    source_variant_id="local_41",
                    source_product_name="AI PRO",
                    normalized_product_name="AI PRO",
                    description="Mô tả",
                    product_attributes={},
                    cost_price_vnd=100_000,
                    available=True,
                    available_quantity=7,
                    quantity_presence="value",
                    raw_data_reference="shop:local_41",
                    warnings=["cảnh báo offer"],
                )
            ]

    result = ExistingProviderAdapter(Provider()).fetch()
    assert result.ok
    assert result.products[0].supplier_product_id == "local_41"
    assert result.products[0].supplier_variant_id == "local_41"
    assert result.products[0].identity_key == "shop_xoai_non|id|local_41|local_41"
    assert result.products[0].warnings == ("cảnh báo offer",)
    assert result.warnings == ("ghi chú provider",)


def test_different_warranty_conditions_are_not_merged() -> None:
    bhf = product("shop_xoai_non", "1", "v", "CANVA PRO 1 NĂM", description="BHF")
    kbh = product("api_2", "2", "", "CANVA PRO 1 NĂM", description="KBH")
    shared_store = store(
        mappings=(
            mapping("SP-1", "shop_xoai_non", "1", "v"),
            mapping("SP-1", "api_2", "2", ""),
        ),
        selling_price=111_000,
    )
    plan = build_catalog_plan([shared_store], [bhf, kbh])
    assert SOURCE_CONDITION_CONFLICT in issue_codes(plan)
    assert plan.selected_sources == {}
    assert plan.store_products[0].selling_price == 111_000


def test_different_versions_are_not_merged() -> None:
    new_version = product("shop_xoai_non", "1", "v", "MICROSOFT OFFICE 2021")
    old_version = product("api_2", "2", "", "MICROSOFT OFFICE 2019")
    shared_store = store(
        mappings=(
            mapping("SP-1", "shop_xoai_non", "1", "v"),
            mapping("SP-1", "api_2", "2", ""),
        ),
    )
    plan = build_catalog_plan([shared_store], [new_version, old_version])
    assert plan.selected_sources == {}
    assert plan.issues
    assert "xác minh" in plan.store_products[0].stock_status.casefold()
