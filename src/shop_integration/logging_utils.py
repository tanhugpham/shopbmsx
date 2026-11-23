"""Nhật ký không được chứa API key hoặc khóa riêng tư."""

from __future__ import annotations

import logging

_PRIVATE_KEY_MARKER = "BEGIN PRIVATE KEY"


def redact(text: str, secrets: list[str]) -> str:
    redacted = text.replace(_PRIVATE_KEY_MARKER, "***")
    for secret in secrets:
        if secret and len(secret) >= 8:
            redacted = redacted.replace(secret, "***")
    return redacted


class RedactingFormatter(logging.Formatter):
    def __init__(self, fmt: str, secrets: list[str]) -> None:
        super().__init__(fmt)
        self._secrets = list(secrets)

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record), self._secrets)


def configure_logging(secrets: list[str]) -> None:
    logger = logging.getLogger("shop_integration")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter("%(levelname)s %(name)s: %(message)s", secrets))
    logger.handlers = [handler]
    for name in ("httpx", "httpcore", "googleapiclient", "google", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)
