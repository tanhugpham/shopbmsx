from __future__ import annotations

import json

import httpx
import pytest

from shop_integration.api_client import (
    ShopApiClient,
    assert_operation_allowed,
    normalize_api_path,
    parse_retry_after,
    retry_wait_seconds,
)
from shop_integration.errors import (
    ApiError,
    AuthenticationError,
    AuthorizationError,
    ConfigError,
    DeliveryAccessDisabledError,
    NotFoundError,
    PurchaseDisabledError,
    ResponseValidationError,
    RetryableTimeoutError,
    RetryableTransportError,
    ServerError,
)
from shop_integration.money import MoneyFormatError, parse_vnd
from tests.conftest import client_for, load_fixture


def _json(payload: dict, status: int = 200, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(status, json=payload, headers=headers)


def test_valid_authentication_reads_me_without_values(settings) -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers["x-api-key"]
        seen["url"] = str(request.url)
        seen["method"] = request.method
        return _json(load_fixture("me_ok.json"))

    with client_for(settings, handler) as client:
        summary = client.get_me()

    assert seen["method"] == "GET"
    assert seen["url"] == "https://example.test/api/v1/me"
    assert seen["key"] == "sxn_live_test_key_value"
    assert summary.top_level_keys == ["data"]
    assert summary.data_keys == ["customer_id", "name"]
    dumped = summary.model_dump()
    assert "cus_test" not in json.dumps(dumped)
    assert "Không in ra" not in json.dumps(dumped)


def test_invalid_api_key_is_not_retried_and_not_echoed(settings) -> None:
    calls = {"count": 0}
    secret = "sxn_live_test_key_value"

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(401, json={"detail": secret})

    with client_for(settings, handler) as client:
        with pytest.raises(AuthenticationError) as caught:
            client.get_me()

    assert calls["count"] == 1
    assert secret not in str(caught.value)
    assert "401" in str(caught.value)


def test_missing_api_key_fails_before_http() -> None:
    from pydantic import SecretStr

    from tests.conftest import make_settings

    empty = make_settings(shop_xoai_non_api_key=SecretStr(""))

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Không được gọi mạng khi thiếu API key")

    with pytest.raises(ConfigError):
        client_for(empty, handler)


def test_timeout_is_retried_without_leaking_key(settings) -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise httpx.ReadTimeout("timed out sxn_live_test_key_value")

    with client_for(settings, handler, max_attempts=3) as client:
        with pytest.raises(RetryableTimeoutError) as caught:
            client.get_me()

    assert calls["count"] == 3
    assert "sxn_live_test_key_value" not in str(caught.value)


def test_connection_error_is_retried(settings) -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise httpx.ConnectError("offline")

    with client_for(settings, handler, max_attempts=2) as client:
        with pytest.raises(RetryableTransportError):
            client.get_wallet()

    assert calls["count"] == 2


def test_rate_limit_uses_retry_after(settings) -> None:
    calls = {"count": 0}
    waits: list[float | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429, headers={"Retry-After": "12"})
        return _json(load_fixture("me_ok.json"))

    def wait(retry_state) -> float:
        exc = retry_state.outcome.exception()
        waits.append(getattr(exc, "retry_after", None))
        return 0

    with client_for(settings, handler, wait=wait, max_attempts=3) as client:
        client.get_me()

    assert calls["count"] == 2
    assert waits == [12.0]


def test_server_error_is_retried_then_succeeds(settings) -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(503, text="<html>down</html>")
        return _json(load_fixture("products_empty.json"))

    with client_for(settings, handler) as client:
        assert client.list_products() == []

    assert calls["count"] == 2


def test_forbidden_and_not_found_are_not_retried(settings) -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if request.url.path.endswith("/missing"):
            return httpx.Response(404)
        return httpx.Response(403)

    with client_for(settings, handler) as client:
        with pytest.raises(AuthorizationError):
            client.get_me()
        with pytest.raises(NotFoundError):
            client._send("GET", "/missing", retry=True)

    assert calls["count"] == 2


def test_redirect_is_not_followed(settings) -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(302, headers={"Location": "https://evil.example/steal"})

    with client_for(settings, handler) as client:
        with pytest.raises(ApiError) as caught:
            client.get_me()

    assert calls["count"] == 1
    assert "API key" in str(caught.value)


def test_empty_product_list(settings) -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return _json(load_fixture("products_empty.json"))

    with client_for(settings, handler) as client:
        assert client.list_all_products() == []

    assert calls["count"] == 1


def test_multiple_product_pages_preserve_ids(settings) -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = request.url.params["offset"]
        calls.append((request.url.params["limit"], offset))
        if offset == "0":
            return _json(load_fixture("products_page1.json"))
        if offset == "2":
            return _json(load_fixture("products_page2.json"))
        return _json(load_fixture("products_empty.json"))

    with client_for(settings, handler) as client:
        items = client.list_all_products(limit=2)

    assert calls == [("2", "0"), ("2", "2")]
    assert [item["id"] for item in items] == ["local_81", "local_82", "local_083"]


def test_repeated_page_stops_pagination(settings) -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return _json(load_fixture("products_page1.json"))

    with client_for(settings, handler) as client:
        items = client.list_all_products(limit=2)

    assert calls["count"] == 2
    assert len(items) == 2
    assert client.notices


def test_search_parameter_is_forwarded(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["search"] == "office"
        return _json(load_fixture("products_empty.json"))

    with client_for(settings, handler) as client:
        client.list_products(search="office")


def test_malformed_json_is_not_retried(settings) -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(200, content=b"not-json", headers={"content-type": "application/json"})

    with client_for(settings, handler) as client:
        with pytest.raises(ResponseValidationError) as caught:
            client.get_me()

    assert calls["count"] == 1
    assert "JSON" in str(caught.value)


def test_product_list_without_data_array(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json({"items": []})

    with client_for(settings, handler) as client:
        with pytest.raises(ResponseValidationError) as caught:
            client.list_products()

    assert "data" in str(caught.value)
    assert "items" in str(caught.value)


def test_quote_success_and_no_retry_on_server_error(settings) -> None:
    calls = {"ok": 0, "fail": 0}

    def ok(request: httpx.Request) -> httpx.Response:
        calls["ok"] += 1
        body = json.loads(request.content.decode())
        assert request.method == "POST"
        assert str(request.url).endswith("/products/local_81/quote")
        assert body == {"variant_id": "local_81", "quantity": 1, "currency_code": "vnd"}
        return _json(load_fixture("quote_ok.json"))

    with client_for(settings, ok) as client:
        quote = client.quote("local_81", "local_81", 1)

    assert quote.unit_price == 9500
    assert quote.total == 9500
    assert quote.can_purchase is True
    assert isinstance(quote.unit_price, int)

    def fail(request: httpx.Request) -> httpx.Response:
        calls["fail"] += 1
        return httpx.Response(500)

    with client_for(settings, fail) as client:
        with pytest.raises(ServerError):
            client.quote("local_81", "local_81", 1)

    assert calls["fail"] == 1


def test_quote_rejects_bad_quantity_before_http(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Không được gọi mạng")

    with client_for(settings, handler) as client:
        with pytest.raises(ApiError):
            client.quote("local_81", "local_81", 0)


def test_purchase_and_delivery_never_hit_the_network(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Đã gọi mạng: {request.method} {request.url}")

    with client_for(settings, handler) as client:
        with pytest.raises(PurchaseDisabledError):
            client._send("POST", "/orders", json_body={"items": []}, retry=False)
        with pytest.raises(PurchaseDisabledError):
            client._send("POST", "/products/local_81/../../orders", json_body={}, retry=False)
        with pytest.raises(DeliveryAccessDisabledError):
            client._send("GET", "/orders/ord_1/delivery", retry=True)
        with pytest.raises(DeliveryAccessDisabledError):
            client._send("GET", "/api/v1/orders/ord_1/delivery", retry=True)


def test_orders_drop_delivery_secrets(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json(load_fixture("orders_with_secret.json"))

    with client_for(settings, handler) as client:
        orders = client.list_orders()

    dumped = orders[0].model_dump()
    assert dumped == {"order_id": "ord_1", "status": "completed"}
    assert "super-secret-password" not in json.dumps(dumped)


def test_wallet_amount_is_unconfirmed(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json(load_fixture("wallet_unknown.json"))

    with client_for(settings, handler) as client:
        wallet = client.get_wallet()

    assert wallet.confirmed is False
    assert wallet.amount == 15000
    assert wallet.amount_key == "unconfirmed_amount"

    def ambiguous(request: httpx.Request) -> httpx.Response:
        return _json({"data": {"a": 1, "b": 2}})

    with client_for(settings, ambiguous) as client:
        unclear = client.get_wallet()

    assert unclear.amount is None
    assert unclear.confirmed is False


def test_product_detail_accepts_data_object(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/products/local_81")
        return _json({"data": load_fixture("products_page1.json")["data"][0]})

    with client_for(settings, handler) as client:
        detail = client.get_product("local_81")

    assert detail["id"] == "local_81"


def test_retry_wait_respects_cap_and_backoff() -> None:
    assert retry_wait_seconds(120, 1) == 60
    assert retry_wait_seconds(12, 1) == 12
    assert retry_wait_seconds(None, 1) == 0.5
    assert retry_wait_seconds(None, 6) == 8


def test_retry_after_http_date_and_invalid() -> None:
    assert parse_retry_after(None) is None
    assert parse_retry_after("-5") is None
    assert parse_retry_after("not-a-date") is None
    assert parse_retry_after("Wed, 21 Oct 2099 07:28:00 GMT") > 0


def test_path_normalization_blocks_traversal() -> None:
    assert normalize_api_path("/products/local_81/../../orders") == "/orders"
    with pytest.raises(PurchaseDisabledError):
        assert_operation_allowed("POST", "/products/x/quote/../../orders")
    assert_operation_allowed("POST", "/products/local_81/quote")
    assert_operation_allowed("GET", "/products")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (9500, 9500),
        ("9500", 9500),
        (9500.0, 9500),
        (" 9500 ", 9500),
        (0, 0),
    ],
)
def test_price_parsing_accepts_integral_vnd(raw, expected) -> None:
    assert parse_vnd(raw) == expected
    assert isinstance(parse_vnd(raw), int)


@pytest.mark.parametrize("raw", [9500.5, "9.500", "9500.0", -1, True, None, "9500 vnd"])
def test_price_parsing_rejects_non_integers(raw) -> None:
    with pytest.raises(MoneyFormatError):
        parse_vnd(raw)


def test_client_repr_does_not_expose_key(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json(load_fixture("me_ok.json"))

    with client_for(settings, handler) as client:
        assert "sxn_live_test_key_value" not in repr(client)
        client.get_me()
