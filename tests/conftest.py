from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from shop_integration.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "shop_xoai_non_base_url": "https://example.test/api/v1",
        "shop_xoai_non_api_key": SecretStr("sxn_live_test_key_value"),
        "google_spreadsheet_id": "sheet-id",
        "google_document_id": "doc-id",
        "google_credentials_file": "",
        "app_env": "test",
        "dry_run": True,
        "enable_purchase": False,
        "enable_telegram": False,
        "request_timeout_seconds": 5.0,
        "nastele_api_key": SecretStr(""),
        "nastele_spreadsheet_id": "",
    }
    values.update(overrides)
    return Settings(**values, _env_file=None)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


def client_for(settings: Settings, handler, **kwargs):
    from shop_integration.api_client import ShopApiClient

    transport = httpx.MockTransport(handler)
    kwargs.setdefault("wait", lambda retry_state: 0)
    return ShopApiClient(settings, transport=transport, **kwargs)
