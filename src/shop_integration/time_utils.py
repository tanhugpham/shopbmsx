"""Mốc thời gian theo giờ Việt Nam (UTC+7, không có giờ mùa)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

VIETNAM_TZ = timezone(timedelta(hours=7))


def format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=VIETNAM_TZ)
    return value.astimezone(VIETNAM_TZ).isoformat(timespec="seconds")


def now_vietnam() -> datetime:
    return datetime.now(VIETNAM_TZ)
