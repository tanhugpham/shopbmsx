"""Lỗi nghiệp vụ. Thông điệp dành cho người vận hành, bằng tiếng Việt."""


class IntegrationError(Exception):
    """Lỗi gốc của hệ thống tích hợp."""


class ConfigError(IntegrationError):
    """Cấu hình thiếu hoặc không hợp lệ."""


class ApiError(IntegrationError):
    """Shop Xoài Non từ chối hoặc trả về dữ liệu không dùng được."""


class AuthenticationError(ApiError):
    """HTTP 401. API key thiếu hoặc không hợp lệ."""


class AuthorizationError(ApiError):
    """HTTP 403. Key hợp lệ nhưng không đủ quyền."""


class NotFoundError(ApiError):
    """HTTP 404."""


class ResponseValidationError(ApiError):
    """JSON không đúng cấu trúc đã được tài liệu xác nhận."""


class PaginationError(ApiError):
    """Phân trang vượt giới hạn an toàn."""


class RetryableApiError(ApiError):
    """Lỗi tạm thời của thao tác đọc, có thể thử lại."""


class RateLimitError(RetryableApiError):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class RetryableTimeoutError(RetryableApiError):
    """Hết thời gian chờ."""


class RetryableTransportError(RetryableApiError):
    """Không kết nối được tới máy chủ."""


class ServerError(RetryableApiError):
    """HTTP 5xx hoặc 408 trên thao tác đọc."""


class PurchaseDisabledError(IntegrationError):
    """Tạo đơn và trừ ví bị khóa trong mã nguồn."""


class DeliveryAccessDisabledError(IntegrationError):
    """Không lấy thông tin tài khoản đã giao."""


class GoogleWorkspaceError(IntegrationError):
    """Lỗi Google Sheets hoặc Google Docs."""


class BackupError(IntegrationError):
    """Sao lưu thiếu, hỏng, hoặc chưa được kiểm tra toàn vẹn."""


class PartialSyncError(GoogleWorkspaceError):
    """Sheets hoặc Docs chưa xong. Không được xem là thành công toàn bộ."""


class TelegramDisabledError(IntegrationError):
    """Bộ điều hợp Telegram chưa được kích hoạt."""
