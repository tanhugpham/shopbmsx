from __future__ import annotations

import pytest

from shop_integration.pricing import quote_cost
from shop_integration.suppliers.models import NormalizedOffer, WholesaleTier
from shop_integration.suppliers.selection import eligible_unit_cost, select_group
from shop_integration.suppliers.source_sync import build_source_plan


def offer(
    supplier: str,
    product_id: str,
    name: str,
    *,
    cost: int | None,
    available: bool | None,
    tiers: list[tuple[int, int]] | None = None,
    variant_id: str | None = None,
) -> NormalizedOffer:
    if variant_id is None:
        variant_id = "" if supplier == "nastele" else "v"
    return NormalizedOffer(
        supplier_id=supplier,
        supplier_name="Nastele" if supplier == "nastele" else "Shop Xoài Non",
        source_product_id=product_id,
        source_variant_id=variant_id,
        source_product_name=name,
        normalized_product_name=name,
        description="",
        product_attributes={},
        cost_price_vnd=cost,
        available=available,
        available_quantity=1 if available else 0,
        quantity_presence="value",
        wholesale_tiers=[WholesaleTier(qty, price) for qty, price in tiers or []],
        raw_data_reference=f"{supplier}:{product_id}",
    )


def test_quantity_one_ignores_wholesale_tiers() -> None:
    item = offer(
        "nastele",
        "43",
        "SLOT CANVA EDU 1 NĂM",
        cost=30000,
        available=True,
        tiers=[(10, 28000), (25, 26000)],
    )
    cost, tier = eligible_unit_cost(item, 1)
    assert cost == 30000
    assert tier is None
    cost_10, tier_10 = eligible_unit_cost(item, 10)
    assert cost_10 == 28000
    assert tier_10 == 10
    cost_25, tier_25 = eligible_unit_cost(item, 25)
    assert cost_25 == 26000
    assert tier_25 == 25


def test_higher_wholesale_price_is_not_used() -> None:
    item = offer("nastele", "1", "CANVA", cost=30000, available=True, tiers=[(1, 50000)])
    cost, tier = eligible_unit_cost(item, 1)
    assert cost == 30000
    assert tier is None


def test_cheaper_out_of_stock_source_is_not_selected() -> None:
    cheap = offer("nastele", "43", "CANVA EDU 1 NĂM", cost=20000, available=False)
    shop = offer("shop_xoai_non", "1", "CANVA EDU 1 NĂM", cost=30000, available=True)
    choice = select_group([cheap, shop], quantity=1, preferred_supplier_id="shop_xoai_non")
    assert choice.selected is shop
    assert choice.selected_cost == 30000
    assert choice.selling_price == quote_cost(30000).selling
    assert choice.rejected_supplier_id == "nastele"
    assert choice.rejected_cost == 20000
    assert "hết hàng" in choice.reason


def test_unknown_availability_is_not_verified() -> None:
    unknown = offer("nastele", "43", "CANVA EDU 1 NĂM", cost=10000, available=None)
    shop = offer("shop_xoai_non", "1", "CANVA EDU 1 NĂM", cost=30000, available=True)
    choice = select_group([unknown, shop], quantity=1, preferred_supplier_id="shop_xoai_non")
    assert choice.selected is shop
    assert choice.rejected_supplier_id == "nastele"
    assert "chưa xác nhận" in choice.reason


def test_no_verified_source_does_not_invent_a_winner() -> None:
    left = offer("nastele", "43", "CANVA EDU 1 NĂM", cost=10000, available=False)
    right = offer("shop_xoai_non", "1", "CANVA EDU 1 NĂM", cost=30000, available=None)
    choice = select_group([left, right], quantity=1, preferred_supplier_id="shop_xoai_non")
    assert choice.selected is None
    assert choice.selling_price is None


def test_tie_uses_preferred_supplier() -> None:
    shop = offer("shop_xoai_non", "1", "CANVA EDU 1 NĂM", cost=30000, available=True)
    nastele = offer("nastele", "43", "CANVA EDU 1 NĂM", cost=30000, available=True)
    choice = select_group([nastele, shop], quantity=1, preferred_supplier_id="shop_xoai_non")
    assert choice.selected is shop
    assert choice.backup is nastele
    assert choice.cost_difference == 0
    flipped = select_group([nastele, shop], quantity=1, preferred_supplier_id="nastele")
    assert flipped.selected is nastele
    assert flipped.backup is shop


def test_exact_group_recalculates_selling_price_from_winner() -> None:
    shop = offer("shop_xoai_non", "1", "CANVA EDU 1 NĂM", cost=40000, available=True)
    nastele = offer(
        "nastele",
        "43",
        "CANVA EDU 1 NĂM",
        cost=30000,
        available=True,
        tiers=[(25, 26000)],
    )
    built = build_source_plan(
        [shop, nastele],
        [],
        quantity=1,
        preferred_supplier_id="shop_xoai_non",
        updated_at="2026-11-23T10:00:00+07:00",
    )
    assert built.review == []
    assert built.exact_count == 1
    assert built.review_count == 0
    assert built.selected_count == 1
    assert len(built.comparison) == 1
    row = built.comparison[0]
    quote = quote_cost(30000)
    assert row["Nastele Cost"] == 30000
    assert row["Shop Xoài Non Cost"] == 40000
    assert row["Selected Supplier"] == "Nastele"
    assert row["Selected Cost"] == 30000
    assert row["Cost Savings"] == 10000
    assert row["Profit"] == quote.profit
    assert row["Suggested Selling Price"] == quote.selling
    assert row["Matching Status"] == "EXACT_MATCH"
    assert "Profit" not in built.customer[0]
    assert built.customer[0]["Selling Price"] == quote.selling
    built_25 = build_source_plan(
        [shop, nastele],
        [],
        quantity=25,
        preferred_supplier_id="shop_xoai_non",
        updated_at="2026-11-23T10:00:00+07:00",
    )
    winner = built_25.comparison[0]
    assert winner["Nastele Cost"] == 26000
    assert winner["Selected Cost"] == 26000
    assert winner["Suggested Selling Price"] == quote_cost(26000).selling


def test_review_rows_are_not_price_compared() -> None:
    left = offer("shop_xoai_non", "1", "WIDGET ALPHA PLUS", cost=1000, available=True)
    right = offer("nastele", "2", "WIDGET ALPHA", cost=9000, available=True)
    built = build_source_plan(
        [left, right],
        [],
        quantity=1,
        preferred_supplier_id="shop_xoai_non",
        updated_at="2026-11-23T10:00:00+07:00",
    )
    assert built.exact_count == 0
    assert built.review_count == 1
    assert len(built.review) == 1
    assert built.review[0]["Proposed Match"] == "REVIEW_REQUIRED"
    assert "Review Status" not in built.review[0]
    costs = {row["Selected Supplier"]: row["Selected Cost"] for row in built.comparison}
    assert costs == {"Shop Xoài Non": 1000, "Nastele": 9000}
    assert all(row["Matching Status"] == "chờ đối chiếu" for row in built.comparison)
    assert len({row["Canonical Product ID"] for row in built.comparison}) == 2


def test_invalid_quantity_is_rejected() -> None:
    item = offer("nastele", "1", "CANVA", cost=1000, available=True)
    with pytest.raises(ValueError):
        eligible_unit_cost(item, 0)
    with pytest.raises(ValueError):
        eligible_unit_cost(item, True)  # type: ignore[arg-type]
