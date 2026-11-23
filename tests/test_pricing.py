from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from shop_integration.pricing import (
    PricingPolicy,
    policy_from_settings,
    quote_cost,
    round_pretty_vnd,
    validate_middle_profit,
)
from tests.conftest import make_settings


def test_bands_boundaries_and_half_up_rounding() -> None:
    assert _parts(10000) == (3000, 13000, "30.00")
    assert _parts(9999) == (3000, 12999, "30.00")
    assert _parts(99999) == (30000, 129999, "30.00")
    assert _parts(100000) == (35000, 135000, "35.00")
    assert _parts(150000) == (35000, 185000, "23.33")
    assert _parts(200000) == (35000, 235000, "17.50")
    assert _parts(200001) == (60000, 260001, "30.00")


def test_same_cost_is_not_marked_up_twice() -> None:
    first = quote_cost(9500)
    second = quote_cost(first.cost)
    assert first.profit == 2850
    assert first.selling == 12350
    assert second == first
    assert quote_cost(1000).selling == 1300
    assert quote_cost(3000).selling == 3900
    assert quote_cost(500).selling == 650


def test_invalid_cost_is_an_error_and_not_zero() -> None:
    for cost in (None, 0, -1, True, False, 1.5, "1000"):
        quote = quote_cost(cost)  # type: ignore[arg-type]
        assert quote.error == "Thiếu giá gốc"
        assert quote.cost is None
        assert quote.profit is None
        assert quote.selling is None
        assert quote.selling != 0


def test_middle_profit_range_and_exact_rate() -> None:
    assert validate_middle_profit(30000) == 30000
    assert validate_middle_profit(40000) == 40000
    with pytest.raises(ValueError):
        validate_middle_profit(25000)
    with pytest.raises(ValueError):
        validate_middle_profit(45000)
    settings = make_settings(pricing_rate_low="0.30", pricing_middle_profit=32000)
    policy = policy_from_settings(settings)
    assert settings.pricing_rate_low == Decimal("0.30")
    assert policy.rate_low == Decimal("0.30")
    assert policy.middle_profit == 32000
    assert quote_cost(150000, policy).profit == 32000
    assert quote_cost(10000, policy).profit == 3000
    with pytest.raises(ValidationError):
        make_settings(pricing_middle_profit=25000)
    with pytest.raises(ValidationError):
        make_settings(pricing_middle_min=0)
    with pytest.raises(ValidationError):
        make_settings(pricing_rate_high="1.5")


def test_pretty_round_lifts_to_a_clean_step_and_keeps_exact_markup() -> None:
    assert round_pretty_vnd(100) == 100
    assert round_pretty_vnd(130) == 200
    assert round_pretty_vnd(650) == 700
    assert round_pretty_vnd(1000) == 1000
    assert round_pretty_vnd(1300) == 1500
    assert round_pretty_vnd(1950) == 2000
    assert round_pretty_vnd(9999) == 10000
    assert round_pretty_vnd(102700) == 103000
    assert round_pretty_vnd(88400) == 89000
    assert round_pretty_vnd(203000) == 203000
    assert quote_cost(79000).selling == 102700
    assert quote_cost(168000).selling == 203000
    with pytest.raises(TypeError):
        round_pretty_vnd(True)  # type: ignore[arg-type]


def test_default_policy_matches_declared_settings() -> None:
    policy = PricingPolicy()
    assert policy.rate_low == Decimal("0.30")
    assert policy.rate_high == Decimal("0.30")
    assert policy.middle_min == 100000
    assert policy.middle_max == 200000
    assert policy.middle_profit == 35000
    settings = make_settings()
    assert policy_from_settings(settings) == policy


def _parts(cost: int) -> tuple[int | None, int | None, str]:
    quote = quote_cost(cost)
    return quote.profit, quote.selling, quote.markup_percent
