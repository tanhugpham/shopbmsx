"""HTTP client cho Shop Xoài Non Customer API v1.

Đường POST duy nhất được phép là báo giá /products/{id}/quote.
POST /orders và mọi đường /delivery bị chặn trước khi mở kết nối.
"""

from __future__ import annotations

import json
import logging
import posixpath
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, unquote, urlparse

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt

from shop_integration.config import Settings
from shop_integration.errors import (
    ApiError,
    AuthenticationError,
    AuthorizationError,
    ConfigError,
    DeliveryAccessDisabledError,
    NotFoundError,
    PurchaseDisabledError,
    RateLimitError,
    ResponseValidationError,
    RetryableApiError,
    RetryableTimeoutError,
    RetryableTransportError,
    ServerError,
)
from shop_integration.models import MeSummary, OrderView, Quote, WalletSummary
from shop_integration.money import MoneyFormatError, parse_optional_count, parse_vnd

logger = logging.getLogger("shop_integration.api_client")

MAX_PAGES = 100
MAX_PAGE_SIZE = 200
def normalize_api_path(path: str) -> str:
    raw = unquote(path.split("?", 1)[0].strip())
    if raw.startswith("http://") or raw.startswith("https://"):
        raw = urlparse(raw).path
    if not raw.startswith("/"):
        raw = "/" + raw
    if raw == "/api/v1":
        raw = "/"
    elif raw.startswith("/api/v1/"):
        raw = raw[len("/api/v1") :]
    normalized = posixpath.normpath(raw)
    if normalized == ".":
        normalized = "/"
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    return normalized


def assert_operation_allowed(method: str, path: str) -> None:
    """Chặn mua hàng, giao tài khoản, và mọi POST không phải báo giá."""

    verb = method.upper()
    normalized = normalize_api_path(path)
    parts = [part for part in normalized.split("/") if part]
    if "delivery" in parts:
        raise DeliveryAccessDisabledError(
            "Đã chặn việc lấy thông tin tài khoản đã giao (GET /orders/{id}/delivery)."
        )
    if verb == "POST" and normalized == "/orders":
        raise PurchaseDisabledError(
            "Đã chặn tạo đơn. Hệ thống không gọi POST /orders và không trừ ví."
        )
    if verb == "POST":
        if not _is_quote_path(normalized):
            raise PurchaseDisabledError(
                "Chỉ cho phép POST báo giá /products/{id}/quote. Mọi POST khác bị chặn."
            )
        return
    if verb != "GET":
        raise PurchaseDisabledError(f"Phương thức {verb} không được phép.")


def _is_quote_path(normalized: str) -> bool:
    parts = [part for part in normalized.split("/") if part]
    return len(parts) == 3 and parts[0] == "products" and parts[2] == "quote"


def validate_resource_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ApiError(f"Thiếu {label}.")
    if any(char in value for char in "/\\?#"):
        raise ApiError(f"{label} chứa ký tự không hợp lệ.")
    return value


def parse_retry_after(header_value: str | None) -> float | None:
    if not header_value:
        return None
    text = header_value.strip()
    try:
        seconds = float(text)
    except ValueError:
        seconds = None
    else:
        return None if seconds < 0 else seconds
    try:
        moment = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    delta = (moment - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, delta)


def retry_wait_seconds(
    retry_after: float | None,
    attempt_number: int,
    *,
    cap: float = 60.0,
) -> float:
    """Ưu tiên Retry-After, có trần 60 giây. Không có header thì lùi theo cấp số nhân."""

    if retry_after is not None:
        return min(max(float(retry_after), 0.0), cap)
    exponent = max(attempt_number - 1, 0)
    return min(0.5 * (2**exponent), 8.0)


def _page_signature(items: list[Any]) -> str:
    markers: list[str] = []
    for item in items:
        if isinstance(item, dict) and item.get("id") is not None:
            markers.append(str(item.get("id")))
        else:
            markers.append(json.dumps(item, sort_keys=True, default=str))
    return "|".join(markers)


