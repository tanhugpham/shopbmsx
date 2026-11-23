from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from shop_integration.api_client import ShopApiClient
from shop_integration.cli import build_parser
from shop_integration.config import Settings
from shop_integration.errors import ConfigError, DeliveryAccessDisabledError, PurchaseDisabledError
from shop_integration.google_auth import (
    DOCUMENTS_SCOPE,
    SPREADSHEETS_SCOPE,
    WORKSPACE_SCOPES,
    load_service_account_credentials,
    translate_google_error,
)
from shop_integration.logging_utils import RedactingFormatter, configure_logging, redact
from shop_integration.telegram_adapter import build_notifier
from shop_integration.telegram_adapter.interfaces import OutboundMessage
from tests.conftest import client_for, make_settings


SECRET = "sxn_live_test_key_value"


def test_settings_do_not_reveal_api_key() -> None:
    settings = make_settings()
    rendered = " ".join(
        [
            repr(settings),
            str(settings),
            json.dumps(settings.model_dump(mode="json")),
        ]
    )
    assert SECRET not in rendered
    assert settings.purchase_allowed is False
    assert settings.telegram_active is False


def test_log_formatter_redacts_key_and_private_key_marker() -> None:
    stream = io.StringIO()
    logger = logging.getLogger("shop_integration.security_test")
    logger.handlers = []
    logger.propagate = False
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter("%(message)s", [SECRET]))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.info("header %s", SECRET)
    logger.info("block BEGIN PRIVATE KEY")

    output = stream.getvalue()
    assert SECRET not in output
    assert "BEGIN PRIVATE KEY" not in output
    assert "***" in output
    assert redact(f"abc {SECRET}", [SECRET]) == "abc ***"


def test_configure_logging_silences_http_libraries(settings=None) -> None:
    configure_logging([SECRET])
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_purchase_flag_cannot_enable_orders() -> None:
    settings = make_settings(enable_purchase=True)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Không được gọi POST /orders")

    with client_for(settings, handler) as client:
        with pytest.raises(PurchaseDisabledError):
            client._send("POST", "/orders", json_body={"payment_method": "wallet"}, retry=False)
        with pytest.raises(DeliveryAccessDisabledError):
            client._send("GET", "/orders/ord_1/delivery", retry=False)

    assert settings.purchase_allowed is False


def test_cli_has_no_purchase_or_delivery_command() -> None:
    parser = build_parser()
    choices = set(parser._subparsers._group_actions[0].choices)
    assert {"ping", "products", "quote", "sync", "report"} <= choices
    assert choices.isdisjoint({"buy", "purchase", "mua", "delivery", "create-order", "order-create"})


def test_telegram_adapter_is_inactive_even_when_flag_is_true() -> None:
    settings = make_settings(enable_telegram=True)
    notifier = build_notifier(settings)
    with pytest.raises(Exception) as caught:
        notifier.send(OutboundMessage(chat_id="1", text="xin chào"))

    assert "Telegram" in str(caught.value)
    assert settings.telegram_active is False


def test_google_scopes_are_limited_to_sheets_and_docs() -> None:
    assert WORKSPACE_SCOPES == [SPREADSHEETS_SCOPE, DOCUMENTS_SCOPE]
    joined = " ".join(WORKSPACE_SCOPES)
    assert "drive" not in joined
    assert "gmail" not in joined


def test_credential_errors_do_not_echo_private_key(tmp_path: Path) -> None:
    user_file = tmp_path / "user.json"
    user_file.write_text(
        json.dumps({"type": "authorized_user", "token": "BEGIN PRIVATE KEY material-user"}),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as user_error:
        load_service_account_credentials(str(user_file), WORKSPACE_SCOPES)
    assert "BEGIN PRIVATE KEY" not in str(user_error.value)
    assert "material-user" not in str(user_error.value)

    broken = tmp_path / "broken.json"
    broken.write_text(
        json.dumps(
            {
                "type": "service_account",
                "client_email": "bot@example.iam.gserviceaccount.com",
                "private_key": "-----BEGIN PRIVATE KEY-----\nmaterial-sa\n-----END PRIVATE KEY-----\n",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as broken_error:
        load_service_account_credentials(str(broken), WORKSPACE_SCOPES)
    message = str(broken_error.value)
    assert "BEGIN PRIVATE KEY" not in message
    assert "material-sa" not in message


def test_google_status_messages_are_vietnamese() -> None:
    class Response:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

    assert "Không tìm thấy" in str(translate_google_error(Response(404)))
    assert "quyền sửa" in str(translate_google_error(Response(403)))


def test_client_follow_redirects_is_disabled(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {}})

    with client_for(settings, handler) as client:
        assert isinstance(client, ShopApiClient)
        assert client._client.follow_redirects is False


def test_https_is_required() -> None:
    with pytest.raises(Exception):
        make_settings(shop_xoai_non_base_url="http://shopxoainon.online/api/v1")


def test_short_secret_is_not_used_as_redaction_needle() -> None:
    settings = make_settings(shop_xoai_non_api_key=SecretStr("short"))
    assert settings.secret_values() == []
