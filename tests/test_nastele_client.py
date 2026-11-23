from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from shop_integration.errors import (
    ApiError,
    AuthenticationError,
    AuthorizationError,
    ConfigError,
    PurchaseDisabledError,
    ResponseValidationError,
    RetryableTimeoutError,
)
from shop_integration.suppliers.nastele_client import NasteleClient, assert_nastele_read, public_product
from tests.conftest import load_fixture, make_settings

SECRET = "nastele_test_key_value"


def _settings(**overrides: object):
    values: dict[str, object] = {
        "nastele_api_key": SecretStr(SECRET),
        "nastele_base_url": "https://nastele.test",
        "nastele_rate_limit_per_minute": 60,
    }
    values.update(overrides)
    return make_settings(**values)


def _client(handler, **kwargs) -> NasteleClient:
    transport = httpx.MockTransport(handler)
    kwargs.setdefault("wait", lambda retry_state: 0)
    return NasteleClient(_settings(**kwargs.pop("settings", {})), transport=transport, **kwargs)


def test_list_sends_api_key_header_and_drops_accounts() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["header"] = request.headers["x-api-key"]
        seen["url"] = str(request.url)
        seen["method"] = request.method
        return httpx.Response(200, json=load_fixture("nastele_products.json"))

    with _client(handler) as client:
        products = client.list_products()

    assert seen["method"] == "GET"
    assert seen["url"] == "https://nastele.test/api/partner/products"
    assert seen["header"] == SECRET
    assert products[0]["id"] == 43
    assert "accounts" not in products[0]
    assert "must-not-store" not in str(products)


def test_missing_key_does_not_open_http() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Không được gọi mạng khi thiếu API key")

    with pytest.raises(ConfigError, match="Thiếu NASTELE_API_KEY"):
        NasteleClient(_settings(nastele_api_key=SecretStr("")), transport=httpx.MockTransport(handler))


def test_401_is_not_retried_and_body_is_not_echoed() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(401, json={"message": SECRET, "accounts": "secret-account"})

    with _client(handler, max_attempts=3) as client:
        with pytest.raises(AuthenticationError) as caught:
            client.list_products()

    assert calls["count"] == 1
    assert SECRET not in str(caught.value)
    assert "secret-account" not in str(caught.value)


def test_403_is_not_retried() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(403, text=SECRET)

    with _client(handler) as client:
        with pytest.raises(AuthorizationError) as caught:
            client.list_products()

    assert calls["count"] == 1
    assert SECRET not in str(caught.value)


def test_429_retries_without_copying_body() -> None:
    calls = {"count": 0}
    waits: list[float | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429, headers={"Retry-After": "12"}, json={"message": SECRET})
        return httpx.Response(200, json=load_fixture("nastele_products.json"))

    def wait(retry_state) -> float:
        exc = retry_state.outcome.exception()
        waits.append(getattr(exc, "retry_after", None))
        return 0

    with _client(handler, wait=wait, max_attempts=3) as client:
        products = client.list_products()

    assert calls["count"] == 2
    assert waits == [12]
    assert products[0]["price"] == 30000


def test_timeout_retries_without_key() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise httpx.ReadTimeout(f"timed out {SECRET}")

    with _client(handler, max_attempts=3) as client:
        with pytest.raises(RetryableTimeoutError) as caught:
            client.list_products()

    assert calls["count"] == 3
    assert SECRET not in str(caught.value)


def test_redirect_is_not_followed() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(302, headers={"Location": "https://evil.example/steal"})

    with _client(handler) as client:
        with pytest.raises(ApiError) as caught:
            client.list_products()

    assert calls["count"] == 1
    assert "evil.example" not in str(caught.value)
    assert SECRET not in str(caught.value)


def test_orders_and_post_never_open_http() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Không được gọi mạng cho đơn hàng")

    with _client(handler) as client:
        with pytest.raises(PurchaseDisabledError):
            client.place_order("43", 1)
        with pytest.raises(PurchaseDisabledError):
            client._send("GET", "/api/partner/orders")
        with pytest.raises(PurchaseDisabledError):
            client._send("GET", "/api/partner/products/43/../../orders")
        with pytest.raises(PurchaseDisabledError):
            client._send("POST", "/api/partner/products")
        with pytest.raises(PurchaseDisabledError):
            assert_nastele_read("GET", "/api/partner/products/43/orders")


def test_detail_uses_digits_and_public_fields_only() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://nastele.test/api/partner/products/43"
        body = load_fixture("nastele_products.json")["data"][0]
        return httpx.Response(200, json={"success": True, "data": body})

    with _client(handler) as client:
        product = client.get_product("0043")
        with pytest.raises(ApiError):
            client.get_product("../43")

    assert product["id"] == 43
    assert "password" not in product
    assert public_product({"id": 1, "name": "A", "password": SECRET, "accounts": []}) == {
        "id": 1,
        "name": "A",
    }


def test_false_success_does_not_echo_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "data": {"password": SECRET}})

    with _client(handler) as client:
        with pytest.raises(ResponseValidationError) as caught:
            client.list_products()

    assert SECRET not in str(caught.value)


def test_rate_limit_window_waits() -> None:
    calls = {"count": 0}
    clock = {"t": 0.0}
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(200, json=load_fixture("nastele_products.json"))

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["t"] += seconds

    with _client(
        handler,
        settings={"nastele_rate_limit_per_minute": 2},
        clock=lambda: clock["t"],
        sleep=sleep,
    ) as client:
        client.list_products()
        client.list_products()
        client.list_products()

    assert calls["count"] == 3
    assert slept and slept[0] >= 59


def test_nastele_settings_reject_bad_values() -> None:
    with pytest.raises(ValidationError):
        make_settings(nastele_base_url="http://nastele.online")
    with pytest.raises(ValidationError):
        make_settings(nastele_rate_limit_per_minute=61)
    with pytest.raises(ValidationError):
        make_settings(preferred_supplier_id="other")
    with pytest.raises(ValidationError):
        make_settings(comparison_quantity=0)
    with pytest.raises(ValidationError):
        make_settings(comparison_quantity=True)
