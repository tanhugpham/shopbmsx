#!/usr/bin/env python
"""Phase 4.3E: cổng xác minh và áp dụng đúng plan migration đã duyệt.

Mặc định chỉ đọc. Chỉ khi ``--apply --confirm`` và toàn bộ pre-check PASS thì mới
gọi các thao tác ghi đã nằm trong allowlist của ``menu_migration``.

Không ghi Google Docs, không tạo đơn, không đổi cấu trúc/formatting/merge/
dimension. Không sửa ``.env``; DRY_RUN chỉ được đổi trong tiến trình này.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shop_integration.backup_service import checksum  # noqa: E402
from shop_integration.combined_quote import (  # noqa: E402
    MENU_HEADERS,
    _menu_product_row,
    _stacked_rows,
    is_menu_product_row,
    manual_quote_rows,
    shop_quote_rows,
)
from shop_integration.config import Settings  # noqa: E402
from shop_integration.menu_layout import (  # noqa: E402
    load_menu_baseline,
    menu_baseline_from_structure,
    menu_structure_diff,
)
from shop_integration.menu_migration import (  # noqa: E402
    _quote_grid,
    execute_migration,
    run_migration,
)
from shop_integration.menu_sync import _load_shop_products  # noqa: E402
from shop_integration.pricing import (  # noqa: E402
    policy_from_settings,
    quote_cost,
    round_pretty_vnd,
)
from shop_integration.workspace import open_workspace  # noqa: E402

APPROVED_PLAN = ROOT / "backups" / "phase43e-final-verification.json"
APPROVED_BACKUP = ROOT / "backups" / "migration-20261125T123504+0700"
SOURCE_FRESHNESS = ROOT / "backups" / "phase43e-source-freshness.json"
BASELINE = ROOT / "backups" / "menu-baseline-phase43b.json"
GATE_ROOT = ROOT / "backups" / "phase43e-live-gate"
PRECHECK_REPORT = ROOT / "backups" / "phase43e-live-precheck.json"
RESULT_REPORT = ROOT / "backups" / "phase43e-live-result.json"
SOURCE_SHA = "50f581534de30b216dc743983ee510e3d84841a555890efeb2a5ec03da1a0f09"

EXPECTED_RANGES = [
    "'BAO_GIA'!A1:G25",
    "'BAO_GIA'!H1:H25",
    "'BAO_GIA_TONG_HOP'!A1:G26",
    "'MENU'!A31:H31",
    "'MENU'!A36:H36",
    "'MENU'!K38",
    "'MENU'!J39",
    "'MENU'!J40",
]
EXPECTED_KINDS = ["values", "clear", "values", "values", "clear", "validation", "values", "values"]
AI_PRO_ROW = [1, "AI PRO", "test", "TESST", "HELLO", 100000, 23]
EXPECTED_K38 = "=MENU!$B$8:$B$35"
EXPECTED_J39 = (
    '="⏱️ Thời hạn: " & IFERROR(VLOOKUP(K38; B8:H35; 3; FALSE); "—") & "  •  🛡️ BH: " '
    '& IFERROR(VLOOKUP(K38; B8:H35; 4; FALSE); "—") & "  •  💰 " '
    '& IFERROR(TEXT(VLOOKUP(K38; B8:H35; 5; FALSE); "#,##0"); "—") & " đ"'
)
EXPECTED_J40 = (
    '=IFERROR(VLOOKUP(K38; BAO_GIA_TONG_HOP!B2:E26; 4; FALSE); '
    'IFERROR(VLOOKUP(K38; B8:C35; 2; FALSE); '
    '"Vui lòng chọn sản phẩm ở ô trên để xem mô tả chi tiết."))'
)
SOURCE_KEYS = [
    "ProductID",
    "VariantID",
    "PRODUCT",
    "DESCRIPTION",
    "DURATION",
    "WARRANTY",
    "PRICE",
    "STOCK",
]


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _source_hash(rows: Sequence[dict[str, Any]]) -> str:
    raw = json.dumps(list(rows), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _project_source(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: row.get(key, "") for key in SOURCE_KEYS} for row in rows]


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _blank(row: Sequence[Any] | None) -> bool:
    return all(not _text(cell) for cell in (row or []))


def _same_grid(left: Sequence[Sequence[Any]], right: Sequence[Sequence[Any]]) -> bool:
    if len(left) != len(right):
        return False
    return all(list(a) == list(b) for a, b in zip(left, right))


def _set_cell(rows: list[list[Any]], row_number: int, column: int, value: Any = "") -> None:
    while len(rows) < row_number:
        rows.append([])
    row = rows[row_number - 1]
    while len(row) <= column:
        row.append("")
    row[column] = value


def _normalize_menu_g5(payload: dict[str, Any]) -> dict[str, Any]:
    """Bỏ giá trị động của MENU!G5 (=NOW()) khi so snapshot/structures."""

    out = copy.deepcopy(payload)
    for sheet in out.get("sheets", []):
        if isinstance(sheet, dict) and sheet.get("title") == "MENU":
            _set_cell(sheet.setdefault("values", []), 5, 6, "")
    return out


def _normalize_menu_structure(structure: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(structure)
    values = out.get("values")
    if isinstance(values, list):
        _set_cell(values, 5, 6, "")
    return out


def _backup_payloads(folder: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        _load_json(folder / "manifest.json"),
        _load_json(folder / "snapshot.json"),
        _load_json(folder / "structures.json"),
    )


def _backup_integrity(folder: Path) -> tuple[bool, str]:
    try:
        manifest, snapshot, structures = _backup_payloads(folder)
    except Exception as exc:  # noqa: BLE001 - báo lỗi vận hành, không che giấu
        return False, f"Không đọc được backup: {exc}"
    if manifest.get("status") != "READY":
        return False, f"manifest.status={manifest.get('status')!r}, cần READY"
    if manifest.get("blockers"):
        return False, "manifest còn blockers: " + "; ".join(map(str, manifest["blockers"]))
    if checksum(snapshot) != manifest.get("snapshot_checksum"):
        return False, "checksum snapshot không khớp manifest"
    if checksum(structures) != manifest.get("structures_checksum"):
        return False, "checksum structures không khớp manifest"
    restore = list(manifest.get("restore_plan") or [])
    if len(restore) != 9:
        return False, f"restore_plan có {len(restore)} dòng, cần 9"
    if "Nastele" in " ".join(restore) or "NASTELEID" in " ".join(restore):
        return False, "restore_plan chạm NASTELEID ngoài phạm vi"
    restored_ranges = [text for text in restore if "Restore giá trị/công thức" in text]
    for range_a1 in EXPECTED_RANGES[:5] + EXPECTED_RANGES[6:]:
        if not any(range_a1 in text for text in restored_ranges):
            return False, f"restore_plan thiếu {range_a1}"
    return True, f"{len(restore)} dòng restore, checksum khớp"


def _read_source(settings: Settings) -> tuple[list[Any], list[dict[str, Any]], list[str]]:
    products, notes = _load_shop_products(settings, None)
    rows = shop_quote_rows(products, policy_from_settings(settings))
    return products, rows, list(notes)


def _local41(products: Sequence[Any], rows: Sequence[dict[str, Any]]) -> tuple[Any, Any, dict[str, Any]]:
    product = next((item for item in products if str(item.id) == "local_41"), None)
    if product is None:
        raise AssertionError("Không thấy ProductID local_41 trong API.")
    variant = next((item for item in product.variants if str(item.id) == "local_41"), None)
    if variant is None:
        raise AssertionError("Không thấy VariantID local_41 trong API.")
    row = next(
        (
            item
            for item in rows
            if str(item.get("ProductID")) == "local_41"
            and str(item.get("VariantID")) == "local_41"
        ),
        None,
    )
    if row is None:
        raise AssertionError("Không thấy dòng local_41/local_41 trong shop_quote_rows.")
    return product, variant, row


def _validation_map(structure: dict[str, Any]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for item in structure.get("data_validations") or []:
        if isinstance(item, dict) and item.get("cell"):
            found[str(item["cell"]).upper()] = item
    return found


def _formula_map(structure: dict[str, Any]) -> dict[str, str]:
    return {
        str(item.get("cell") or "").upper(): str(item.get("formula") or "")
        for item in structure.get("formulas") or []
        if isinstance(item, dict) and item.get("cell")
    }


def _normalize_validation_value(item: dict[str, Any]) -> str:
    for value in item.get("values") or []:
        text = value.get("userEnteredValue") if isinstance(value, dict) else value
        if _text(text):
            return str(text).strip()
    return ""


def _check(
    checks: list[dict[str, Any]],
    blockers: list[str],
    name: str,
    ok: bool,
    detail: str = "",
) -> bool:
    checks.append({"check": name, "status": "PASS" if ok else "FAIL", "detail": detail})
    if not ok:
        blockers.append(f"{name}: {detail}" if detail else name)
    return ok


def _target_check(
    approved_snapshot: dict[str, Any],
    approved_structures: dict[str, Any],
    fresh_backup: Path,
) -> tuple[bool, str]:
    current_manifest, current_snapshot, current_structures = _backup_payloads(fresh_backup)
    if current_manifest.get("status") != "READY":
        return False, "backup mới không READY"
    left = checksum(_normalize_menu_g5(approved_snapshot))
    right = checksum(_normalize_menu_g5(current_snapshot))
    if left != right:
        return False, "snapshot target lệch quá ô động MENU!G5"
    left = checksum(_normalize_menu_structure(approved_structures.get("MENU") or {}))
    right = checksum(_normalize_menu_structure(current_structures.get("MENU") or {}))
    if left != right:
        return False, "structures MENU lệch quá ô động MENU!G5"
    return True, "target snapshot + structures khớp 12:35 (bỏ G5=NOW())"


def _precheck(
    gateway: Any,
    settings: Settings,
    *,
    plan_path: Path = APPROVED_PLAN,
    backup_path: Path = APPROVED_BACKUP,
    freshness_path: Path = SOURCE_FRESHNESS,
) -> dict[str, Any]:
    approved = _load_json(plan_path)
    approved_manifest = _load_json(backup_path / "manifest.json")
    approved_snapshot = _load_json(backup_path / "snapshot.json")
    approved_structures = _load_json(backup_path / "structures.json")
    freshness = _load_json(freshness_path)
    approved_sha = str(freshness.get("sha256") or "")

    checks: list[dict[str, Any]] = []
    blockers: list[str] = []
    notes: list[str] = []

    _check(
        checks,
        blockers,
        "approved plan",
        approved.get("status") == "PLANNED"
        and approved.get("dry_run") is True
        and approved.get("applied") is False,
        f"status={approved.get('status')!r}, dry_run={approved.get('dry_run')!r}",
    )
    _check(
        checks,
        blockers,
        "approved plan shape",
        len(approved.get("operations") or []) == 8
        and [item.get("range") for item in approved["operations"]] == EXPECTED_RANGES
        and [item.get("kind") for item in approved["operations"]] == EXPECTED_KINDS,
        f"{len(approved.get('operations') or [])} thao tác",
    )
    ok, detail = _backup_integrity(backup_path)
    _check(checks, blockers, "approved backup READY/checksum", ok, detail)
    _check(
        checks,
        blockers,
        "backup 12:35 manifest",
        approved_manifest.get("status") == "READY"
        and approved_manifest.get("spreadsheet_id")
        == "196goR1lDZN6zJfoC3a7vOVyo5u9o-pvxXpc3mnGrGSY"
        and list(approved_manifest.get("tabs") or [])
        == ["MENU", "NASTELEID", "BAO_GIA", "BAO_GIA_TONG_HOP"],
        f"tabs={approved_manifest.get('tabs')}",
    )

    # Đọc API hai lần, cách nhau 2 giây. Không dùng lần đọc cũ của kế hoạch.
    products1, rows1, notes1 = _read_source(settings)
    time.sleep(2)
    products2, rows2, notes2 = _read_source(settings)
    notes.extend(notes1)
    notes.extend(note for note in notes2 if note not in notes1)
    projected1 = _project_source(rows1)
    projected2 = _project_source(rows2)
    current_sha = _source_hash(projected2)
    _check(
        checks,
        blockers,
        "API ổn định giữa 2 lần đọc",
        projected1 == projected2 and _source_hash(projected1) == current_sha,
        f"sha={current_sha}",
    )
    _check(
        checks,
        blockers,
        "source fingerprint khớp approved",
        bool(approved_sha)
        and current_sha == approved_sha
        and projected2 == freshness.get("rows")
        and freshness.get("sha256") == approved_sha
        and freshness.get("second_read_sha256") == approved_sha
        and freshness.get("stable_across_seconds") is True,
        f"current={current_sha}, approved={approved_sha}, rows={len(projected2)}",
    )
    _check(
        checks,
        blockers,
        "source rows",
        len(rows2) == 24
        and len({(str(r.get("ProductID")), str(r.get("VariantID"))) for r in rows2}) == 24,
        f"{len(rows2)} dòng, "
        f"{len({(str(r.get('ProductID')), str(r.get('VariantID'))) for r in rows2})} identity",
    )

    try:
        product41, variant41, row41 = _local41(products2, rows2)
        quote41 = quote_cost(variant41.price_vnd, policy_from_settings(settings))
        selling41 = round_pretty_vnd(quote41.selling) if quote41.selling is not None else None
        detail41 = (
            f"id={product41.id}/{variant41.id}, cost={variant41.price_vnd}, "
            f"selling_api={row41.get('PRICE')}, selling_calc={selling41}, stock={row41.get('STOCK')}"
        )
        _check(
            checks,
            blockers,
            "SXN-24 local_41/local_41",
            str(product41.id) == "local_41"
            and str(variant41.id) == "local_41"
            and str(row41.get("ProductID")) == "local_41"
            and str(row41.get("VariantID")) == "local_41",
            detail41,
        )
        _check(
            checks,
            blockers,
            "giá bán 90.000 / tồn 7",
            row41.get("PRICE") == 90000
            and selling41 == 90000
            and row41.get("STOCK") == 7
            and variant41.available_quantity == 7,
            detail41,
        )
    except Exception as exc:  # noqa: BLE001
        _check(checks, blockers, "SXN-24 local_41/local_41", False, str(exc))

    tong_grid = gateway.get_values(settings.spreadsheet_id, "'BAO_GIA_TONG_HOP'!A1:G26")
    ai_pro = list(tong_grid[1][:7]) if len(tong_grid) > 1 else []
    _check(
        checks,
        blockers,
        "AI PRO nhập tay trước migration",
        ai_pro == AI_PRO_ROW,
        f"actual={ai_pro}",
    )

    # Dựng lại plan ngay bây giờ. Nếu không khớp 100% plan 12:35 thì dừng.
    fresh = run_migration(
        gateway,
        settings.spreadsheet_id,
        rows2,
        live=False,
        backup_root=GATE_ROOT,
        baseline_path=str(BASELINE),
        secrets=settings.secret_values(),
    )
    fresh_dict = fresh.as_dict()
    _check(
        checks,
        blockers,
        "fresh plan PLANNED/READY",
        fresh.status == "PLANNED"
        and fresh.dry_run is True
        and fresh.applied is False
        and fresh.backup is not None
        and fresh.backup.status == "READY"
        and not fresh.backup.blockers,
        f"status={fresh.status}, backup={fresh.backup.status if fresh.backup else None}, "
        f"blockers={fresh.backup.blockers if fresh.backup else []}",
    )
    _check(
        checks,
        blockers,
        "fresh plan khớp approved",
        all(
            checksum(fresh_dict.get(key)) == checksum(approved.get(key))
            for key in ("source_plans", "menu_plan", "operations")
        ),
        "source_plans/menu_plan/operations",
    )
    _check(
        checks,
        blockers,
        "8 thao tác đúng allowlist",
        len(fresh.operations) == 8
        and [item.range_a1 for item in fresh.operations] == EXPECTED_RANGES
        and [item.kind for item in fresh.operations] == EXPECTED_KINDS,
        f"ranges={[item.range_a1 for item in fresh.operations]}",
    )
    if fresh.menu_plan is not None:
        _check(
            checks,
            blockers,
            "MENU plan SXN-24",
            fresh.menu_plan.target_row == 31
            and fresh.menu_plan.target_id == "SXN-24"
            and fresh.menu_plan.product_id == "local_41"
            and fresh.menu_plan.variant_id == "local_41"
            and fresh.menu_plan.product_count_after == 24
            and fresh.menu_plan.unique_ids_after == 24
            and fresh.menu_plan.selector_new == "B8:B35",
            f"row={fresh.menu_plan.target_row}, {fresh.menu_plan.product_id}/"
            f"{fresh.menu_plan.variant_id}, count={fresh.menu_plan.product_count_after}",
        )
    else:
        _check(checks, blockers, "MENU plan SXN-24", False, "không có menu_plan")

    fresh_structures: dict[str, Any] = {}
    if fresh.backup is not None:
        _check(
            checks,
            blockers,
            "fresh restore plan",
            list(fresh.backup.restore_plan)
            == list(approved.get("backup", {}).get("restore_plan") or []),
            f"{len(fresh.backup.restore_plan)} dòng",
        )
        ok, detail = _target_check(
            approved_snapshot,
            approved_structures,
            Path(fresh.backup.directory),
        )
        _check(checks, blockers, "target snapshot khớp 12:35", ok, detail)
        _check(
            checks,
            blockers,
            "fresh backup READY/checksum",
            fresh.backup.status == "READY" and not fresh.backup.blockers,
            f"{fresh.backup.directory}, checksums={fresh.backup.checksums}",
        )
        fresh_structures = _load_json(Path(fresh.backup.directory) / "structures.json")
        protected_before = list(
            (approved_structures.get("MENU") or {}).get("protected_ranges") or []
        )
        protected_now = list(
            (fresh_structures.get("MENU") or {}).get("protected_ranges") or []
        )
        _check(
            checks,
            blockers,
            "MENU protected ranges",
            protected_before == protected_now and bool(protected_now),
            f"{len(protected_now)} vùng bảo vệ",
        )
        try:
            saved_baseline = load_menu_baseline(str(BASELINE))
            current_baseline = menu_baseline_from_structure(
                fresh_structures.get("MENU") or {}, MENU_HEADERS
            )
            differences = menu_structure_diff(saved_baseline, current_baseline)
            _check(
                checks,
                blockers,
                "MENU baseline",
                not differences,
                "; ".join(differences[:5]) if differences else "khớp baseline phase43b",
            )
        except Exception as exc:  # noqa: BLE001
            _check(checks, blockers, "MENU baseline", False, str(exc))

    # Kiểm tra nội dung các operation trước khi ghi.
    try:
        by_range = {item.range_a1: item for item in fresh.operations}
        expected_bao = _quote_grid(rows2)
        snapshot_payload = (
            _load_json(Path(fresh.backup.directory) / "snapshot.json")
            if fresh.backup is not None
            else {}
        )
        nasteleid_grid = next(
            (
                sheet.get("values") or []
                for sheet in snapshot_payload.get("sheets", [])
                if isinstance(sheet, dict) and sheet.get("title") == "NASTELEID"
            ),
            [],
        )
        manual_rows, _manual_note = manual_quote_rows(nasteleid_grid)
        expected_tong = _quote_grid(_stacked_rows(list(manual_rows) + list(rows2)))
        expected_menu_row = _menu_product_row("SXN-24", row41)
        _check(
            checks,
            blockers,
            "BAO_GIA payload 24 sản phẩm",
            by_range["'BAO_GIA'!A1:G25"].values == expected_bao
            and len(expected_bao) == 25
            and len({row[0] for row in expected_bao[1:]}) == 24,
            f"{len(expected_bao) - 1} dòng dữ liệu",
        )
        _check(
            checks,
            blockers,
            "BAO_GIA_TONG_HOP payload + AI PRO",
            by_range["'BAO_GIA_TONG_HOP'!A1:G26"].values == expected_tong
            and len(expected_tong) == 26
            and expected_tong[1] == AI_PRO_ROW,
            f"{len(expected_tong) - 1} dòng dữ liệu",
        )
        _check(
            checks,
            blockers,
            "MENU A31 payload",
            by_range["'MENU'!A31:H31"].values == [expected_menu_row]
            and expected_menu_row[0] == "SXN-24"
            and expected_menu_row[5] == 90000
            and expected_menu_row[6] == 7,
            f"{expected_menu_row[:2]}...",
        )
        _check(
            checks,
            blockers,
            "J39/J40 payload",
            by_range["'MENU'!J39"].values == [[EXPECTED_J39]]
            and by_range["'MENU'!J40"].values == [[EXPECTED_J40]],
            "formula mới",
        )
        _check(
            checks,
            blockers,
            "K38 payload",
            by_range["'MENU'!K38"].validation_type == "ONE_OF_RANGE"
            and by_range["'MENU'!K38"].validation_values == [EXPECTED_K38],
            str(by_range["'MENU'!K38"].validation_values),
        )
        _check(
            checks,
            blockers,
            "clear H1:H25 / A36:H36",
            by_range["'BAO_GIA'!H1:H25"].values == [[""] for _ in range(25)]
            and by_range["'MENU'!A36:H36"].values == [[""] * 8],
            "đúng range, không xóa dòng",
        )
    except Exception as exc:  # noqa: BLE001
        _check(checks, blockers, "operation payload", False, str(exc))

    status = "PASS" if not blockers else "FAIL"
    payload = {
        "status": status,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": {
            "sha256": current_sha,
            "approved_sha256": approved_sha,
            "rows": len(projected2),
            "notes": notes,
        },
        "target": {
            "approved_plan": str(plan_path),
            "approved_backup": str(backup_path),
            "fresh_backup": fresh.backup.directory if fresh.backup else "",
        },
        "plan": {
            "operations": len(fresh.operations),
            "ranges": [item.range_a1 for item in fresh.operations],
            "menu_plan": fresh.menu_plan.as_dict() if fresh.menu_plan else None,
        },
        "backup": fresh.backup.as_dict() if fresh.backup else None,
        "checks": checks,
        "blockers": blockers,
        "_fresh": fresh,
    }
    return payload


def _layout_checks(
    checks: list[dict[str, Any]],
    blockers: list[str],
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    before_formulas = _formula_map(before)
    after_formulas = _formula_map(after)
    expected_formulas = dict(before_formulas)
    expected_formulas["J39"] = EXPECTED_J39
    expected_formulas["J40"] = EXPECTED_J40
    _check(
        checks,
        blockers,
        "formula preservation",
        after_formulas == expected_formulas,
        "chỉ J39/J40 đổi",
    )

    before_validations = _validation_map(before)
    after_validations = _validation_map(after)
    _check(
        checks,
        blockers,
        "dropdown preservation",
        _normalize_validation_value(after_validations.get("K38", {})) == EXPECTED_K38
        and after_validations.get("K38", {}).get("type") == "ONE_OF_RANGE"
        and {
            cell: _normalize_validation_value(item)
            for cell, item in after_validations.items()
            if cell != "K38"
        }
        == {
            cell: _normalize_validation_value(item)
            for cell, item in before_validations.items()
            if cell != "K38"
        },
        "chỉ K38 đổi",
    )

    for key in (
        "merges",
        "protected_ranges",
        "column_widths",
        "row_heights",
        "grid_properties",
        "conditional_format_count",
    ):
        _check(
            checks,
            blockers,
            f"layout {key}",
            checksum(before.get(key)) == checksum(after.get(key)),
            "không đổi",
        )

    before_values = before.get("values") or []
    after_values = after.get("values") or []
    skipped: list[str] = []
    mismatch: list[str] = []
    for row_number in range(1, max(len(before_values), len(after_values)) + 1):
        left = before_values[row_number - 1] if row_number - 1 < len(before_values) else []
        right = after_values[row_number - 1] if row_number - 1 < len(after_values) else []
        for column in range(max(len(left), len(right))):
            cell = f"{chr(65 + column) if column < 26 else '?'}{row_number}"
            if row_number == 5 and column == 6:
                skipped.append(cell)
                continue
            if row_number in (31, 36) and column < 8:
                skipped.append(cell)
                continue
            if row_number in (39, 40) and column == 9:
                skipped.append(cell)
                continue
            if (left[column] if column < len(left) else "") != (
                right[column] if column < len(right) else ""
            ):
                mismatch.append(cell)
    _check(
        checks,
        blockers,
        "MENU values preservation",
        not mismatch,
        f"chỉ bỏ qua {', '.join(skipped[:8])}"
        + ("..." if len(skipped) > 8 else "")
        + (f"; lệch: {mismatch[:8]}" if mismatch else ""),
    )

    before_policy = [
        [row[column] if column < len(row) else "" for column in range(9, 13)]
        for row in before_values
        if isinstance(row, list)
    ]
    after_policy = [
        [row[column] if column < len(row) else "" for column in range(9, 13)]
        for row in after_values
        if isinstance(row, list)
    ]
    # J39/J40 là ô công thức được phép đổi; J:M còn lại phải bất biến.
    for index in (38, 39):
        if index < len(before_policy) and index < len(after_policy):
            before_policy[index][0] = ""
            after_policy[index][0] = ""
    _check(
        checks,
        blockers,
        "warranty policy J:M",
        before_policy == after_policy,
        "không đổi ngoài J39/J40",
    )


def _postcheck(
    gateway: Any,
    settings: Settings,
    ops: Sequence[Any],
    precheck: dict[str, Any],
    completed: list[dict[str, Any]],
    failure: dict[str, Any] | None,
    *,
    approved_backup_path: Path = APPROVED_BACKUP,
) -> dict[str, Any]:
    del precheck
    checks: list[dict[str, Any]] = []
    blockers: list[str] = []
    time.sleep(2)
    post_bao = gateway.get_values(settings.spreadsheet_id, "'BAO_GIA'!A1:G25")
    post_h = gateway.get_values(settings.spreadsheet_id, "'BAO_GIA'!H1:H25")
    post_tong = gateway.get_values(settings.spreadsheet_id, "'BAO_GIA_TONG_HOP'!A1:G26")
    post_menu = gateway.get_values(settings.spreadsheet_id, "'MENU'!A1:H40")
    post_structure = gateway.get_sheet_structure(settings.spreadsheet_id, "MENU")

    by_range = {item.range_a1: item for item in ops}
    expected_bao = by_range["'BAO_GIA'!A1:G25"].values
    expected_tong = by_range["'BAO_GIA_TONG_HOP'!A1:G26"].values
    expected_menu = by_range["'MENU'!A31:H31"].values
    _check(
        checks,
        blockers,
        "BAO_GIA read-back",
        _same_grid(post_bao, expected_bao),
        f"{len(post_bao)} dòng (header + 24)",
    )
    _check(
        checks,
        blockers,
        "BAO_GIA H cleared",
        all(_blank(row[:1]) for row in post_h) if post_h else True,
        f"{len(post_h)} dòng H",
    )
    _check(
        checks,
        blockers,
        "BAO_GIA_TONG_HOP read-back",
        _same_grid(post_tong, expected_tong),
        f"{len(post_tong)} dòng (header + AI PRO + 24)",
    )
    _check(
        checks,
        blockers,
        "MENU A31:H31 read-back",
        len(post_menu) >= 31
        and list(post_menu[30][:8]) == list(expected_menu[0]),
        f"row31={post_menu[30][:8] if len(post_menu) >= 31 else 'missing'}",
    )
    _check(
        checks,
        blockers,
        "MENU A36:H36 cleared",
        len(post_menu) < 36 or _blank(post_menu[35][:8]),
        "legacy row 36 trống",
    )
    ids = [str(row[0]).strip() for row in post_bao[1:] if row and _text(row[0])]
    menu_product_rows = [
        row
        for row in post_menu[6:35]
        if is_menu_product_row(list(row))
    ] if len(post_menu) >= 35 else []
    menu_product_ids = {
        str(row[0]).strip()
        for row in menu_product_rows
        if _text(row[0])
    }
    _check(
        checks,
        blockers,
        "MENU product count",
        len(menu_product_rows) == 24 and len(menu_product_ids) == 24,
        f"{len(menu_product_rows)} dòng sản phẩm, {len(menu_product_ids)} ID duy nhất",
    )
    _check(
        checks,
        blockers,
        "AI PRO preserved",
        len(post_tong) > 1 and list(post_tong[1][:7]) == AI_PRO_ROW,
        f"actual={list(post_tong[1][:7]) if len(post_tong) > 1 else 'missing'}",
    )
    _check(
        checks,
        blockers,
        "SXN-24 local_41",
        len(post_menu) >= 31
        and list(post_menu[30][:8]) == list(expected_menu[0])
        and str(post_menu[30][0]).strip() == "SXN-24"
        and post_menu[30][5] == 90000
        and post_menu[30][6] == 7,
        f"row31={post_menu[30][:8] if len(post_menu) >= 31 else 'missing'}",
    )
    formulas = _formula_map(post_structure)
    _check(
        checks,
        blockers,
        "J39/J40 formulas",
        formulas.get("J39") == EXPECTED_J39 and formulas.get("J40") == EXPECTED_J40,
        "khớp công thức mới",
    )
    validations = _validation_map(post_structure)
    _check(
        checks,
        blockers,
        "K38 dropdown",
        _normalize_validation_value(validations.get("K38", {})) == EXPECTED_K38
        and validations.get("K38", {}).get("type") == "ONE_OF_RANGE",
        str(validations.get("K38")),
    )

    approved_structures = _load_json(approved_backup_path / "structures.json")
    _layout_checks(checks, blockers, approved_structures.get("MENU") or {}, post_structure)

    if failure:
        blockers.append(
            f"Thao tác {failure.get('index')} thất bại: {failure.get('error')}; "
            f"đã hoàn tất {len(completed)}/8"
        )
    status = "PASS" if not blockers else "FAIL"
    payload = {
        "status": status,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "operations_completed": completed,
        "failure": failure,
        "readback": {
            "baogia_rows": len(post_bao),
            "baogia_ids": len(ids),
            "tonghop_rows": len(post_tong),
            "menu_product_rows": len(menu_product_rows),
            "menu_product_ids": len(menu_product_ids),
            "ai_pro": list(post_tong[1][:7]) if len(post_tong) > 1 else [],
            "sxn24": list(post_menu[30][:8]) if len(post_menu) >= 31 else [],
            "k38": _normalize_validation_value(validations.get("K38", {})),
            "j39": formulas.get("J39", ""),
            "j40": formulas.get("J40", ""),
        },
        "checks": checks,
        "blockers": blockers,
        "remaining_risks": [
            "Dữ liệu nguồn có thể thay đổi sau thời điểm kiểm tra; cần chạy lại pre-check trước lần ghi kế tiếp.",
            "Baseline chính thức chỉ được tạo sau khi toàn bộ read-back PASS.",
            "Không có khôi phục tự động nếu Google trả lỗi giữa các thao tác; dùng restore plan từ backup 12:35.",
        ],
    }
    return payload


def _execute_ops(
    gateway: Any,
    spreadsheet_id: str,
    ops: Sequence[Any],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    completed: list[dict[str, Any]] = []
    failure: dict[str, Any] | None = None
    for index, op in enumerate(ops, start=1):
        try:
            execute_migration(gateway, spreadsheet_id, [op])
            completed.append(
                {
                    "index": index,
                    "kind": op.kind,
                    "range": op.range_a1,
                    "status": "PASS",
                }
            )
        except Exception as exc:  # noqa: BLE001 - dừng ngay, báo đúng thao tác
            failure = {
                "index": index,
                "kind": op.kind,
                "range": op.range_a1,
                "error": f"{type(exc).__name__}: {exc}",
            }
            break
    return completed, failure


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Ghi sau khi pre-check PASS.")
    parser.add_argument("--confirm", action="store_true", help="Bắt buộc đi cùng --apply.")
    parser.add_argument("--plan", type=Path, default=APPROVED_PLAN)
    parser.add_argument("--backup", type=Path, default=APPROVED_BACKUP)
    parser.add_argument("--freshness", type=Path, default=SOURCE_FRESHNESS)
    parser.add_argument("--precheck-report", type=Path, default=PRECHECK_REPORT)
    parser.add_argument("--result-report", type=Path, default=RESULT_REPORT)
    args = parser.parse_args(argv)
    if args.apply and not args.confirm:
        print("--apply phải đi cùng --confirm. Không ghi Google.")
        return 2

    settings = Settings()
    if not settings.spreadsheet_id:
        print("Thiếu GOOGLE_SPREADSHEET_ID.")
        return 2
    workspace = open_workspace(settings, sheets=True)
    gateway = workspace.sheets
    if gateway is None:
        print("Thiếu kết nối Google Sheets.")
        return 2

    try:
        precheck = _precheck(
            gateway,
            settings,
            plan_path=args.plan,
            backup_path=args.backup,
            freshness_path=args.freshness,
        )
    except Exception as exc:  # noqa: BLE001
        payload = {
            "status": "FAIL",
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "blockers": [f"Pre-check lỗi: {type(exc).__name__}: {exc}"],
        }
        _write_json(args.precheck_report, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    # Không đưa object MigrationRun vào JSON.
    fresh = precheck.pop("_fresh", None)
    _write_json(args.precheck_report, precheck)
    print(json.dumps(precheck, ensure_ascii=False, indent=2))
    if precheck["status"] != "PASS":
        print("PRE-CHECK FAIL: không ghi Google.")
        return 1
    if fresh is None or not args.apply:
        print("PRE-CHECK PASS: chỉ đọc; chưa ghi Google.")
        return 0

    ops = list(fresh.operations)
    previous_dry_run = os.environ.get("DRY_RUN")
    os.environ["DRY_RUN"] = "false"
    failure: dict[str, Any] | None = None
    completed: list[dict[str, Any]] = []
    try:
        completed, failure = _execute_ops(gateway, settings.spreadsheet_id, ops)
        post = _postcheck(
            gateway,
            settings,
            ops,
            precheck,
            completed,
            failure,
            approved_backup_path=args.backup,
        )
    except Exception as exc:  # noqa: BLE001
        post = {
            "status": "FAIL",
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "operations_completed": completed,
            "failure": failure,
            "blockers": [f"Post-check lỗi: {type(exc).__name__}: {exc}"],
            "remaining_risks": ["Không tự khôi phục; dùng restore plan đã duyệt."],
        }
    finally:
        os.environ["DRY_RUN"] = previous_dry_run if previous_dry_run is not None else "true"

    post["migration_backup"] = (
        fresh.backup.as_dict() if fresh.backup is not None else None
    )
    _write_json(args.result_report, post)
    print(json.dumps(post, ensure_ascii=False, indent=2))
    return 0 if post.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
