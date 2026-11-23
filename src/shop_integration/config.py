"""Cấu hình từ biến môi trường. Không có giá trị bí mật trong mã nguồn."""

from __future__ import annotations

from pathlib import Path

from decimal import Decimal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, DotEnvSettingsSource, SettingsConfigDict

from shop_integration.pricing import PricingPolicy, validate_middle_profit, validate_rate
from pydantic_settings.sources import PydanticBaseSettingsSource

# config.py nằm tại src/shop_integration/config.py.
# parents[0] là package, parents[1] là src, parents[2] là thư mục gốc dự án.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GOOGLE_CREDENTIALS_FILE = "./GOOGLE_CREDENTIALS_FILE.json"


def resolve_project_path(value: str) -> Path:
    """Đường dẫn tuyệt đối giữ nguyên. Đường dẫn tương đối tính từ PROJECT_ROOT."""

    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (PROJECT_ROOT / candidate).resolve()


def resolve_credentials_path(value: str | None) -> Path:
    text = "" if value is None else str(value).strip().strip('"').strip("'")
    if not text:
        text = DEFAULT_GOOGLE_CREDENTIALS_FILE
    return resolve_project_path(text)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    shop_xoai_non_base_url: str = "https://shopxoainon.online/api/v1"
    shop_xoai_non_api_key: SecretStr = Field(default=SecretStr(""))
    google_spreadsheet_id: str = ""
    google_document_id: str = ""
    google_credentials_file: str = DEFAULT_GOOGLE_CREDENTIALS_FILE
    app_env: str = "development"
    dry_run: bool = True
    enable_purchase: bool = False
    enable_telegram: bool = False
    request_timeout_seconds: float = 30.0
    pricing_rate_low: Decimal = Field(default_factory=lambda: PricingPolicy().rate_low)
    pricing_rate_high: Decimal = Field(default_factory=lambda: PricingPolicy().rate_high)
    pricing_middle_min: int = Field(default_factory=lambda: PricingPolicy().middle_min)
    pricing_middle_max: int = Field(default_factory=lambda: PricingPolicy().middle_max)
    pricing_middle_profit: int = Field(default_factory=lambda: PricingPolicy().middle_profit)
    nastele_base_url: str = "https://nastele.online"
    nastele_api_key: SecretStr = Field(default=SecretStr(""))
    preferred_supplier_id: str = "shop_xoai_non"
    comparison_quantity: int = 1
    nastele_rate_limit_per_minute: int = 60
    nastele_spreadsheet_id: str = ""
    last_spreadsheet_id: str = ""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        del dotenv_settings
        dotenv = DotEnvSettingsSource(
            settings_cls,
            env_file=PROJECT_ROOT / ".env",
            env_file_encoding="utf-8",
        )
        return init_settings, env_settings, dotenv, file_secret_settings

    @field_validator("google_credentials_file", mode="before")
    @classmethod
    def credentials_file_path(cls, value: object) -> str:
        if value is None:
            return DEFAULT_GOOGLE_CREDENTIALS_FILE
        text = str(value).strip().strip('"').strip("'")
        if not text:
            return DEFAULT_GOOGLE_CREDENTIALS_FILE
        return text

    @property
    def resolved_credentials_path(self) -> Path:
        return resolve_credentials_path(self.google_credentials_file)

    @field_validator("shop_xoai_non_base_url")
    @classmethod
    def https_base_url(cls, value: str) -> str:
        cleaned = value.strip().rstrip("/")
        if not cleaned.startswith("https://"):
            raise ValueError("SHOP_XOAI_NON_BASE_URL phải dùng HTTPS.")
        return cleaned

    @field_validator("request_timeout_seconds")
    @classmethod
    def positive_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("REQUEST_TIMEOUT_SECONDS phải lớn hơn 0.")
        return value

    @field_validator("pricing_rate_low", "pricing_rate_high", mode="before")
    @classmethod
    def pricing_rate(cls, value: object) -> Decimal:
        return validate_rate(Decimal(str(value)))

    @field_validator("pricing_middle_profit")
    @classmethod
    def pricing_profit(cls, value: int) -> int:
        return validate_middle_profit(value)

    @model_validator(mode="after")
    def pricing_band(self) -> "Settings":
        if self.pricing_middle_min <= 0 or self.pricing_middle_max < self.pricing_middle_min:
            raise ValueError("PRICING_MIDDLE_MIN và PRICING_MIDDLE_MAX không hợp lệ.")
        return self

    @property
    def purchase_allowed(self) -> bool:
        """Luôn False. ENABLE_PURCHASE không mở được đường tạo đơn."""

        return False

    @property
    def telegram_active(self) -> bool:
        """Luôn False. Giai đoạn này chỉ có giao diện kiến trúc."""

        return False

    @field_validator("nastele_base_url")
    @classmethod
    def https_nastele_url(cls, value: str) -> str:
        cleaned = value.strip().rstrip("/")
        if not cleaned.startswith("https://"):
            raise ValueError("NASTELE_BASE_URL phải dùng HTTPS.")
        return cleaned

    @field_validator("preferred_supplier_id")
    @classmethod
    def known_preferred_supplier(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned not in {"shop_xoai_non", "nastele"}:
            raise ValueError("PREFERRED_SUPPLIER_ID chỉ nhận shop_xoai_non hoặc nastele.")
        return cleaned

    @field_validator("comparison_quantity", "nastele_rate_limit_per_minute", mode="before")
    @classmethod
    def reject_bool_counts(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("Không nhận giá trị boolean.")
        return value

    @field_validator("comparison_quantity")
    @classmethod
    def comparison_quantity_at_least_one(cls, value: int) -> int:
        if isinstance(value, bool) or value < 1:
            raise ValueError("COMPARISON_QUANTITY phải là số nguyên lớn hơn hoặc bằng 1.")
        return value

    @field_validator("nastele_spreadsheet_id")
    @classmethod
    def strip_nastele_spreadsheet_id(cls, value: str) -> str:
        return str(value or "").strip()

    @field_validator("last_spreadsheet_id")
    @classmethod
    def strip_last_spreadsheet_id(cls, value: str) -> str:
        return str(value or "").strip()

    @field_validator("nastele_rate_limit_per_minute")
    @classmethod
    def nastele_rate_cap(cls, value: int) -> int:
        if isinstance(value, bool) or value < 1 or value > 60:
            raise ValueError("NASTELE_RATE_LIMIT_PER_MINUTE phải từ 1 đến 60.")
        return value

    def secret_values(self) -> list[str]:
        found: list[str] = []
        for secret in (self.shop_xoai_non_api_key, self.nastele_api_key):
            raw = secret.get_secret_value().strip()
            if len(raw) >= 8 and raw not in found:
                found.append(raw)
        return found
