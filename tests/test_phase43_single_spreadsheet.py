"""Phase 4.3B: một Google Spreadsheet, cấu hình sạch và an toàn ghi."""

from __future__ import annotations

import argparse
from dataclasses import replace

from shop_integration import cli
from shop_integration.cli import command_config_check
from shop_integration.combined_quote import (
    TAB_MENU,
    WRITE_TABS,
    CombinedQuoteWriter,
    shop_quote_rows,
)
from shop_integration.config import SINGLE_SPREADSHEET_ENV, Settings
from shop_integration.menu_sync import run_menu_sync
from shop_integration.pricing import PricingPolicy, quote_cost
from shop_integration.sheet_layout import ALL_TABS, LEGACY_TAB_NOTES
from shop_integration.suppliers.models import (
    EXACT_MATCH,
    NASTELE_CATALOG_EMPTY,
    SUPPLIER_NASTELE,
    SUPPLIER_SHOP,
    ProviderResult,
)
from shop_integration.suppliers.nastele_sheet import (
    NASTELE_TABS,
    TAB_LOG,
    TAB_PRODUCTS,
    NasteleSheetWriter,
)
from shop_integration.suppliers.source_sync import (
    SOURCE_TABS,
    _unique_offers,
    build_source_plan,
)
from shop_integration.workspace import Workspace, open_workspace
from tests.conftest import make_settings
from tests.test_combined_quote import _menu_book, _products
from tests.test_phase421_catalog import MemoryGateway, _shop_offer


class EmptyShop:
    def list_all_products(self) -> list:
        return []


def test_settings_use_one_spreadsheet_id() -> None:
    settings = make_settings(google_spreadsheet_id="  official-id  ")

    assert settings.google_spreadsheet_id == "official-id"
    assert settings.spreadsheet_id == "official-id"
    assert not hasattr(settings, "last_spreadsheet_id")
    assert not hasattr(settings, "nastele_spreadsheet_id")


