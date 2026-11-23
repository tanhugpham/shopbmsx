"""Giá VND là số nguyên. Không dùng float để lưu tiền."""

from __future__ import annotations

import re
from typing import Any


class MoneyFormatError(ValueError):
    """Giá không phải số nguyên VND."""


_INTEGER_TEXT = re.compile(r"[0-9]+")


def parse_vnd(value: Any) -> int:
    """Chuyển một giá trị JSON đã biết là tiền VND thành int.

    Chấp nhận số nguyên JSON, chuỗi chữ số, và float chỉ khi phần thập phân bằng 0
    (một số bộ giải mã JSON trả 9500.0). Từ chối số lẻ và chuỗi có dấu phân tách.
    """

    if isinstance(value, bool) or value is None:
        raise MoneyFormatError("Giá VND không hợp lệ.")
    if isinstance(value, int):
        amount = value
    elif isinstance(value, float):
        if not value.is_integer():
            raise MoneyFormatError("Giá VND phải là số nguyên, không dùng số thập phân.")
        amount = int(value)
    elif isinstance(value, str):
        text = value.strip()
        if not _INTEGER_TEXT.fullmatch(text):
            raise MoneyFormatError("Giá VND phải là chuỗi chữ số nguyên, không có dấu phân tách.")
        amount = int(text)
    else:
        raise MoneyFormatError("Giá VND phải là số nguyên.")
    if amount < 0:
        raise MoneyFormatError("Giá VND không được âm.")
    return amount


def parse_optional_count(value: Any) -> int | None:
    """Số lượng tồn kho. None khi API không gửi trường. Giữ số 0."""

    if value is None:
        return None
    try:
        amount = parse_vnd(value)
    except MoneyFormatError as exc:
        raise MoneyFormatError("Số lượng không hợp lệ.") from exc
    return amount