def _string_keys(payload: Any) -> list[str]:
    if isinstance(payload, dict):
        return [str(key) for key in payload.keys()]
    return []


def summarize_me(payload: Mapping[str, Any]) -> MeSummary:
    data = payload.get("data")
    return MeSummary(top_level_keys=_string_keys(payload), data_keys=_string_keys(data))


def summarize_wallet(payload: Mapping[str, Any]) -> WalletSummary:
    """Không đoán tên trường khi phản hồi có nhiều số."""

    data = payload.get("data") if "data" in payload else payload
    keys = _string_keys(data)
    amount: int | None = None
    amount_key: str | None = None
    if isinstance(data, int) and not isinstance(data, bool):
        amount = data
        amount_key = "data"
    elif isinstance(data, dict):
        integers = [
            (str(key), value)
            for key, value in data.items()
            if isinstance(value, int) and not isinstance(value, bool)
        ]
        if len(integers) == 1:
            amount_key, amount = integers[0]
    return WalletSummary(
        amount=amount,
        amount_key=amount_key,
        data_keys=keys,
        confirmed=False,
    )


def order_view(item: Mapping[str, Any]) -> OrderView:
    raw_id = item.get("order_id")
    if raw_id is None:
        raw_id = item.get("id")
    if raw_id is None:
        raw_id = item.get("checkout_id")
    status = item.get("status")
    return OrderView(
        order_id=str(raw_id) if raw_id is not None else None,
        status=status if isinstance(status, str) else None,
    )


def parse_quote_payload(data: Mapping[str, Any]) -> Quote:
    if "unit_price" not in data or "total" not in data:
        raise ResponseValidationError("Báo giá thiếu unit_price hoặc total.")
    try:
        unit_price = parse_vnd(data.get("unit_price"))
        total = parse_vnd(data.get("total"))
        quantity = parse_optional_count(data.get("quantity")) if "quantity" in data else None
        available = (
            parse_optional_count(data.get("available_quantity"))
            if "available_quantity" in data
            else None
        )
    except MoneyFormatError as exc:
        raise ResponseValidationError(str(exc)) from None
    can_purchase = data.get("can_purchase")
    if can_purchase is not None and not isinstance(can_purchase, bool):
        raise ResponseValidationError("Trường can_purchase phải là true hoặc false.")
    product_id = data.get("product_id")
    return Quote(
        product_id=str(product_id) if product_id is not None else None,
        quantity=quantity,
        unit_price=unit_price,
        total=total,
        available_quantity=available,
        can_purchase=can_purchase if isinstance(can_purchase, bool) else None,
    )


class ShopApiClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        max_attempts: int = 4,
        wait: Callable[..., float] | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        api_key = settings.shop_xoai_non_api_key.get_secret_value().strip()
        if not api_key:
            raise ConfigError("Thiếu SHOP_XOAI_NON_API_KEY.")
        self._settings = settings
        self._api_key = api_key
        self._base_url = settings.shop_xoai_non_base_url.rstrip("/")
        self._max_attempts = max(1, max_attempts)
        self._wait = wait or self._default_wait
        self.notices: list[str] = []
        self._client = httpx.Client(
            headers={
                "x-api-key": api_key,
                "Accept": "application/json",
                "User-Agent": "shopxoainon-integration/1.0",
            },
            timeout=timeout
            or httpx.Timeout(settings.request_timeout_seconds, connect=10.0),
            transport=transport,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ShopApiClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def get_me(self) -> MeSummary:
        payload = self._send("GET", "/me", retry=True)
        return summarize_me(payload)

    def get_wallet(self) -> WalletSummary:
        payload = self._send("GET", "/wallet/balance", retry=True)
        return summarize_wallet(payload)

    def list_products(
        self,
        *,
        limit: int = MAX_PAGE_SIZE,
        offset: int = 0,
        search: str | None = None,
    ) -> list[Any]:
        page_size = _clamp_limit(limit)
        if offset < 0:
            raise ApiError("offset phải lớn hơn hoặc bằng 0.")
        params: dict[str, Any] = {"limit": page_size, "offset": offset}
        if search:
            params["search"] = search
        payload = self._send("GET", "/products", params=params, retry=True)
        data = payload.get("data")
        if not isinstance(data, list):
            keys = ", ".join(_string_keys(payload)) or "(không có khóa)"
            raise ResponseValidationError(
                f"Danh sách sản phẩm thiếu mảng data. Khóa nhận được: {keys}."
            )
        return data

    def list_all_products(
        self,
        *,
        limit: int = MAX_PAGE_SIZE,
        search: str | None = None,
    ) -> list[Any]:
        page_size = _clamp_limit(limit)
        offset = 0
        collected: list[Any] = []
        seen: set[str] = set()
        self.notices = []
        for page_number in range(1, MAX_PAGES + 1):
            page = self.list_products(limit=page_size, offset=offset, search=search)
            if not page:
                break
            signature = _page_signature(page)
            if signature in seen:
                self.notices.append(
                    "Đã dừng phân trang vì API trả lại một trang sản phẩm đã nhận."
                )
                break
            seen.add(signature)
            collected.extend(page)
            if len(page) < page_size:
                break
            offset += len(page)
            if page_number == MAX_PAGES:
                self.notices.append(
                    f"Đã đạt giới hạn {MAX_PAGES} trang. Danh sách có thể chưa đủ."
                )
        return collected

    def get_product(self, product_id: str) -> dict[str, Any]:
        safe_id = validate_resource_id(product_id, "mã sản phẩm")
        payload = self._send("GET", f"/products/{quote(safe_id, safe='')}", retry=True)
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        if "id" in payload:
            return dict(payload)
        keys = ", ".join(_string_keys(payload)) or "(không có khóa)"
        raise ResponseValidationError(
            "Chi tiết sản phẩm không có đối tượng data. "
            f"Khóa nhận được: {keys}. Cấu trúc này chưa được tài liệu mô tả đầy đủ."
        )

    def list_orders(self) -> list[OrderView]:
        payload = self._send("GET", "/orders", retry=True)
        return self._orders_from_payload(payload)

    def get_order(self, order_id: str) -> OrderView:
        safe_id = validate_resource_id(order_id, "mã đơn")
        payload = self._send("GET", f"/orders/{quote(safe_id, safe='')}", retry=True)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        if not isinstance(data, dict):
            raise ResponseValidationError("Chi tiết đơn hàng không phải đối tượng JSON.")
        return order_view(data)

    def quote(self, product_id: str, variant_id: str, quantity: int = 1) -> Quote:
        """Báo giá theo yêu cầu tường minh. Không được gọi trong đồng bộ định kỳ."""

        safe_product = validate_resource_id(product_id, "mã sản phẩm")
        safe_variant = validate_resource_id(variant_id, "mã biến thể")
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
            raise ApiError("Số lượng báo giá phải là số nguyên lớn hơn hoặc bằng 1.")
        body = {
            "variant_id": safe_variant,
            "quantity": quantity,
            "currency_code": "vnd",
        }
        payload = self._send(
            "POST",
            f"/products/{quote(safe_product, safe='')}/quote",
            json_body=body,
            retry=False,
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ResponseValidationError("Báo giá thiếu đối tượng data.")
        return parse_quote_payload(data)

    def _orders_from_payload(self, payload: Mapping[str, Any]) -> list[OrderView]:
        if "data" not in payload:
            keys = ", ".join(_string_keys(payload)) or "(không có khóa)"
            raise ResponseValidationError(
                "Danh sách đơn hàng không có trường data. "
                f"Khóa nhận được: {keys}. Schema GET /orders chưa được OpenAPI mô tả."
            )
        data = payload.get("data")
        if not isinstance(data, list):
            raise ResponseValidationError("Trường data của danh sách đơn hàng không phải mảng.")
        views: list[OrderView] = []
        for item in data:
            if isinstance(item, dict):
                views.append(order_view(item))
        return views

    def _send(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        retry: bool,
    ) -> dict[str, Any]:
        assert_operation_allowed(method, path)
        if not retry:
            return self._request_once(method, path, params=params, json_body=json_body)
        retrying = Retrying(
            stop=stop_after_attempt(self._max_attempts),
            wait=self._wait,
            retry=retry_if_exception_type(RetryableApiError),
            reraise=True,
            before_sleep=self._before_sleep,
        )
        for attempt in retrying:
            with attempt:
                return self._request_once(method, path, params=params, json_body=json_body)
        raise ApiError("Không nhận được phản hồi từ Shop Xoài Non.")

    def _request_once(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None,
        json_body: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        url = self._url(path)
        try:
            response = self._client.request(method, url, params=params, json=json_body)
        except httpx.TimeoutException:
            raise RetryableTimeoutError("Hết thời gian chờ khi gọi Shop Xoài Non.") from None
        except httpx.TransportError:
            raise RetryableTransportError("Không kết nối được tới Shop Xoài Non.") from None
        logger.info("HTTP %s %s -> %s", method.upper(), normalize_api_path(path), response.status_code)
        if 300 <= response.status_code < 400:
            raise ApiError(
                "API trả về chuyển hướng. Yêu cầu đã bị dừng để không gửi API key sang địa chỉ khác."
            )
        if response.status_code >= 400:
            raise self._error_from_status(response)
        return self._parse_json(response)

    def _url(self, path: str) -> str:
        suffix = path if path.startswith("/") else f"/{path}"
        return f"{self._base_url}{suffix}"

    def _error_from_status(self, response: httpx.Response) -> ApiError:
        status = response.status_code
        if status == 401:
            return AuthenticationError("API key không hợp lệ hoặc bị thiếu (HTTP 401).")
        if status == 403:
            return AuthorizationError("Không đủ quyền cho thao tác này (HTTP 403).")
        if status == 404:
            return NotFoundError("Không tìm thấy dữ liệu trên Shop Xoài Non (HTTP 404).")
        if status == 429:
            retry_after = parse_retry_after(response.headers.get("Retry-After"))
            return RateLimitError(
                "Shop Xoài Non đang giới hạn tần suất (HTTP 429).",
                retry_after=retry_after,
            )
        if status == 408 or 500 <= status <= 599:
            return ServerError(f"Shop Xoài Non tạm thời không phản hồi (HTTP {status}).")
        return ApiError(f"Yêu cầu bị từ chối (HTTP {status}).")

    def _parse_json(self, response: httpx.Response) -> dict[str, Any]:
        if not response.content:
            raise ResponseValidationError("API trả về nội dung rỗng.")
        try:
            payload = response.json()
        except json.JSONDecodeError:
            raise ResponseValidationError("API trả về JSON không hợp lệ.") from None
        if not isinstance(payload, dict):
            raise ResponseValidationError("API trả về JSON không phải đối tượng.")
        return payload

    @staticmethod
    def _default_wait(retry_state: Any) -> float:
        outcome = retry_state.outcome
        exc = outcome.exception() if outcome is not None else None
        retry_after = getattr(exc, "retry_after", None)
        return retry_wait_seconds(retry_after, retry_state.attempt_number)

    @staticmethod
    def _before_sleep(retry_state: Any) -> None:
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        logger.warning(
            "Thử lại lần %s sau lỗi tạm thời: %s",
            retry_state.attempt_number,
            type(exc).__name__ if exc else "Unknown",
        )


def _clamp_limit(limit: int) -> int:
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ApiError("limit phải là số nguyên.")
    if limit < 1:
        raise ApiError("limit phải lớn hơn hoặc bằng 1.")
    return min(limit, MAX_PAGE_SIZE)
