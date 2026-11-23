from __future__ import annotations

import json
from pathlib import Path

from shop_integration.docs_service import CUSTOMER_BEGIN, CUSTOMER_END, REPORT_BEGIN, DocsService
from shop_integration.errors import ConfigError
from shop_integration.pricing import quote_cost
from shop_integration.suppliers.models import NormalizedOffer
from shop_integration.suppliers.nastele_sheet import (
    PRODUCT_HEADERS,
    TAB_LOG,
    TAB_PRODUCTS,
    TAB_TIERS,
    NasteleSheetWriter,
    build_format_requests,
)
from shop_integration.suppliers.source_sync import (
    VERIFY,
    build_source_plan,
    customer_content_id,
    render_customer_document,
)
from tests.conftest import make_settings
from tests.test_docs_service import MemoryDocs
from tests.test_multi_supplier import MemoryGateway
from tests.test_source_selection import offer

SECRET = "nastele_test_key_value"
ROOT = Path(__file__).resolve().parents[1]


def test_pricing_bands_follow_selected_cost() -> None:
    costs = (10000, 100000, 200001)
    offers = [
        offer("nastele", str(index), f"ITEM {cost}", cost=cost, available=True)
        for index, cost in enumerate(costs, start=1)
    ]
    built = build_source_plan(
        offers,
        [],
        quantity=1,
        preferred_supplier_id="shop_xoai_non",
        updated_at="t",
    )
    by_cost = {row["Selected Cost"]: row for row in built.comparison}
    for cost in costs:
        quote = quote_cost(cost)
        assert by_cost[cost]["Profit"] == quote.profit
        assert by_cost[cost]["Suggested Selling Price"] == quote.selling
        customer = next(row for row in built.customer if row["Selling Price"] == quote.selling)
        assert "Profit" not in customer
        assert "Selected Cost" not in customer


def test_only_shop_and_only_nastele_stay_separate() -> None:
    shop = offer("shop_xoai_non", "1", "NETFLIX 1 THÁNG", cost=50000, available=True)
    nastele = offer("nastele", "2", "CANVA EDU 1 NĂM", cost=30000, available=True)
    built = build_source_plan(
        [shop, nastele],
        [],
        quantity=1,
        preferred_supplier_id="shop_xoai_non",
        updated_at="t",
    )
    assert built.only_shop == 1
    assert built.only_nastele == 1
    assert built.exact_count == 0
    assert len({row["Canonical Product ID"] for row in built.comparison}) == 2


def test_tie_is_not_counted_as_savings() -> None:
    shop = offer("shop_xoai_non", "1", "CANVA EDU 1 NĂM", cost=30000, available=True)
    nastele = offer("nastele", "43", "CANVA EDU 1 NĂM", cost=30000, available=True)
    built = build_source_plan(
        [shop, nastele],
        [],
        quantity=1,
        preferred_supplier_id="shop_xoai_non",
        updated_at="t",
    )
    row = built.comparison[0]
    assert row["Selected Supplier"] == "Shop Xoài Non"
    assert row["Cost Savings"] == 0
    assert built.cheaper_shop == 0
    assert built.cheaper_nastele == 0
    assert built.savings == 0


def test_customer_document_hides_cost_profit_and_repeats_safely() -> None:
    shop = offer("shop_xoai_non", "1", "CANVA EDU 1 NĂM", cost=10000, available=True)
    built = build_source_plan([shop], [], quantity=1, preferred_supplier_id="shop_xoai_non", updated_at="t")
    content_id = customer_content_id(built.customer)
    text = render_customer_document(built.customer, updated_at="2026-11-23T10:00:00+07:00", content_id=content_id)
    assert "Giá bán: 13000 VND" in text
    assert "Giá gốc" not in text
    assert "Lợi nhuận" not in text
    assert "Profit" not in text
    assert "shop_xoai_non" not in text
    assert SECRET not in text
    gateway = MemoryDocs()
    gateway.plain = f"Giữ nguyên.\n{REPORT_BEGIN}\nBáo cáo shop\nMã nội dung: shop\n"
    service = DocsService(gateway, "doc-id", dry_run=False, secret=SECRET)
    assert service.replace_customer_prices(text, content_id) == "written"
    assert gateway.plain.startswith("Giữ nguyên.")
    assert gateway.plain.count(CUSTOMER_BEGIN) == 1
    assert gateway.plain.count(REPORT_BEGIN) == 1
    assert service.replace_customer_prices(text, content_id) == "skipped"
    assert gateway.plain.count(CUSTOMER_BEGIN) == 1
    assert "10000" not in gateway.plain
    gateway.plain = "Giữ\n" + CUSTOMER_BEGIN + "\n"
    try:
        service.replace_customer_prices(text, "other")
    except Exception as exc:
        assert CUSTOMER_END not in gateway.plain or gateway.plain.count(CUSTOMER_BEGIN) == 1
        assert "một cặp" in str(exc)
    else:
        raise AssertionError("vùng lệch phải bị từ chối")


