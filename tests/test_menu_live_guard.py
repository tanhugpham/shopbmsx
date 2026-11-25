"""Cổng backup bền và giữ điều khoản vận hành. Không gọi Google."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shop_integration.backup_service import assess_live_sync_readiness
from shop_integration.combined_quote import _keep_operator_terms
from shop_integration.errors import BackupError
from shop_integration.menu_region_backup import (
    BACKUP_TAB,
    OFFICIAL_SPREADSHEET_ID,
    durable_backup_required,
    store_region_backup,
)
from shop_integration.menu_sync import run_menu_sync
from tests.conftest import make_settings
from tests.test_combined_quote import TAB_MENU, _menu_book
from tests.test_menu_sync import EmptyShop, StockShop


def test_pytest_on_github_actions_does_not_force_menu_backup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("MENU_REQUIRE_DURABLE_BACKUP", raising=False)
    assert durable_backup_required() is False


def test_explicit_flag_requires_durable_backup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("MENU_REQUIRE_DURABLE_BACKUP", "true")
    assert durable_backup_required() is True


def test_sync_menu_workflow_is_the_only_live_writer() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "sync-menu.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert 'cron: "*/5 * * * *"' in workflow
    assert "group: sync-menu" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "timeout-minutes: 15" in workflow
    assert 'DRY_RUN: "false"' in workflow
    assert 'ENABLE_PURCHASE: "false"' in workflow
    assert 'ENABLE_TELEGRAM: "false"' in workflow
    assert 'ENABLE_SCHEDULED_SYNC: "false"' in workflow
    assert 'ENABLE_SCHEDULED_MENU_LIVE: "false"' in workflow
    assert "MENU_REQUIRE_DURABLE_BACKUP" in workflow
    assert "data/menu-baseline.json" in workflow
    assert "sync-menu-preflight --verify-backup" in workflow
    assert "actions/upload-artifact" not in workflow
    sources = (root / ".github" / "workflows" / "sync-sources.yml").read_text(encoding="utf-8")
    assert 'DRY_RUN: "true"' in sources
    assert 'DRY_RUN: "false"' not in sources
    web = (root / "src" / "shop_integration" / "web.py").read_text(encoding="utf-8")
    assert 'os.environ["ENABLE_SCHEDULED_SYNC"] = "false"' in web
    assert 'os.environ["ENABLE_SCHEDULED_MENU_LIVE"] = "false"' in web
    blueprint = (root / "render.yaml").read_text(encoding="utf-8")
    assert "type: cron" not in blueprint
    assert 'ENABLE_SCHEDULED_SYNC' in blueprint
    assert 'value: "false"' in blueprint


def test_source_backup_readiness_stays_not_ready() -> None:
    report = assess_live_sync_readiness()
    assert report["status"] == "FAIL"
    assert report["github_actions_live_sync"] == "NOT_READY"
    assert "Google Drive" in report["proposal"]


def test_wrong_spreadsheet_is_not_backed_up() -> None:
    gateway = _menu_book()
    with pytest.raises(BackupError, match="không khớp spreadsheet chính thức"):
        store_region_backup(gateway, "last-id", secrets=["sxn_live_test_key_value"])
    assert gateway.batches == []
    assert BACKUP_TAB not in gateway.titles


def test_region_backup_roundtrip_and_chunk_restore(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shop_integration.menu_region_backup.CHUNK_CHARS", 24)
    gateway = _menu_book()
    gateway.values[TAB_MENU][7][2] = "mô tả " + ("x" * 80)
    note = store_region_backup(
        gateway,
        OFFICIAL_SPREADSHEET_ID,
        secrets=["sxn_live_test_key_value"],
    )
    assert "READY" in note
    assert BACKUP_TAB in gateway.titles
    ledger = gateway.values[BACKUP_TAB]
    parts = [row[1] for row in ledger[1:] if row and row[0]]
    assert "manifest" in parts
    assert "restore_plan" in parts
    assert "payload-000" in parts
    assert "payload-001" in parts
    blob = json.dumps(ledger, ensure_ascii=False)
    assert "sxn_live_test_key_value" not in blob
    assert "BEGIN PRIVATE KEY" not in blob


def test_private_key_is_redacted_before_the_backup_tab(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shop_integration.menu_region_backup.CHUNK_CHARS", 1000)
    gateway = _menu_book()
    gateway.values[TAB_MENU][7][2] = "-----BEGIN PRIVATE KEY-----\nsecret-material"
    store_region_backup(gateway, OFFICIAL_SPREADSHEET_ID, secrets=[])
    blob = json.dumps(gateway.values[BACKUP_TAB], ensure_ascii=False)
    assert "PRIVATE KEY" not in blob
    assert "secret-material" not in blob


def test_volatile_formula_is_not_treated_as_another_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = _menu_book()
    original = gateway.get_values
    clock = {"n": 0}

    def reading(spreadsheet_id: str, range_a1: str, value_render_option: str = "UNFORMATTED_VALUE"):
        values = original(spreadsheet_id, range_a1)
        if "MENU" not in range_a1 or BACKUP_TAB in range_a1:
            return values
        grid = [list(row) for row in values]
        while len(grid) < 5:
            grid.append([])
        while len(grid[4]) < 7:
            grid[4].append("")
        if value_render_option == "FORMULA":
            grid[4][6] = "=NOW()"
        else:
            clock["n"] += 1
            grid[4][6] = clock["n"]
        return grid

    monkeypatch.setattr(gateway, "get_values", reading)
    note = store_region_backup(gateway, OFFICIAL_SPREADSHEET_ID, secrets=[])
    assert "READY" in note
    assert "=NOW()" in json.dumps(gateway.values[BACKUP_TAB], ensure_ascii=False)


def test_region_change_during_backup_blocks_the_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = _menu_book()
    original = gateway.get_values
    seen = {"menu": 0}

    def changing(
        spreadsheet_id: str,
        range_a1: str,
        value_render_option: str = "UNFORMATTED_VALUE",
    ):
        values = original(spreadsheet_id, range_a1)
        if (
            "MENU" in range_a1
            and BACKUP_TAB not in range_a1
            and value_render_option != "FORMULA"
        ):
            seen["menu"] += 1
            if seen["menu"] >= 2:
                changed = [list(row) for row in values]
                changed[0] = list(changed[0]) + ["doi"]
                return changed
        return values

    monkeypatch.setattr(gateway, "get_values", changing)
    with pytest.raises(BackupError, match="nguồn ghi khác"):
        store_region_backup(gateway, OFFICIAL_SPREADSHEET_ID, secrets=[])


def test_live_sync_refuses_unofficial_sheet_without_writing_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MENU_REQUIRE_DURABLE_BACKUP", "true")
    gateway = _menu_book()
    before = [list(row) for row in gateway.values[TAB_MENU]]
    result = run_menu_sync(
        make_settings(google_spreadsheet_id="last-id"),
        live=True,
        gateway=gateway,
        shop_client=EmptyShop(),
    )
    assert result.live is False
    assert result.status == "error"
    assert result.cells_written == 0
    assert gateway.values[TAB_MENU] == before
    assert any("Không ghi MENU" in item for item in result.errors)


def test_live_sync_writes_menu_only_after_backup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MENU_REQUIRE_DURABLE_BACKUP", "true")
    gateway = _menu_book()
    result = run_menu_sync(
        make_settings(google_spreadsheet_id=OFFICIAL_SPREADSHEET_ID),
        live=True,
        gateway=gateway,
        shop_client=StockShop(),
    )
    assert result.status == "ok"
    assert result.live is True
    assert result.cells_written > 0
    assert BACKUP_TAB in gateway.titles
    row = gateway.values[TAB_MENU][7]
    assert row[0] == "SXN-01"
    assert row[2] == "API mô tả"
    assert row[4] == "BHF"
    assert row[5] == 203000
    assert row[6] == 9
    assert "local_" not in " ".join(str(cell) for cell in row)


def test_operator_warranty_and_activation_stay() -> None:
    kept = _keep_operator_terms(
        ["SXN-01", "Tên", "Cách kích hoạt thủ công", "30", "Bảo hành đã ghi", 100, 1, "IN STOCK"],
        ["SXN-01", "Tên mới", "mô tả API", "24", "Bảo hành 1:1", 203000, 9, "IN STOCK"],
        description_locked=False,
    )
    assert kept[1] == "Tên mới"
    assert kept[2] == "Cách kích hoạt thủ công"
    assert kept[3] == "24"
    assert kept[4] == "Bảo hành đã ghi"
    assert kept[5] == 203000
    assert kept[6] == 9

    replaced = _keep_operator_terms(
        ["SXN-01", "Tên", "mô tả cũ", "30", "", 100, 1, "IN STOCK"],
        ["SXN-01", "Tên mới", "mô tả mới", "24", "Bảo hành 1:1", 203000, 9, "IN STOCK"],
        description_locked=False,
    )
    assert replaced[2] == "mô tả mới"
    assert replaced[4] == "Bảo hành 1:1"

    stated = _keep_operator_terms(
        ["SXN-01", "Tên", "mô tả cũ", "30", "KBH", 100, 1, "IN STOCK"],
        ["SXN-01", "Tên mới", "Gói riêng BHF", "24", "BHF", 203000, 9, "IN STOCK"],
        description_locked=False,
    )
    assert stated[4] == "BHF"
    assert stated[5] == 203000

    locked = _keep_operator_terms(
        ["SXN-01", "Tên", "Cách kích hoạt cũ", "30", "BHF", 100, 1, "IN STOCK"],
        ["SXN-01", "Tên khóa", "Mô tả đã khóa", "24", "BHF", 203000, 4, "IN STOCK"],
        description_locked=True,
    )
    assert locked[2] == "Mô tả đã khóa"
    assert locked[4] == "BHF"
    assert locked[5] == 203000
