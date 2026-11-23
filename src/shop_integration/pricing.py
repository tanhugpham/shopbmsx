"""Tính lợi nhuận trên giá gốc. Không tính trên giá bán cũ.

Quy tắc làm tròn: khoản lợi nhuận theo tỷ lệ được làm tròn nửa lên
(ROUND_HALF_UP) về VND nguyên trước khi cộng vào giá gốc.
0,5 VND thành 1 VND. Mức lợi nhuận cố định ở khoảng giữa không bị làm tròn thêm.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

MIDDLE_PROFIT_MIN = 30000
MIDDLE_PROFIT_MAX = 40000


@dataclass(frozen=True)
class PricingPolicy:
    """Markup trên giá vốn, không phải margin trên doanh thu."""

    rate_low: Decimal = Decimal("0.30")
    rate_high: Decimal = Decimal("0.30")
    middle_min: int = 100000
    middle_max: int = 200000
    middle_profit: int = 35000


@dataclass(frozen=True)
class PriceQuote:
    cost: int | None
    profit: int | None
    selling: int | None
    markup_percent: str
    error: str


def policy_from_settings(settings: object) -> PricingPolicy:
    return PricingPolicy(
        rate_low=Decimal(settings.pricing_rate_low),
        rate_high=Decimal(settings.pricing_rate_high),
        middle_min=int(settings.pricing_middle_min),
        middle_max=int(settings.pricing_middle_max),
        middle_profit=int(settings.pricing_middle_profit),
    )


def validate_middle_profit(value: int) -> int:
    if value < MIDDLE_PROFIT_MIN or value > MIDDLE_PROFIT_MAX:
        raise ValueError("PRICING_MIDDLE_PROFIT phải từ 30000 đến 40000.")
    return value


def validate_rate(value: Decimal) -> Decimal:
    if value < 0 or value > 1:
        raise ValueError("Tỷ lệ lợi nhuận phải từ 0 đến 1.")
    return value


def quote_cost(cost: int | None, policy: PricingPolicy | None = None) -> PriceQuote:
    """Tính từ giá gốc. Không nhận giá bán đã cộng lợi nhuận."""

    rules = policy or PricingPolicy()
    if cost is None or isinstance(cost, bool) or not isinstance(cost, int) or cost <= 0:
        return PriceQuote(None, None, None, "", "Thiếu giá gốc")
    if cost < rules.middle_min:
        profit = _round_vnd(Decimal(cost) * rules.rate_low)
    elif cost <= rules.middle_max:
        profit = rules.middle_profit
    else:
        profit = _round_vnd(Decimal(cost) * rules.rate_high)
    selling = cost + profit
    percent = (Decimal(profit) / Decimal(cost) * Decimal(100)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    return PriceQuote(cost, profit, selling, f"{percent:.2f}", "")


def _round_vnd(amount: Decimal) -> int:
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def round_pretty_vnd(amount: int) -> int:
    """Làm tròn lên tới mốc chẵn để giá bán dễ đọc. Mốc đã chẵn thì giữ nguyên.

    Không dùng cho giá gốc. Giá sau lợi nhuận không bị hạ.
    Dưới 1.000 đồng: mốc 100. Từ 1.000 đến dưới 10.000: mốc 500.
    Từ 10.000 đồng: mốc 1.000.
    """

    if isinstance(amount, bool) or not isinstance(amount, int):
        raise TypeError("Số tiền làm tròn phải là số nguyên VND.")
    if amount <= 0:
        return amount
    if amount < 1000:
        step = 100
    elif amount < 10000:
        step = 500
    else:
        step = 1000
    remainder = amount % step
    if remainder == 0:
        return amount
    return amount + (step - remainder)
