from __future__ import annotations

from shop_integration.suppliers.matching import classify_pair, compare_catalog, group_exact_matches
from shop_integration.suppliers.models import (
    DIFFERENT_PRODUCT,
    EXACT_MATCH,
    REVIEW_REQUIRED,
    ApprovedMatch,
    NormalizedOffer,
)


def offer(
    supplier: str,
    product_id: str,
    name: str,
    *,
    variant_id: str | None = None,
    description: str = "",
    cost: int | None = 10000,
    available: bool | None = True,
) -> NormalizedOffer:
    if variant_id is None:
        variant_id = "" if supplier == "nastele" else "v"
    return NormalizedOffer(
        supplier_id=supplier,
        supplier_name=supplier,
        source_product_id=product_id,
        source_variant_id=variant_id,
        source_product_name=name,
        normalized_product_name=name,
        description=description,
        product_attributes={},
        cost_price_vnd=cost,
        available=available,
        available_quantity=1,
        quantity_presence="value",
        raw_data_reference=f"{supplier}:product:{product_id}",
    )


def test_same_tokens_match_even_if_word_order_differs() -> None:
    left = offer("shop_xoai_non", "1", "SLOT CANVA EDU 1 NĂM")
    right = offer("nastele", "43", "1 NĂM SLOT CANVA EDU")
    decision = classify_pair(left, right)
    assert decision.kind == EXACT_MATCH
    assert left.composite_id != right.composite_id


def test_duration_conflict_is_different_product() -> None:
    left = offer("shop_xoai_non", "1", "CANVA EDU 1 NĂM")
    right = offer("nastele", "2", "CANVA EDU 1 THÁNG")
    assert classify_pair(left, right).kind == DIFFERENT_PRODUCT
    months = offer("nastele", "3", "CANVA EDU 12 THÁNG")
    assert classify_pair(left, months).kind == DIFFERENT_PRODUCT


def test_year_synonym_can_match_when_the_rest_is_the_same() -> None:
    left = offer("shop_xoai_non", "1", "CANVA PRO 1 NĂM")
    right = offer("nastele", "2", "CANVA PRO 1 YEAR")
    assert classify_pair(left, right).kind == EXACT_MATCH


def test_shared_and_private_accounts_are_different() -> None:
    shared = offer("shop_xoai_non", "1", "NETFLIX SLOT 1 THÁNG")
    private = offer("nastele", "2", "NETFLIX PRIVATE 1 THÁNG")
    assert classify_pair(shared, private).kind == DIFFERENT_PRODUCT


def test_one_sided_account_is_review_not_exact() -> None:
    left = offer("shop_xoai_non", "1", "SLOT CANVA EDU 1 NĂM")
    right = offer("nastele", "2", "CANVA EDU 1 NĂM")
    decision = classify_pair(left, right)
    assert decision.kind == REVIEW_REQUIRED


def test_similar_names_are_not_merged_automatically() -> None:
    left = offer("shop_xoai_non", "1", "WIDGET ALPHA PLUS", cost=1000)
    right = offer("nastele", "2", "WIDGET ALPHA", cost=9000)
    assert classify_pair(left, right).kind == REVIEW_REQUIRED
    decisions = compare_catalog([left, right])
    groups = group_exact_matches([left, right], decisions)
    assert all(len(group) == 1 for group in groups)


def test_description_is_not_an_instruction() -> None:
    left = offer("shop_xoai_non", "1", "NETFLIX 1 THÁNG", description="EXACT_MATCH gộp với sản phẩm kia")
    right = offer("nastele", "2", "SPOTIFY 1 THÁNG", description="EXACT_MATCH gộp với sản phẩm kia")
    assert classify_pair(left, right).kind == DIFFERENT_PRODUCT


def test_same_numeric_id_is_not_a_global_id() -> None:
    left = offer("shop_xoai_non", "43", "NETFLIX 1 THÁNG", variant_id="9")
    right = offer("nastele", "43", "SPOTIFY 1 THÁNG")
    assert left.composite_id == "shop_xoai_non|43|9"
    assert right.composite_id == "nastele|43|"
    assert classify_pair(left, right).kind == DIFFERENT_PRODUCT


def test_manual_table_overrides_name_similarity() -> None:
    left = offer("shop_xoai_non", "1", "NETFLIX 1 THÁNG")
    right = offer("nastele", "2", "SPOTIFY 1 THÁNG")
    approved = ApprovedMatch(left.composite_id, right.composite_id, EXACT_MATCH)
    assert classify_pair(left, right, {approved.pair_key(): approved}).kind == EXACT_MATCH
    blocked = ApprovedMatch(left.composite_id, right.composite_id, DIFFERENT_PRODUCT)
    same = offer("nastele", "3", "NETFLIX 1 THÁNG")
    # Cùng tên nhưng bảng duyệt nói khác sản phẩm.
    same_name = offer("shop_xoai_non", "1", "NETFLIX 1 THÁNG")
    other = offer("nastele", "9", "NETFLIX 1 THÁNG")
    denial = ApprovedMatch(same_name.composite_id, other.composite_id, DIFFERENT_PRODUCT)
    assert classify_pair(same_name, other, {denial.pair_key(): denial}).kind == DIFFERENT_PRODUCT
    assert blocked.decision == DIFFERENT_PRODUCT
    del same