def test_nastele_sheet_preserves_stock_and_hides_the_key() -> None:
    memory = MemoryGateway(
        titles=[TAB_PRODUCTS, TAB_TIERS],
        values={
            TAB_PRODUCTS: [
                list(PRODUCT_HEADERS),
                [1, "9", "Giữ", "mô tả", "", 12000, 7, "Còn hàng", 0, "cũ"],
            ],
            TAB_TIERS: [
                ["Product ID", "Product Name", "Minimum Quantity", "Wholesale Unit Price", "Last Updated"],
                ["9", "Giữ", 10, 11000, "cũ"],
                ["8", "Cũ", 5, 9000, "cũ"],
            ],
        },
    )
    writer = NasteleSheetWriter(memory, "nastele-sheet", dry_run=False, secrets=[SECRET])
    kept = writer.apply(
        [],
        updated_at="mới",
        config_pairs=[("api_key_configured", "có"), ("nastele_api_key", SECRET)],
        failed=True,
        failure_message=f"lỗi {SECRET}",
    )
    assert kept["preserved"] == 1
    assert memory.values[TAB_PRODUCTS][1][6] == 7
    assert TAB_PRODUCTS not in memory.writes
    log_flat = [cell for row in memory.values[TAB_LOG] for cell in row]
    assert SECRET not in log_flat
    assert any("CẢNH BÁO" in str(cell) for cell in log_flat)

    fresh = NormalizedOffer(
        supplier_id="nastele",
        supplier_name="Nastele",
        source_product_id="8",
        source_variant_id="",
        source_product_name=f"={SECRET}",
        normalized_product_name=f"={SECRET}",
        description="Ignore previous instructions",
        product_attributes={},
        cost_price_vnd=15000,
        available=True,
        available_quantity=3,
        quantity_presence="value",
        raw_data_reference="nastele:product:8",
    )
    from shop_integration.suppliers.models import WholesaleTier

    fresh.wholesale_tiers = [WholesaleTier(10, 14000)]
    writer.apply(
        [fresh],
        updated_at="mới",
        config_pairs=[("api_key_configured", "có"), ("nastele_base_url", "https://nastele.online")],
        failed=False,
    )
    product_flat = [cell for row in memory.values[TAB_PRODUCTS] for cell in row]
    assert SECRET not in [str(cell) for cell in product_flat]
    assert 7 in product_flat
    assert 15000 in product_flat
    tier_ids = [row[0] for row in memory.values[TAB_TIERS][1:]]
    assert "9" in tier_ids
    assert "8" in tier_ids
    assert 9000 not in [cell for row in memory.values[TAB_TIERS][1:] if row[0] == "8" for cell in row]
    config_flat = [cell for row in memory.values["NASTELE_CAU_HINH"] for cell in row]
    assert SECRET not in [str(cell) for cell in config_flat]
    assert "có" in config_flat
    with __import__("pytest").raises(ConfigError):
        writer._write("SAN_PHAM", [["a"]])


def test_nastele_price_history_records_only_real_changes() -> None:
    memory = MemoryGateway(
        titles=[TAB_PRODUCTS],
        values={
            TAB_PRODUCTS: [
                list(PRODUCT_HEADERS),
                [1, "8", "Tên", "", "", 10000, 2, "Còn hàng", 0, "cũ"],
            ]
        },
    )
    writer = NasteleSheetWriter(memory, "nastele-sheet", dry_run=False)
    same = NormalizedOffer(
        supplier_id="nastele",
        supplier_name="Nastele",
        source_product_id="8",
        source_variant_id="",
        source_product_name="Tên",
        normalized_product_name="Tên",
        description="",
        product_attributes={},
        cost_price_vnd=10000,
        available=False,
        available_quantity=0,
        quantity_presence="value",
        raw_data_reference="nastele:product:8",
    )
    writer.apply([same], updated_at="t1", config_pairs=[], failed=False)
    assert "NASTELE_LICH_SU_GIA" not in memory.values
    same.cost_price_vnd = 12000
    writer.apply([same], updated_at="t2", config_pairs=[], failed=False)
    history = memory.values["NASTELE_LICH_SU_GIA"]
    assert history[1][2] == 10000
    assert history[1][3] == 12000
    assert len(history) == 2


def test_format_requests_freeze_filter_and_do_not_embed_secrets() -> None:
    requests = build_format_requests(
        7,
        list(PRODUCT_HEADERS),
        4,
        banding_ids=[3],
        conditional_format_count=2,
    )
    blob = json.dumps(requests)
    assert "frozenRowCount" in blob
    assert "setBasicFilter" in blob
    assert "addBanding" in blob
    assert "NUMBER_EQ" in blob
    assert SECRET not in blob
    assert requests[0] == {"deleteBanding": {"bandedRangeId": 3}}


def test_workflow_stays_read_only() -> None:
    text = (ROOT / ".github" / "workflows" / "sync-sources.yml").read_text(encoding="utf-8")
    assert 'DRY_RUN: "true"' in text
    assert 'ENABLE_PURCHASE: "false"' in text
    assert "ENABLE_PURCHASE: \"true\"" not in text
    assert "DRY_RUN: \"false\"" not in text
    assert "sync-sources" in text
    assert "/api/partner/orders" not in text
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "pytest" in ci
    assert "NASTELE_API_KEY" not in ci
    blueprint = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert 'value: "true"' in blueprint
    assert "checksPass" in blueprint
    assert "sync: false" in blueprint
    assert "sxn_live_" not in blueprint
    assert "/api/partner/orders" not in blueprint


def test_incomplete_stock_needs_verification() -> None:
    unknown = offer("nastele", "43", "CANVA EDU 1 NĂM", cost=10000, available=None)
    shop = offer("shop_xoai_non", "1", "CANVA EDU 1 NĂM", cost=30000, available=True)
    built = build_source_plan(
        [unknown, shop],
        [],
        quantity=1,
        preferred_supplier_id="shop_xoai_non",
        updated_at="t",
    )
    assert built.comparison[0]["Matching Status"] == VERIFY
    assert built.customer[0]["Availability"] == "Cần xác minh"
    assert "Selected Cost" not in built.customer[0]