def test_legacy_spreadsheet_env_is_ignored(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("shop_integration.config.PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("GOOGLE_SPREADSHEET_ID", "official")
    monkeypatch.setenv("LAST_SPREADSHEET_ID", "old-quote")
    monkeypatch.setenv("NASTELE_SPREADSHEET_ID", "old-nastele")

    settings = Settings(_env_file=None)

    assert settings.spreadsheet_id == "official"
    assert settings.legacy_spreadsheet_env() == [
        "NASTELE_SPREADSHEET_ID",
        "LAST_SPREADSHEET_ID",
    ]
    warnings = " ".join(settings.config_warnings())
    assert "LAST_SPREADSHEET_ID" in warnings
    assert "GOOGLE_SPREADSHEET_ID" in warnings


def test_old_init_kwargs_do_not_change_the_target() -> None:
    settings = Settings(
        google_spreadsheet_id="official",
        last_spreadsheet_id="old",
        nastele_spreadsheet_id="old-nastele",
        _env_file=None,
    )

    assert settings.spreadsheet_id == "official"


def test_obsolete_env_names_are_reported_not_applied(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("shop_integration.config.PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("DEFAULT_PURCHASE_QUANTITY", "5")
    monkeypatch.setenv("PREFERRED_SUPPLIER_ON_TIE", "shopxoainon")

    settings = Settings(_env_file=None)

    assert settings.comparison_quantity == 1
    assert settings.preferred_supplier_id == "shop_xoai_non"
    assert set(settings.unused_env()) == {
        "DEFAULT_PURCHASE_QUANTITY",
        "PREFERRED_SUPPLIER_ON_TIE",
    }


def test_config_check_prints_single_id_and_marks_legacy_tabs(capsys) -> None:
    settings = make_settings(google_spreadsheet_id="official")

    code = command_config_check(argparse.Namespace(), settings)
    output = capsys.readouterr().out

    assert code == 0
    assert f"{SINGLE_SPREADSHEET_ENV}: official" in output
    assert "purchase_allowed=false" in output
    assert "telegram_active=false" in output
    assert "NASTELE_NHAT_KY" in output
    assert settings.shop_xoai_non_api_key.get_secret_value() not in output


def test_open_workspace_reuses_one_credentials(monkeypatch) -> None:
    calls: dict[str, list] = {"credentials": [], "sheets": [], "docs": []}

    def fake_credentials(path: str, scopes: list[str]) -> object:
        del scopes
        credentials = object()
        calls["credentials"].append(path)
        return credentials

    class FakeSheets:
        def __init__(self, credentials: object) -> None:
            calls["sheets"].append(credentials)

    class FakeDocs:
        def __init__(self, credentials: object) -> None:
            calls["docs"].append(credentials)

    monkeypatch.setattr(
        "shop_integration.workspace.load_service_account_credentials",
        fake_credentials,
    )
    monkeypatch.setattr("shop_integration.workspace.GoogleSheetsGateway", FakeSheets)
    monkeypatch.setattr("shop_integration.workspace.GoogleDocsGateway", FakeDocs)

    workspace = open_workspace(
        make_settings(google_credentials_file="sa.json"),
        sheets=True,
        docs=True,
    )

    assert len(calls["credentials"]) == 1
    assert calls["sheets"] == calls["docs"] == [workspace.credentials]
    assert isinstance(workspace, Workspace)


def test_source_command_uses_one_spreadsheet_for_both_writers(monkeypatch) -> None:
    seen: list[str] = []

    class StubWriter:
        def __init__(self, gateway, spreadsheet_id, *, dry_run, secrets=None) -> None:
            del gateway, secrets
            seen.append(spreadsheet_id)
            self.spreadsheet_id = spreadsheet_id
            self.dry_run = dry_run
            self.tabs_to_create: list[str] = []

        def read_approvals(self):
            return [], []

        def apply(self, *args, **kwargs):
            return {}

    monkeypatch.setattr(cli, "SourceSheetWriter", StubWriter)
    monkeypatch.setattr(cli, "NasteleSheetWriter", StubWriter)
    monkeypatch.setattr(
        cli,
        "open_workspace",
        lambda settings, **kwargs: Workspace(credentials=object(), sheets=object()),
    )
    monkeypatch.setattr(
        cli,
        "collect_suppliers",
        lambda settings, stamp: (
            ProviderResult(SUPPLIER_SHOP, []),
            ProviderResult(SUPPLIER_NASTELE, [], catalog_status=NASTELE_CATALOG_EMPTY),
        ),
    )
    settings = make_settings(
        google_spreadsheet_id="official",
        google_credentials_file="sa.json",
    )

    code = cli.command_sources(argparse.Namespace(), settings)

    assert code == 0
    assert seen == ["official", "official"]



def test_empty_nastele_catalog_writes_no_product_rows() -> None:
    before = [["Product ID", "Stock Quantity"], ["43", 40]]
    memory = MemoryGateway(
        titles=[TAB_PRODUCTS],
        values={TAB_PRODUCTS: [list(row) for row in before]},
    )
    writer = NasteleSheetWriter(memory, "official", dry_run=False)

    counts = writer.apply(
        [],
        updated_at="2026-11-25T10:00:00+07:00",
        config_pairs=[],
        failed=True,
        failure_message=NASTELE_CATALOG_EMPTY,
    )

    assert counts == {"products": 0, "tiers": 0, "preserved": 1}
    assert memory.values[TAB_PRODUCTS] == before
    assert memory.writes == [TAB_LOG]
    assert memory.values[TAB_LOG][-1][1] == "CẢNH BÁO"


def test_pricing_regression_middle_band() -> None:
    quote = quote_cost(168000, PricingPolicy())

    assert quote.cost == 168000
    assert quote.selling == 203000


def test_exact_match_selects_the_cheaper_supplier() -> None:
    shop = _shop_offer(cost=10000)
    nastele = replace(
        shop,
        supplier_id=SUPPLIER_NASTELE,
        supplier_name="Nastele",
        source_product_id="43",
        source_variant_id="43",
        cost_price_vnd=8000,
    )

    built = build_source_plan(
        [shop, nastele],
        [],
        quantity=1,
        preferred_supplier_id=SUPPLIER_SHOP,
        policy=PricingPolicy(),
        updated_at="2026-11-25T10:00:00+07:00",
    )

    assert built.exact_count == 1
    row = built.comparison[0]
    assert row["Matching Status"] == EXACT_MATCH
    assert row["Selected Supplier"] == "Nastele"
    assert row["Selected Cost"] == 8000


def test_duplicate_offers_are_collapsed() -> None:
    offer = _shop_offer()

    assert len(_unique_offers([offer, replace(offer)])) == 1


def test_menu_sidebar_and_merges_are_preserved() -> None:
    gateway = _menu_book()
    merge = {
        "startRowIndex": 6,
        "endRowIndex": 7,
        "startColumnIndex": 0,
        "endColumnIndex": 8,
    }
    gateway.merges[TAB_MENU] = [merge]
    sidebar = [row[9] if len(row) > 9 else "" for row in gateway.values[TAB_MENU]]

    result = CombinedQuoteWriter(gateway, "official").update(
        shop_quote_rows(_products()),
        dry_run=False,
    )

    menu = gateway.values[TAB_MENU]
    assert [row[9] if len(row) > 9 else "" for row in menu[: len(sidebar)]] == sidebar
    assert gateway.merges[TAB_MENU] == [merge]
    protected = (f"'{TAB_MENU}'!I", f"'{TAB_MENU}'!J", f"'{TAB_MENU}'!K")
    blocked = (f"'{TAB_MENU}'!L", f"'{TAB_MENU}'!M")
    assert not any(item.startswith(protected + blocked) for item in result.ranges)


def test_menu_dry_run_does_not_write() -> None:
    gateway = _menu_book()

    result = run_menu_sync(
        make_settings(google_spreadsheet_id="official"),
        live=False,
        gateway=gateway,
        shop_client=EmptyShop(),
    )

    assert result.status == "ok"
    assert gateway.batches == []
    assert gateway.added == []


def test_legacy_tabs_are_marked_and_kept() -> None:
    assert set(LEGACY_TAB_NOTES).isdisjoint(ALL_TABS)
    assert "Trang tính1" not in LEGACY_TAB_NOTES
    assert "BANG_GIA_TOI_UU" in LEGACY_TAB_NOTES
    assert "NASTELE_SAN_PHAM" in LEGACY_TAB_NOTES


def test_writer_tab_sets_do_not_overlap_managed_catalog() -> None:
    assert set(SOURCE_TABS).isdisjoint(ALL_TABS)
    assert set(NASTELE_TABS).isdisjoint(ALL_TABS + list(SOURCE_TABS))
    assert set(WRITE_TABS).isdisjoint(ALL_TABS + list(SOURCE_TABS) + list(NASTELE_TABS))


def test_purchase_and_telegram_cannot_be_enabled() -> None:
    settings = make_settings(enable_purchase=True, enable_telegram=True)

    assert settings.purchase_allowed is False
    assert settings.telegram_active is False
