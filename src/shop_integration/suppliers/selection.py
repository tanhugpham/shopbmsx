"""Chọn nguồn rẻ nhất trong nhóm EXACT_MATCH. Giá sỉ chỉ dùng khi đủ số lượng."""

from __future__ import annotations

from shop_integration.pricing import PricingPolicy, quote_cost
from shop_integration.suppliers.models import NormalizedOffer, SourceChoice, WholesaleTier


def eligible_unit_cost(offer: NormalizedOffer, quantity: int) -> tuple[int | None, int | None]:
    """Giá một đơn vị cho đúng số lượng dự kiến mua.

    Giá lẻ luôn được xét. Bậc sỉ chỉ được xét khi minQuantity nhỏ hơn hoặc bằng số lượng.
    Cột bậc sỉ chỉ có giá trị khi bậc đó thấp hơn giá lẻ.
    """

    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
        raise ValueError("Số lượng so sánh phải là số nguyên lớn hơn hoặc bằng 1.")
    retail = _positive(offer.cost_price_vnd)
    best = retail
    applied: int | None = None
    for tier in offer.wholesale_tiers:
        if not _tier_ok(tier) or tier.min_quantity > quantity:
            continue
        if best is None or tier.unit_price_vnd < best:
            best = tier.unit_price_vnd
            applied = tier.min_quantity
        elif applied is not None and tier.unit_price_vnd == best and tier.min_quantity < applied:
            applied = tier.min_quantity
    if retail is not None and best == retail:
        applied = None
    return best, applied


def select_group(
    offers: list[NormalizedOffer],
    *,
    quantity: int,
    preferred_supplier_id: str,
    policy: PricingPolicy | None = None,
) -> SourceChoice:
    """Chỉ nhận các offer đã được xác nhận là cùng một sản phẩm, hoặc một offer lẻ."""

    rules = policy or PricingPolicy()
    priced: list[tuple[NormalizedOffer, int, int | None]] = []
    for offer in offers:
        cost, tier = eligible_unit_cost(offer, quantity)
        if cost is None:
            continue
        priced.append((offer, cost, tier))
    available = [item for item in priced if item[0].available is True]
    if not available:
        return SourceChoice(
            selected=None,
            selected_cost=None,
            backup=None,
            backup_cost=None,
            cost_difference=None,
            selling_price=None,
            reason="Không có nguồn đã xác nhận còn hàng với giá hợp lệ.",
        )

    def sort_key(item: tuple[NormalizedOffer, int, int | None]) -> tuple[int, int, str]:
        offer, cost, _tier = item
        preferred = 0 if offer.supplier_id == preferred_supplier_id else 1
        return cost, preferred, offer.composite_id

    available.sort(key=sort_key)
    winner, winner_cost, winner_tier = available[0]
    backup_item = next(
        (item for item in available[1:] if item[0].supplier_id != winner.supplier_id),
        None,
    )
    backup = backup_item[0] if backup_item else None
    backup_cost = backup_item[1] if backup_item else None
    difference = backup_cost - winner_cost if backup_cost is not None else None
    rejected_id = ""
    rejected_cost: int | None = None
    unavailable = [item for item in priced if item[0].available is not True and item[1] < winner_cost]
    unavailable.sort(key=lambda item: (item[1], item[0].composite_id))
    if unavailable:
        rejected, rejected_cost, _tier = unavailable[0]
        rejected_id = rejected.supplier_id
    quote = quote_cost(winner_cost, rules)
    if len(offers) == 1:
        reason = "Chỉ có một nguồn."
    elif rejected_id:
        state = "hết hàng" if unavailable[0][0].available is False else "chưa xác nhận còn hàng"
        reason = f"Nguồn rẻ hơn đang {state}. Chọn nguồn còn hàng rẻ nhất."
    else:
        reason = "Giá đơn vị thấp nhất trong các nguồn còn hàng."
    return SourceChoice(
        selected=winner,
        selected_cost=winner_cost,
        backup=backup,
        backup_cost=backup_cost,
        cost_difference=difference,
        selling_price=quote.selling,
        reason=reason,
        rejected_supplier_id=rejected_id,
        rejected_cost=rejected_cost,
        applied_tier_quantity=winner_tier,
    )


def classify_offer(offer: NormalizedOffer, quantity: int) -> str:
    """Tách còn hàng khỏi đủ điều kiện chọn nguồn. Không suy hết hàng khi cờ không có."""

    cost, _tier = eligible_unit_cost(offer, quantity)
    if offer.available is True and cost is not None:
        return "in_stock_valid"
    if offer.available is True:
        return "in_stock_invalid_price"
    if offer.available is False:
        return "out_of_stock"
    return "unknown"


def is_selectable(offer: NormalizedOffer, quantity: int, preferred_supplier_id: str, policy: PricingPolicy | None = None) -> bool:
    choice = select_group(
        [offer],
        quantity=quantity,
        preferred_supplier_id=preferred_supplier_id,
        policy=policy,
    )
    return choice.selected is not None


def _positive(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _tier_ok(tier: WholesaleTier) -> bool:
    return (
        isinstance(tier.min_quantity, int)
        and not isinstance(tier.min_quantity, bool)
        and tier.min_quantity >= 1
        and isinstance(tier.unit_price_vnd, int)
        and not isinstance(tier.unit_price_vnd, bool)
        and tier.unit_price_vnd > 0
    )
