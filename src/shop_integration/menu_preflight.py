"""Cổng an toàn trước khi GitHub Actions ghi MENU. Không ghi giá bán.

``--verify-backup`` mới ghi tab backup bền, và chỉ ghi tab đó.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any

from shop_integration.combined_quote import MENU_HEADERS
from shop_integration.config import LEGACY_SPREADSHEET_ENV, Settings
from shop_integration.content_overrides import OverrideStoreError, load_override_store
from shop_integration.errors import BackupError, MenuLayoutError
from shop_integration.menu_layout import load_menu_baseline, menu_structure_diff, read_menu_baseline
from shop_integration.menu_region_backup import (
    OFFICIAL_SPREADSHEET_ID,
    assert_official_spreadsheet,
    store_region_backup,
)
from shop_integration.workspace import open_sheets

RENDER_HEALTH_URL = "https://shopbmsx.onrender.com/health"
REQUIRED_MERGE_COUNT = 62
DEFAULT_BASELINE = Path("data") / "menu-baseline.json"


def baseline_path() -> Path:
    raw = os.environ.get("MENU_BASELINE_FILE", "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            from shop_integration.config import PROJECT_ROOT

            return (PROJECT_ROOT / candidate).resolve()
        return candidate.resolve()
    from shop_integration.config import PROJECT_ROOT

    return (PROJECT_ROOT / DEFAULT_BASELINE).resolve()


def run_preflight(settings: Settings, *, verify_backup: bool) -> int:
    blockers: list[str] = []
    notes: list[str] = []
    _check_flags(blockers, notes)
    _check_secrets(settings, blockers, notes)
    _check_overrides(blockers, notes)
    _check_sources_workflow(blockers)
    gateway = None
    try:
        assert_official_spreadsheet(settings.spreadsheet_id)
        notes.append("GOOGLE_SPREADSHEET_ID khớp spreadsheet chính thức.")
    except BackupError as exc:
        blockers.append(str(exc))
    if not blockers:
        try:
            gateway = open_sheets(settings)
        except Exception as exc:
            blockers.append(f"Không mở được Google Sheets: {type(exc).__name__}.")
            gateway = None
        if gateway is None:
            blockers.append("Thiếu kết nối Google Sheets.")
        else:
            _check_sheet_access(gateway, settings, blockers, notes)
            _check_baseline(gateway, settings, blockers, notes)
            if verify_backup and not blockers:
                _verify_backup(gateway, settings, blockers, notes)
            close = getattr(gateway, "close", None)
            if callable(close):
                close()
    _check_render(blockers, notes)
    for note in notes:
        print(note)
    if blockers:
        print("Preflight: FAIL")
        for item in blockers:
            print(f"- {item}")
        return 1
    print("Preflight: PASS")
    print("Backup bền: READY" if verify_backup else "Backup bền: chưa ghi trong preflight.")
    print("Render scheduler: OFF")
    return 0


def _check_flags(blockers: list[str], notes: list[str]) -> None:
    flags = {
        "ENABLE_PURCHASE": "false",
        "ENABLE_TELEGRAM": "false",
        "ENABLE_SCHEDULED_SYNC": "false",
        "ENABLE_SCHEDULED_MENU_LIVE": "false",
    }
    for name, expected in flags.items():
        current = os.environ.get(name, "").strip().lower()
        if current != expected:
            blockers.append(f"{name} phải là {expected}.")
        else:
            notes.append(f"{name}={expected}.")


def _check_secrets(settings: Settings, blockers: list[str], notes: list[str]) -> None:
    if settings.shop_xoai_non_api_key.get_secret_value().strip():
        notes.append("SHOP_XOAI_NON_API_KEY: có.")
    else:
        blockers.append("Thiếu SHOP_XOAI_NON_API_KEY.")
    if settings.spreadsheet_id.strip():
        notes.append("GOOGLE_SPREADSHEET_ID: có.")
    else:
        blockers.append("Thiếu GOOGLE_SPREADSHEET_ID.")
    legacy = settings.legacy_spreadsheet_env()
    if legacy:
        blockers.append(
            "Còn biến spreadsheet cũ: " + ", ".join(legacy) + ". Chỉ dùng GOOGLE_SPREADSHEET_ID."
        )
    else:
        notes.append("Không dùng NASTELE_SPREADSHEET_ID hay LAST_SPREADSHEET_ID.")
    if settings.nastele_api_key.get_secret_value().strip():
        notes.append("NASTELE_API_KEY: có. sync-menu không gọi API Nastele.")
    else:
        notes.append("NASTELE_API_KEY: không có. sync-menu không cần API Nastele.")
    path = os.environ.get("GOOGLE_CREDENTIALS_FILE", "./GOOGLE_CREDENTIALS_FILE.json")
    from shop_integration.config import resolve_credentials_path

    file_path = resolve_credentials_path(path)
    if not file_path.is_file():
        blockers.append("Thiếu file GOOGLE_CREDENTIALS_FILE.")
        return
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        blockers.append("GOOGLE_CREDENTIALS_FILE không phải JSON hợp lệ.")
        return
    if not isinstance(payload, dict) or payload.get("type") != "service_account":
        blockers.append("GOOGLE_CREDENTIALS_FILE không phải service account.")
        return
    if "BEGIN PRIVATE KEY" not in json.dumps(payload):
        blockers.append("Service account thiếu private key. Không in nội dung tệp.")
        return
    notes.append("Google credentials: service account, không in giá trị.")


def _check_overrides(blockers: list[str], notes: list[str]) -> None:
    try:
        store = load_override_store()
    except OverrideStoreError as exc:
        blockers.append(str(exc))
        return
    notes.append(f"Content override đọc được: {len(store.items)} mục.")


def _check_sources_workflow(blockers: list[str]) -> None:
    from shop_integration.config import PROJECT_ROOT

    path = PROJECT_ROOT / ".github" / "workflows" / "sync-sources.yml"
    if not path.is_file():
        blockers.append("Thiếu workflow sync-sources.")
        return
    text = path.read_text(encoding="utf-8")
    if 'DRY_RUN: "false"' in text or "DRY_RUN: 'false'" in text:
        blockers.append("sync-sources đang tắt DRY_RUN. Không được có scheduler ghi thứ hai.")
    web = PROJECT_ROOT / "src" / "shop_integration" / "web.py"
    web_text = web.read_text(encoding="utf-8")
    if 'os.environ["ENABLE_SCHEDULED_SYNC"] = "false"' not in web_text:
        blockers.append("Render web không còn khóa ENABLE_SCHEDULED_SYNC.")
    if 'os.environ["ENABLE_SCHEDULED_MENU_LIVE"] = "false"' not in web_text:
        blockers.append("Render web không còn khóa ENABLE_SCHEDULED_MENU_LIVE.")


def _check_sheet_access(gateway: Any, settings: Settings, blockers: list[str], notes: list[str]) -> None:
    try:
        titles = gateway.list_sheet_titles(settings.spreadsheet_id)
    except Exception as exc:
        blockers.append(f"Không đọc được spreadsheet: {type(exc).__name__}.")
        return
    if "MENU" not in titles:
        blockers.append("Spreadsheet chính thức không có tab MENU.")
        return
    notes.append(f"Google Sheets đọc được. Số tab: {len(titles)}.")


def _check_baseline(gateway: Any, settings: Settings, blockers: list[str], notes: list[str]) -> None:
    path = baseline_path()
    if not path.is_file():
        blockers.append(f"Thiếu baseline MENU đã xác minh: {path.name}.")
        return
    try:
        saved = load_menu_baseline(path)
        current = read_menu_baseline(gateway, settings.spreadsheet_id, MENU_HEADERS)
    except (MenuLayoutError, OSError, UnicodeError) as exc:
        blockers.append(f"Baseline MENU không dùng được: {exc}")
        return
    if saved.merge_count != REQUIRED_MERGE_COUNT or current.merge_count != REQUIRED_MERGE_COUNT:
        blockers.append(
            f"MENU phải giữ {REQUIRED_MERGE_COUNT} vùng gộp. "
            f"Baseline {saved.merge_count}, hiện tại {current.merge_count}."
        )
    formula_cells = {item.get("cell") for item in current.formulas}
    if "J39" not in formula_cells or "J40" not in formula_cells:
        blockers.append("Thiếu công thức J39 hoặc J40.")
    if not _has_k38(current.data_validations):
        blockers.append("Thiếu dropdown K38.")
    if saved.headers != list(MENU_HEADERS):
        blockers.append("Baseline MENU không đúng dòng tiêu đề A:H.")
    differences = menu_structure_diff(saved, current)
    if differences:
        blockers.append("MENU lệch baseline đã xác minh: " + "; ".join(differences[:5]))
    else:
        notes.append(
            f"Baseline khớp Google. merge={current.merge_count}, "
            f"công thức={len(current.formulas)}, dropdown={len(current.data_validations)}."
        )


def _has_k38(validations: list[dict[str, Any]]) -> bool:
    for item in validations:
        rows = item.get("rows") or []
        label = str(item.get("range") or item.get("cell") or "").upper()
        if isinstance(rows, list) and 38 in rows and label.startswith("K"):
            return True
        if label.startswith("K38"):
            return True
    return False


def _verify_backup(gateway: Any, settings: Settings, blockers: list[str], notes: list[str]) -> None:
    try:
        notes.append(
            store_region_backup(
                gateway,
                settings.spreadsheet_id,
                secrets=settings.secret_values(),
            )
        )
    except Exception as exc:
        blockers.append(f"Backup bền chưa READY. {exc}")


def _check_render(blockers: list[str], notes: list[str]) -> None:
    try:
        with urllib.request.urlopen(RENDER_HEALTH_URL, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        blockers.append("Không đọc được Render /health. Không bật ghi khi chưa chắc scheduler tắt.")
        return
    if payload.get("status") != "ok" or payload.get("dry_run") is not True:
        blockers.append("Render /health không còn dry-run.")
        return
    scheduled = payload.get("scheduled_sync") or {}
    if scheduled.get("enabled") or scheduled.get("started") or scheduled.get("live"):
        blockers.append("Render scheduler đang bật. Dừng để không có hai nguồn ghi MENU.")
        return
    notes.append("Render /health OK. Scheduler OFF. dry_run=true.")
