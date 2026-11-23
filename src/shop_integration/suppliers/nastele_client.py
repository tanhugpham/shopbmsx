"""Client đọc Nastele. Chỉ GET danh sách và chi tiết sản phẩm.

POST /api/partner/orders và mọi đường đơn hàng bị chặn trước khi mở kết nối.
Giới hạn tài liệu là 60 request mỗi phút cho mỗi key.
"""

from __future__ import annotations

import logging
import posixpath
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt

from shop_integration.api_client import parse_retry_after, retry_wait_seconds
from shop_integration.config import Settings
from shop_integration.errors import (
    ApiError,
    AuthenticationError,
    AuthorizationError,
    ConfigError,
    NotFoundError,
    PurchaseDisabledError,
    RateLimitError,
    ResponseValidationError,
    RetryableApiError,
    RetryableTimeoutError,
    RetryableTransportError,
    ServerError,
)
from shop_integration.suppliers.models import NASTELE_CATALOG_EMPTY

logger = logging.getLogger("shop_integration.nastele")

_PRODUCT_FIELDS = ("id", "name", "price", "description", "category_id", "stock", "wholesale_tiers")
_TIER_FIELDS = ("minQuantity", "unitPrice")


def assert_nastele_read(method: str, path: str) -> str:
    """Chỉ cho phép hai đường đọc sản phẩm. Chặn đơn và tài khoản trước HTTP."""

    verb = method.upper()
    raw = path.split("?", 1)[0].strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        raise PurchaseDisabledError("Không gọi Nastele bằng URL tuyệt đối.")
    if not raw.startswith("/"):
        raw = "/" + raw
    normalized = posixpath.normpath(raw)
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    parts = [part for part in normalized.split("/") if part]
    lowered = [part.casefold() for part in parts]
    if any(part in {"orders", "order", "delivery", "accounts", "account"} for part in lowered):
        raise PurchaseDisabledError(
            "Đã chặn đơn hàng Nastele. Hệ thống không gọi API đơn và không lưu tài khoản."
        )
    if verb != "GET":
        raise PurchaseDisabledError("Nastele chỉ cho phép GET danh sách và chi tiết sản phẩm.")
    if normalized == "/api/partner/products":
        return normalized
    if (
        len(parts) == 4
        and parts[0] == "api"
        and parts[1] == "partner"
        and parts[2] == "products"
        and parts[3].isdigit()
    ):
        return normalized
    raise PurchaseDisabledError("Đường dẫn Nastele này không nằm trong phạm vi đọc sản phẩm.")


def interpret_product_list(payload: dict[str, Any]) -> tuple[list[Any], str, list[str]]:
    """Phân loại danh sách đã được tài liệu mô tả.

    Mảng data rỗng là catalog rỗng, không phải chứng cứ sản phẩm đã bị xóa.
    Phong bì phân trang rỗng cũng không được đổi thành sản phẩm giả.
    Hình dạng khác thì từ chối, không đoán trường.
    """

    if not isinstance(payload, dict):
        raise ResponseValidationError("Cấu trúc danh sách Nastele thay đổi. Không phân tích thành sản phẩm.")
    if payload.get("success") is not True:
        raise ResponseValidationError("Nastele trả về success khác true.")
    if "data" not in payload:
        raise ResponseValidationError("Cấu trúc danh sách Nastele thay đổi. Không phân tích thành sản phẩm.")
    data = payload.get("data")
    if isinstance(data, list):
        if len(data) == 0:
            return (
                [],
                NASTELE_CATALOG_EMPTY,
                [
                    "NASTELE_CATALOG_EMPTY: API trả mảng data rỗng. Đây không phải bằng chứng mọi sản phẩm đã bị xóa."
                ],
            )
        return list(data), "OK", []
    if isinstance(data, dict) and _empty_pagination(data):
        return (
            [],
            NASTELE_CATALOG_EMPTY,
            [
                "NASTELE_CATALOG_EMPTY: phân trang sản phẩm rỗng. Không tạo sản phẩm giả và không coi là đã xóa."
            ],
        )
    raise ResponseValidationError("Cấu trúc danh sách Nastele thay đổi. Không phân tích thành sản phẩm.")


def _empty_pagination(data: dict[str, Any]) -> bool:
    items = data.get("items")
    pagination = data.get("pagination")
    if not isinstance(items, list) or items or not isinstance(pagination, dict):
        return False
    total = pagination.get("total")
    if isinstance(total, bool) or (isinstance(total, int) and total > 0):
        return False
    return True


def public_product(item: dict[str, Any]) -> dict[str, Any]:
    """Chỉ giữ trường sản phẩm đã có trong tài liệu. Bỏ account, password, 2fa."""

    cleaned = {key: item[key] for key in _PRODUCT_FIELDS if key in item}
    tiers = cleaned.get("wholesale_tiers")
    if isinstance(tiers, list):
        safe_tiers: list[dict[str, Any]] = []
        for tier in tiers:
            if isinstance(tier, dict):
                safe_tiers.append({key: tier[key] for key in _TIER_FIELDS if key in tier})
        cleaned["wholesale_tiers"] = safe_tiers
    elif tiers is not None:
        cleaned["wholesale_tiers"] = tiers
    return cleaned


class NasteleClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        max_attempts: int = 3,
        wait: Callable[..., float] | None = None,
        sleep: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        api_key = settings.nastele_api_key.get_secret_value().strip()
        if not api_key:
            raise ConfigError("Thiếu NASTELE_API_KEY.")
        self._api_key = api_key
        self._base_url = settings.nastele_base_url.rstrip("/")
        self._limit = settings.nastele_rate_limit_per_minute
        self._max_attempts = max(1, max_attempts)
        self._wait = wait or _default_wait
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic
        self._hits: deque[float] = deque()
        self.notices: list[str] = []
        self.catalog_status = ""
        self._client = httpx.Client(
            headers={
                "X-API-Key": api_key,
                "Accept": "application/json",
                "User-Agent": "shopxoainon-integration-nastele/1.0",
            },
            timeout=timeout or httpx.Timeout(settings.request_timeout_seconds, connect=10.0),
            transport=transport,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> NasteleClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def list_products(self) -> list[dict[str, Any]]:
        """Một GET là cả danh mục. Tài liệu không mô tả phân trang sản phẩm."""

        payload = self._send("GET", "/api/partner/products")
        data, status, notices = interpret_product_list(payload)
        self.catalog_status = status
        self.notices.extend(notices)
        products: list[dict[str, Any]] = []
        dropped = 0
        for item in data:
            if isinstance(item, dict):
                products.append(public_product(item))
            else:
                dropped += 1
        if dropped:
            self.notices.append(f"Bỏ qua {dropped} phần tử sản phẩm không phải đối tượng.")
        return products

    def get_product(self, product_id: str) -> dict[str, Any]:
        cleaned = _digits(product_id)
        payload = self._send("GET", f"/api/partner/products/{cleaned}")
        data = self._data(payload, expect="dict")
        if not isinstance(data, dict):
            raise ResponseValidationError("Chi tiết sản phẩm Nastele thiếu đối tượng data.")
        return public_product(data)

    def place_order(self, product_id: str, quantity: int) -> None:
        """Luôn từ chối. Không có cờ môi trường nào mở được đường này."""

        del product_id, quantity
        assert_nastele_read("POST", "/api/partner/orders")

    def _data(self, payload: dict[str, Any], *, expect: str) -> Any:
        if payload.get("success") is not True:
            raise ResponseValidationError("Nastele trả về success khác true.")
        data = payload.get("data")
        if expect == "list" and not isinstance(data, list):
            raise ResponseValidationError("Danh sách sản phẩm Nastele thiếu mảng data.")
        if expect == "dict" and not isinstance(data, dict):
            raise ResponseValidationError("Chi tiết sản phẩm Nastele thiếu đối tượng data.")
        return data

    def _send(self, method: str, path: str) -> dict[str, Any]:
        normalized = assert_nastele_read(method, path)
        retrying = Retrying(
            stop=stop_after_attempt(self._max_attempts),
            wait=self._wait,
            retry=retry_if_exception_type(RetryableApiError),
            reraise=True,
            before_sleep=_before_sleep,
        )
        for attempt in retrying:
            with attempt:
                return self._request_once(method, normalized)
        raise ApiError("Không nhận được phản hồi từ Nastele.")

    def _request_once(self, method: str, path: str) -> dict[str, Any]:
        self._acquire()
        try:
            response = self._client.request(method, f"{self._base_url}{path}")
        except httpx.TimeoutException:
            raise RetryableTimeoutError("Hết thời gian chờ khi gọi Nastele.") from None
        except httpx.TransportError:
            raise RetryableTransportError("Không kết nối được tới Nastele.") from None
        logger.info("HTTP %s %s -> %s", method.upper(), path, response.status_code)
        if 300 <= response.status_code < 400:
            raise ApiError(
                "Nastele trả về chuyển hướng. Yêu cầu đã bị dừng để không gửi API key sang địa chỉ khác."
            )
        if response.status_code >= 400:
            raise self._error_from_status(response)
        return _parse_json(response)

    def _acquire(self) -> None:
        while True:
            now = self._clock()
            while self._hits and now - self._hits[0] >= 60:
                self._hits.popleft()
            if len(self._hits) < self._limit:
                self._hits.append(now)
                return
            delay = 60 - (now - self._hits[0])
            if delay <= 0:
                self._hits.popleft()
                continue
            self._sleep(delay)

    @staticmethod
    def _error_from_status(response: httpx.Response) -> ApiError:
        status = response.status_code
        if status == 401:
            return AuthenticationError("API key Nastele không hợp lệ hoặc bị thiếu (HTTP 401).")
        if status == 403:
            return AuthorizationError("Tài khoản Nastele không đủ quyền hoặc bị tạm khóa (HTTP 403).")
        if status == 404:
            return NotFoundError("Không tìm thấy sản phẩm Nastele (HTTP 404).")
        if status == 429:
            return RateLimitError(
                "Nastele đang giới hạn tần suất (HTTP 429).",
                retry_after=parse_retry_after(response.headers.get("Retry-After")),
            )
        if status == 408 or 500 <= status <= 599:
            return ServerError(f"Nastele tạm thời không phản hồi (HTTP {status}).")
        return ApiError(f"Nastele từ chối yêu cầu (HTTP {status}).")


def _digits(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or not value.strip().isdigit():
        raise ApiError("Mã sản phẩm Nastele phải là chữ số.")
    return str(int(value.strip()))


def _parse_json(response: httpx.Response) -> dict[str, Any]:
    if not response.content:
        raise ResponseValidationError("Nastele trả về nội dung rỗng.")
    try:
        payload = response.json()
    except ValueError:
        raise ResponseValidationError("Nastele trả về JSON không hợp lệ.") from None
    if not isinstance(payload, dict):
        raise ResponseValidationError("Nastele trả về JSON không phải đối tượng.")
    return payload


def _default_wait(retry_state: Any) -> float:
    outcome = retry_state.outcome
    exc = outcome.exception() if outcome is not None else None
    return retry_wait_seconds(getattr(exc, "retry_after", None), retry_state.attempt_number)


def _before_sleep(retry_state: Any) -> None:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    logger.warning(
        "Thử lại Nastele lần %s sau lỗi tạm thời: %s",
        retry_state.attempt_number,
        type(exc).__name__ if exc else "Unknown",
    )
