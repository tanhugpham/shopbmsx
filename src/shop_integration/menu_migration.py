"""Phase 4.3E: sửa schema nguồn và MENU bằng kế hoạch migration có phê duyệt.

Module chỉ lập kế hoạch, chụp snapshot local và kiểm tra an toàn. Không ghi Google
khi ``live=False``. Mọi thay đổi ngoài allowlist của migration bị từ chối.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from shop_integration.backup_service import checksum
from shop_integration.combined_quote import (
    MENU_HEADERS,
    QUOTE_HEADERS,
    SOURCE_MANUAL,
    SOURCE_SHOP,
    TAB_AUTO,
    TAB_COMBINED,
    TAB_MANUAL,
    TAB_MENU,
    _menu_product_row,
    _outgoing,
    _stacked_rows,
    is_menu_product_row,
    manual_quote_rows,
    quote_layout_problems,
)
from shop_integration.config import PROJECT_ROOT
from shop_integration.errors import IntegrationError
from shop_integration.menu_audit import (
    normalize_product_name,
    price_number,
    same_source_product,
)
from shop_integration.menu_layout import (
    MenuBaseline,
    MenuCapacity,
    load_menu_baseline,
    menu_baseline_from_structure,
    menu_structure_diff,
)
from shop_integration.time_utils import format_timestamp, now_vietnam

MENU_TARGET_ROW = 31
MENU_LEGACY_ROW = 36
MENU_EXTENDED_LAST_ROW = 35
MENU_TARGET_ID = "SXN-24"
MIGRATION_TABS = (TAB_MENU, TAB_MANUAL, TAB_AUTO, TAB_COMBINED)
SOURCE_LABELS = {SOURCE_SHOP.casefold(), SOURCE_MANUAL.casefold()}


class MigrationError(IntegrationError):
    """Kế hoạch migration không đủ bằng chứng hoặc chạm vùng không được phép."""


class MigrationBlocked(MigrationError):
    """Migration dừng trước khi ghi vì backup/identity/layout chưa đạt."""


@dataclass(frozen=True)
class SourceContract:
    """Contract riêng của từng tab nguồn. Không dùng một phép dồn cột chung."""

    tab: str
    mode: str
    extra_clear_columns: tuple[str, ...] = ()

    def target_grid(
        self,
        shop_rows: Sequence[dict[str, Any]],
        manual_rows: Sequence[dict[str, Any]],
    ) -> list[list[Any]]:
        if self.mode == "shop":
            return _quote_grid(shop_rows)
        if self.mode == "stacked":
            stacked = _stacked_rows(list(manual_rows) + list(shop_rows))
            return _quote_grid(stacked)
        raise MigrationError(f"Contract '{self.mode}' của tab {self.tab} không hợp lệ.")


SOURCE_CONTRACTS: dict[str, SourceContract] = {
    # BAO_GIA chỉ chứa Shop Xoài Non và được tool ghi từ API.
    TAB_AUTO: SourceContract(TAB_AUTO, "shop", extra_clear_columns=("H",)),
    # BAO_GIA_TONG_HOP xếp chồng NASTELEID trước, rồi Shop Xoài Non.
    TAB_COMBINED: SourceContract(TAB_COMBINED, "stacked"),
}


@dataclass(frozen=True)
class PlannedChange:
    """Một thay đổi được phép. Không có insert/delete row hay format."""

    kind: str  # values | clear | validation
    tab: str
    range_a1: str
    values: list[list[Any]] = field(default_factory=list)
    value_input_option: str = "RAW"
    validation_type: str = ""
    validation_values: list[str] = field(default_factory=list)
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "tab": self.tab,
            "range": self.range_a1,
            "rows": len(self.values),
            "value_input_option": self.value_input_option,
            "validation_type": self.validation_type,
            "validation_values": self.validation_values,
            "reason": self.reason,
        }


@dataclass
class SourceRepairPlan:
    tab: str
    mode: str
    current_rows: int
    target_rows: int
    changed_cells: int
    operations: list[PlannedChange] = field(default_factory=list)
    diagnostic: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tab": self.tab,
            "mode": self.mode,
            "current_rows": self.current_rows,
            "target_rows": self.target_rows,
            "changed_cells": self.changed_cells,
            "operations": [item.as_dict() for item in self.operations],
            "diagnostic": self.diagnostic,
            "notes": self.notes,
        }


@dataclass
class MenuMigrationPlan:
    header_row: int
    target_row: int
    target_id: str
    product_id: str
    variant_id: str
    legacy_row: int
    legacy_duplicate_of: str
    selector_old: str
    selector_new: str
    formulas_old: dict[str, str] = field(default_factory=dict)
    formulas_new: dict[str, str] = field(default_factory=dict)
    operations: list[PlannedChange] = field(default_factory=list)
    product_count_after: int = 0
    unique_ids_after: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "header_row": self.header_row,
            "target_row": self.target_row,
            "target_id": self.target_id,
            "product_id": self.product_id,
            "variant_id": self.variant_id,
            "legacy_row": self.legacy_row,
            "legacy_duplicate_of": self.legacy_duplicate_of,
            "selector_old": self.selector_old,
            "selector_new": self.selector_new,
            "formulas_old": self.formulas_old,
            "formulas_new": self.formulas_new,
            "operations": [item.as_dict() for item in self.operations],
            "product_count_after": self.product_count_after,
            "unique_ids_after": self.unique_ids_after,
            "notes": self.notes,
        }


@dataclass
class BackupReadiness:
    status: str
    directory: str
    checksums: dict[str, str] = field(default_factory=dict)
    tabs: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    restore_plan: list[str] = field(default_factory=list)
    complete_fidelity: bool = False

    @property
    def ready(self) -> bool:
        return self.status == "READY"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "directory": self.directory,
            "checksums": self.checksums,
            "tabs": self.tabs,
            "blockers": self.blockers,
            "restore_plan": self.restore_plan,
            "complete_fidelity": self.complete_fidelity,
        }


@dataclass
class MigrationRun:
    status: str
    dry_run: bool
    applied: bool
    source_plans: list[SourceRepairPlan] = field(default_factory=list)
    menu_plan: MenuMigrationPlan | None = None
    backup: BackupReadiness | None = None
    operations: list[PlannedChange] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "dry_run": self.dry_run,
            "applied": self.applied,
            "source_plans": [item.as_dict() for item in self.source_plans],
            "menu_plan": self.menu_plan.as_dict() if self.menu_plan else None,
            "backup": self.backup.as_dict() if self.backup else None,
            "operations": [item.as_dict() for item in self.operations],
            "blockers": self.blockers,
            "notes": self.notes,
        }

    def text(self) -> str:
        parts = [
            f"menu-migrate status={self.status}",
            f"dry_run={str(self.dry_run).lower()}",
            f"applied={str(self.applied).lower()}",
            f"operations={len(self.operations)}",
        ]
        for plan in self.source_plans:
            parts.append(
                f"{plan.tab}: {plan.current_rows}->{plan.target_rows} dòng, "
                f"{plan.changed_cells} ô lệch, {len(plan.operations)} thao tác."
            )
        if self.menu_plan:
            parts.append(
                f"MENU: {self.menu_plan.target_id} -> {self.menu_plan.product_id}/"
                f"{self.menu_plan.variant_id} tại dòng {self.menu_plan.target_row}; "
                f"legacy dòng {self.menu_plan.legacy_row} "
                f"({self.menu_plan.legacy_duplicate_of})."
            )
            parts.append(
                f"K38 {self.menu_plan.selector_old} -> {self.menu_plan.selector_new}."
            )
            for cell, formula in self.menu_plan.formulas_new.items():
                parts.append(f"{cell}: {formula}")
            parts.append(
                f"Sau migration: {self.menu_plan.product_count_after} sản phẩm, "
                f"{self.menu_plan.unique_ids_after} ID duy nhất."
            )
        if self.backup:
            parts.append(f"Backup: {self.backup.status} tại {self.backup.directory}.")
        parts.extend(self.blockers)
        parts.extend(self.notes)
        return " ".join(part for part in parts if part)


class MigrationGuard:
    """Allowlist tuyệt đối cho migration. Không nới lỏng guard sync-menu."""

    def __init__(self, allowed_ranges: Iterable[str]) -> None:
        self.allowed_ranges = frozenset(allowed_ranges)

    def check(self, change: PlannedChange) -> None:
        if change.kind not in {"values", "clear", "validation"}:
            raise MigrationError(
                f"Từ chối thao tác '{change.kind}' trên {change.range_a1}. "
                "Migration chỉ được ghi giá trị, xóa giá trị hoặc đổi dropdown."
            )
        if change.range_a1 not in self.allowed_ranges:
            raise MigrationError(
                f"Từ chối ghi {change.range_a1}. Vùng này nằm ngoài allowlist migration."
            )
        if change.kind == "validation" and change.range_a1 != f"'{TAB_MENU}'!K38":
            raise MigrationError(
                f"Từ chối validation {change.range_a1}. Chỉ K38 được đổi dropdown."
            )

    def check_all(self, changes: Iterable[PlannedChange]) -> None:
        for change in changes:
            self.check(change)


def _quote_grid(rows: Sequence[dict[str, Any]]) -> list[list[Any]]:
    grid: list[list[Any]] = [list(QUOTE_HEADERS)]
    for row in rows:
        grid.append([_outgoing(row.get(header, "")) for header in QUOTE_HEADERS])
    return grid


def _cell(row: Sequence[Any], column: int) -> Any:
    if column >= len(row):
        return ""
    return row[column]


def _text(row: Sequence[Any], column: int) -> str:
    value = _cell(row, column)
    return "" if value is None else str(value).strip()


def _identity(row: dict[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("ProductID") or "").strip(),
        str(row.get("VariantID") or "").strip(),
    )


def validate_source_identities(rows: Sequence[dict[str, Any]]) -> list[str]:
    """Bắt buộc ProductID/VariantID trước khi dùng dòng API làm nguồn ghi."""

    problems: list[str] = []
    seen: dict[tuple[str, str], str] = {}
    names: dict[str, tuple[str, str]] = {}
    for index, row in enumerate(rows, start=1):
        product_id, variant_id = _identity(row)
        name = str(row.get("PRODUCT") or "").strip()
        price = price_number(row.get("PRICE"))
        if not product_id or not variant_id:
            problems.append(
                f"Dòng nguồn {index} '{name}' thiếu ProductID/VariantID. "
                "Không chuyển dữ liệu dựa vào tên hoặc vị trí dòng."
            )
            continue
        if price is None:
            problems.append(
                f"Dòng nguồn {index} {product_id}/{variant_id} không có PRICE hợp lệ."
            )
        key = (product_id, variant_id)
        if key in seen:
            problems.append(
                f"Trùng ProductID/VariantID {product_id}/{variant_id} ở dòng {seen[key]} và {index}."
            )
            continue
        seen[key] = str(index)
        folded = normalize_product_name(name)
        if folded in names and names[folded] != key:
            problems.append(
                f"Hai ProductID/VariantID khác nhau dùng cùng tên '{name}'. "
                "Không dùng tên làm định danh."
            )
        names[folded] = key
    return problems


def resolve_source_identity(
    name: object,
    price: object,
    shop_rows: Sequence[dict[str, Any]],
) -> tuple[str, str] | None:
    """Khớp tên + giá để chẩn đoán dòng legacy; không dùng để quyết định ghi."""

    wanted = normalize_product_name(name)
    if not wanted:
        return None
    exact = [
        row
        for row in shop_rows
        if normalize_product_name(row.get("PRODUCT")) == wanted
    ]
    if len(exact) == 1:
        return _identity(exact[0])
    matches = [
        row
        for row in shop_rows
        if same_source_product(
            str(name or ""),
            price,
            [(normalize_product_name(row.get("PRODUCT")), price_number(row.get("PRICE")))],
        )
    ]
    if len(matches) == 1:
        return _identity(matches[0])
    return None


def diagnose_source_grid(
    grid: Sequence[Sequence[Any]] | None,
    shop_rows: Sequence[dict[str, Any]],
    manual_rows: Sequence[dict[str, Any]] = (),
    *,
    tab: str,
) -> dict[str, Any]:
    """Đọc dòng nguồn hiện tại để báo lệch cột/mất/trùng identity. Chỉ đọc."""

    rows = list(grid or [])
    manual_names = {
        normalize_product_name(row.get("PRODUCT")) for row in manual_rows
    }
    matched: list[dict[str, Any]] = []
    manual_seen: list[str] = []
    unresolved: list[dict[str, Any]] = []
    duplicate: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], int] = {}
    for index, raw in enumerate(rows[1:], start=2):
        product_cell = _text(raw, 1)
        next_cell = _text(raw, 2)
        misaligned = product_cell.casefold() in SOURCE_LABELS and bool(next_cell)
        name = next_cell if misaligned else product_cell
        price = _cell(raw, 5)
        if not name and price_number(price) is None:
            continue
        identity = resolve_source_identity(name, price, shop_rows)
        if identity and identity[0] and identity[1]:
            item = {
                "row_number": index,
                "name": name,
                "product_id": identity[0],
                "variant_id": identity[1],
                "misaligned": misaligned,
            }
            if identity in seen:
                item["duplicate_of_row"] = seen[identity]
                duplicate.append(item)
            else:
                seen[identity] = index
            matched.append(item)
            continue
        if normalize_product_name(name) in manual_names:
            manual_seen.append(name)
            continue
        unresolved.append(
            {
                "row_number": index,
                "product_cell": product_cell,
                "candidate_name": name,
                "price": price,
                "misaligned": misaligned,
            }
        )
    matched_ids = {(item["product_id"], item["variant_id"]) for item in matched}
    missing = [
        {
            "product_id": product_id,
            "variant_id": variant_id,
            "name": str(row.get("PRODUCT") or ""),
            "price": row.get("PRICE"),
        }
        for row in shop_rows
        for product_id, variant_id in [_identity(row)]
        if (product_id, variant_id) not in matched_ids
    ]
    return {
        "layout_problems": quote_layout_problems(rows, tab=tab),
        "matched": matched,
        "manual_rows": manual_seen,
        "unresolved": unresolved,
        "duplicate_identities": duplicate,
        "missing_identities": missing,
    }


def _row_diff_count(
    current: Sequence[Sequence[Any]],
    target: Sequence[Sequence[Any]],
    *,
    columns: int,
) -> int:
    changed = 0
    for index in range(max(len(current), len(target))):
        left = current[index] if index < len(current) else []
        right = target[index] if index < len(target) else []
        for column in range(columns):
            if not _same_value(_cell(left, column), _cell(right, column)):
                changed += 1
    return changed


def _same_value(left: Any, right: Any) -> bool:
    if left == right:
        return True
    if str(left or "").strip() == "" and str(right or "").strip() == "":
        return True
    left_number = price_number(left)
    right_number = price_number(right)
    if left_number is not None and right_number is not None:
        return left_number == right_number
    return False


def plan_source_repairs(
    grids: dict[str, Sequence[Sequence[Any]]],
    shop_rows: Sequence[dict[str, Any]],
    manual_rows: Sequence[dict[str, Any]],
) -> list[SourceRepairPlan]:
    """Lập kế hoạch sửa schema từng tab theo contract riêng."""

    problems = validate_source_identities(shop_rows)
    if problems:
        raise MigrationBlocked(" ".join(problems))
    plans: list[SourceRepairPlan] = []
    for tab, contract in SOURCE_CONTRACTS.items():
        current = list(grids.get(tab) or [])
        target = contract.target_grid(shop_rows, manual_rows)
        if len(target[0]) != len(QUOTE_HEADERS):
            raise MigrationBlocked(f"{tab}: lưới đích không đúng {len(QUOTE_HEADERS)} cột.")
        changed = _row_diff_count(current, target, columns=len(QUOTE_HEADERS))
        extra = max((len(row) for row in current if row), default=len(QUOTE_HEADERS))
        if len(current) > len(target):
            for offset in range(len(target), len(current)):
                raw = current[offset]
                if any(_text(raw, column) for column in range(len(QUOTE_HEADERS))):
                    raise MigrationBlocked(
                        f"{tab}: dòng {offset + 1} có dữ liệu ngoài vùng đích "
                        f"{len(target)} dòng. Không tự xóa dòng; cần duyệt tay."
                    )
        operations: list[PlannedChange] = []
        if changed:
            operations.append(
                PlannedChange(
                    kind="values",
                    tab=tab,
                    range_a1=f"'{tab}'!A1:G{len(target)}",
                    values=[list(row) for row in target],
                    value_input_option="RAW",
                    reason=(
                        "Ghi lại toàn bộ vùng dữ liệu nguồn theo contract "
                        f"{contract.mode}; không dồn cột chung."
                    ),
                )
            )
        for column in contract.extra_clear_columns:
            column_index = ord(column.upper()) - ord("A")
            if any(_text(row, column_index) for row in current):
                operations.append(
                    PlannedChange(
                        kind="clear",
                        tab=tab,
                        range_a1=f"'{tab}'!{column.upper()}1:{column.upper()}{len(target)}",
                        values=[[""] for _ in range(len(target))],
                        value_input_option="RAW",
                        reason=f"Bỏ cột {column.upper()} trùng khỏi contract 7 cột.",
                    )
                )
        diagnostic = diagnose_source_grid(current, shop_rows, manual_rows, tab=tab)
        diagnostic["extra_columns"] = extra
        notes: list[str] = []
        if diagnostic["layout_problems"]:
            notes.append(
                f"{tab}: phát hiện lệch cột hiện tại; migration dựng lại từ "
                "ProductID/VariantID."
            )
        if diagnostic["unresolved"]:
            notes.append(
                f"{tab}: {len(diagnostic['unresolved'])} dòng legacy không khớp identity; "
                "không dùng các dòng này để chuyển dữ liệu."
            )
        if diagnostic["missing_identities"]:
            notes.append(
                f"{tab}: thiếu {len(diagnostic['missing_identities'])} identity so với API."
            )
        plans.append(
            SourceRepairPlan(
                tab=tab,
                mode=contract.mode,
                current_rows=max(len(current), 0),
                target_rows=len(target),
                changed_cells=changed,
                operations=operations,
                diagnostic=diagnostic,
                notes=notes,
            )
        )
    return plans


def _product_name_for_menu_row(raw: Sequence[Any]) -> tuple[str, bool]:
    product_cell = _text(raw, 1)
    next_cell = _text(raw, 2)
    misaligned = product_cell.casefold() in SOURCE_LABELS and bool(next_cell)
    return (next_cell if misaligned else product_cell), misaligned


def _row_blank(raw: Sequence[Any], columns: int = 8) -> bool:
    return all(not _text(raw, column) for column in range(columns))


def _validation_value(baseline: MenuBaseline, cell: str) -> str:
    for item in baseline.data_validations:
        label = str(item.get("cell") or "").strip()
        if not label:
            label = str(item.get("range") or "").split(":", 1)[0].strip()
        if label != cell:
            continue
        for value in item.get("values") or []:
            text = value.get("userEnteredValue") if isinstance(value, dict) else value
            if str(text or "").strip():
                return str(text).strip()
    return ""


def _formula_values(baseline: MenuBaseline) -> dict[str, str]:
    return {
        str(item.get("cell") or ""): str(item.get("formula") or "")
        for item in baseline.formulas
        if item.get("cell")
    }


def _formula_rewrite(formula: str, *, menu_end: int, tong_end: int) -> str:
    rewritten = re.sub(r"B8:H30\b", f"B8:H{menu_end}", formula)
    rewritten = re.sub(r"B8:C30\b", f"B8:C{menu_end}", rewritten)
    rewritten = re.sub(
        r"VLOOKUP\(K38;\s*BAO_GIA_TONG_HOP!C2:E24;\s*3;\s*FALSE\)",
        f"VLOOKUP(K38; BAO_GIA_TONG_HOP!B2:E{tong_end}; 4; FALSE)",
        rewritten,
    )
    return rewritten


def plan_menu_migration(
    grid: Sequence[Sequence[Any]],
    baseline: MenuBaseline,
    shop_rows: Sequence[dict[str, Any]],
    manual_rows: Sequence[dict[str, Any]] = (),
    *,
    target_row: int = MENU_TARGET_ROW,
    legacy_row: int = MENU_LEGACY_ROW,
) -> MenuMigrationPlan:
    """Lập kế hoạch MENU: thêm local_41, xóa bản sao legacy, mở rộng vùng chọn."""

    header_row = baseline.header_row or 0
    if header_row <= 0:
        raise MigrationBlocked("MENU chưa có dòng tiêu đề ID | PRODUCT.")
    header_cells = [
        _text(grid[header_row - 1], column) if header_row - 1 < len(grid) else ""
        for column in range(len(MENU_HEADERS))
    ]
    if [cell.upper() for cell in header_cells] != [h.upper() for h in MENU_HEADERS]:
        raise MigrationBlocked(
            "Dòng tiêu đề MENU không đúng mẫu " + " | ".join(MENU_HEADERS) + "."
        )
    capacity = MenuCapacity.from_baseline(baseline)
    if not capacity.row_allowed(target_row):
        raise MigrationBlocked(
            f"Dòng {target_row} nằm ngoài vùng A:H được phép (tới dòng "
            f"{capacity.last_writable_row})."
        )
    if any(capacity.cell_blocked(column, target_row) for column in range(8)):
        raise MigrationBlocked(
            f"Dòng {target_row} có merge/công thức/protected trong A:H."
        )

    target_identity = next(
        (
            _identity(row)
            for row in shop_rows
            if str(row.get("ProductID") or "").strip() == "local_41"
        ),
        None,
    )
    if target_identity is None or not all(target_identity):
        raise MigrationBlocked("Không thấy ProductID/VariantID local_41 trong nguồn API.")
    target_source = next(
        row for row in shop_rows if _identity(row) == target_identity
    )
    target_values = _menu_product_row(MENU_TARGET_ID, target_source)

    product_rows = [
        row_number
        for row_number in range(header_row + 1, len(grid) + 1)
        if is_menu_product_row(grid[row_number - 1])
    ]
    current_by_identity: dict[tuple[str, str], int] = {}
    for row_number in product_rows:
        raw = grid[row_number - 1]
        name, _misaligned = _product_name_for_menu_row(raw)
        identity = resolve_source_identity(name, _cell(raw, 5), shop_rows)
        if identity is None or not all(identity):
            raise MigrationBlocked(
                f"MENU dòng {row_number}: không xác định được ProductID/VariantID "
                f"cho '{name}'. Không dựa vào tên để migration."
            )
        current_by_identity.setdefault(identity, row_number)

    current_target = grid[target_row - 1] if target_row - 1 < len(grid) else []
    already_target = all(
        _same_value(_cell(current_target, column), target_values[column])
        for column in range(8)
    )
    if not already_target and not _row_blank(current_target):
        raise MigrationBlocked(
            f"MENU A{target_row}:H{target_row} không trống. Cần duyệt tay trước khi ghi."
        )

    api_identities = {_identity(row) for row in shop_rows}
    missing = [
        identity
        for identity in api_identities
        if identity not in current_by_identity
    ]
    if already_target:
        if missing:
            raise MigrationBlocked(
                "MENU đã có local_41 nhưng còn thiếu identity khác: "
                + ", ".join("/".join(item) for item in sorted(missing))
            )
    elif set(missing) != {target_identity}:
        raise MigrationBlocked(
            "Ngoài local_41 còn identity khác chưa có trên MENU: "
            + ", ".join("/".join(item) for item in sorted(missing - {target_identity}))
        )

    legacy_raw = grid[legacy_row - 1] if legacy_row - 1 < len(grid) else []
    legacy_duplicate_of = ""
    if not _row_blank(legacy_raw):
        legacy_name, _legacy_misaligned = _product_name_for_menu_row(legacy_raw)
        legacy_identity = resolve_source_identity(
            legacy_name,
            _cell(legacy_raw, 5),
            shop_rows,
        )
        if legacy_identity is None or not all(legacy_identity):
            raise MigrationBlocked(
                f"MENU dòng {legacy_row} không đối chiếu được ProductID/VariantID. "
                "Không xóa dòng legacy khi chưa xác minh."
            )
        first_row = current_by_identity.get(legacy_identity)
        if first_row is None or first_row >= legacy_row:
            raise MigrationBlocked(
                f"MENU dòng {legacy_row} không phải bản sao của một dòng phía trên."
            )
        legacy_duplicate_of = _text(grid[first_row - 1], 0)
        if not legacy_duplicate_of:
            raise MigrationBlocked(
                f"MENU dòng {legacy_row}: dòng gốc {first_row} không có ID."
            )

    after = [list(row) for row in grid]
    while len(after) < max(target_row, legacy_row):
        after.append([""] * 8)
    if not already_target:
        after[target_row - 1] = list(target_values)
    after[legacy_row - 1] = [""] * 8 + list(after[legacy_row - 1][8:])
    after_identities: set[tuple[str, str]] = set()
    after_ids: set[str] = set()
    for row_number in range(header_row + 1, MENU_EXTENDED_LAST_ROW + 1):
        raw = after[row_number - 1]
        if not is_menu_product_row(raw):
            continue
        name, _misaligned = _product_name_for_menu_row(raw)
        identity = resolve_source_identity(name, _cell(raw, 5), shop_rows)
        if identity is None or not all(identity):
            raise MigrationBlocked(
                f"Sau migration, MENU dòng {row_number} vẫn không có identity."
            )
        after_identities.add(identity)
        item_id = _text(raw, 0)
        if item_id:
            after_ids.add(item_id)
    if after_identities != api_identities:
        raise MigrationBlocked(
            "Sau migration MENU không đủ 24 identity. Thiếu: "
            + ", ".join(
                "/".join(item) for item in sorted(api_identities - after_identities)
            )
        )
    if len(after_ids) != len(api_identities):
        raise MigrationBlocked(
            f"Sau migration MENU có {len(after_ids)} ID cho "
            f"{len(api_identities)} sản phẩm."
        )

    selector_old = capacity.selector_span
    selector_new = f"B8:B{MENU_EXTENDED_LAST_ROW}"
    current_validation = _validation_value(baseline, "K38")
    expected_validation = f"=MENU!$B$8:$B${MENU_EXTENDED_LAST_ROW}"
    if current_validation not in {
        "=MENU!$B$8:$B$30",
        expected_validation,
    }:
        raise MigrationBlocked(
            f"K38 đang là '{current_validation or '(trống)'}', không khớp mẫu "
            "=MENU!$B$8:$B$30. Không tự đổi vùng chọn."
        )

    formulas = _formula_values(baseline)
    for cell in ("J39", "J40"):
        if not formulas.get(cell):
            raise MigrationBlocked(f"Thiếu công thức {cell} trong baseline MENU.")
    tong_last_row = 1 + len(manual_rows) + len(shop_rows)
    target_tong_row = 1 + len(manual_rows) + next(
        index
        for index, row in enumerate(shop_rows, start=1)
        if _identity(row) == target_identity
    )
    if target_tong_row > tong_last_row:
        raise MigrationBlocked(
            f"local_41 ở BAO_GIA_TONG_HOP dòng {target_tong_row}, ngoài vùng "
            f"{tong_last_row}."
        )

    operations: list[PlannedChange] = []
    if not already_target:
        operations.append(
            PlannedChange(
                kind="values",
                tab=TAB_MENU,
                range_a1=f"'{TAB_MENU}'!A{target_row}:H{target_row}",
                values=[list(target_values)],
                value_input_option="RAW",
                reason="Ghi local_41 vào dòng 31 với mã SXN-24.",
            )
        )
    if not _row_blank(legacy_raw):
        operations.append(
            PlannedChange(
                kind="clear",
                tab=TAB_MENU,
                range_a1=f"'{TAB_MENU}'!A{legacy_row}:H{legacy_row}",
                values=[[""] * 8],
                value_input_option="RAW",
                reason=(
                    f"Xóa giá trị bản sao legacy dòng {legacy_row} "
                    f"(trùng {legacy_duplicate_of}); không xóa dòng, không đụng I:M."
                ),
            )
        )
    if current_validation != expected_validation:
        operations.append(
            PlannedChange(
                kind="validation",
                tab=TAB_MENU,
                range_a1=f"'{TAB_MENU}'!K38",
                validation_type="ONE_OF_RANGE",
                validation_values=[expected_validation],
                reason="Mở rộng dropdown K38 tới dòng 35.",
            )
        )

    formulas_old: dict[str, str] = {}
    formulas_new: dict[str, str] = {}
    for cell in ("J39", "J40"):
        old = formulas[cell]
        new = _formula_rewrite(
            old,
            menu_end=MENU_EXTENDED_LAST_ROW,
            tong_end=tong_last_row,
        )
        if cell == "J39":
            if f"B8:H{MENU_EXTENDED_LAST_ROW}" in old:
                continue
        elif (
            f"B8:C{MENU_EXTENDED_LAST_ROW}" in old
            and f"BAO_GIA_TONG_HOP!B2:E{tong_last_row}" in old
            and "C2:E24" not in old
        ):
            continue
        if new == old or not any(
            token in old for token in ("B8:H30", "B8:C30", "C2:E24")
        ):
            raise MigrationBlocked(
                f"{cell} không khớp mẫu cũ B8:H30/B8:C30/BAO_GIA_TONG_HOP!C2:E24."
            )
        formulas_old[cell] = old
        formulas_new[cell] = new
        operations.append(
            PlannedChange(
                kind="values",
                tab=TAB_MENU,
                range_a1=f"'{TAB_MENU}'!{cell}",
                values=[[new]],
                value_input_option="USER_ENTERED",
                reason=(
                    f"Mở rộng {cell} tới dòng {MENU_EXTENDED_LAST_ROW}; "
                    f"J40 đọc BAO_GIA_TONG_HOP B2:E{tong_last_row}."
                ),
            )
        )
    if target_row > MENU_EXTENDED_LAST_ROW:
        raise MigrationBlocked(
            f"Dòng {target_row} vượt vùng chọn mới tới dòng {MENU_EXTENDED_LAST_ROW}."
        )

    return MenuMigrationPlan(
        header_row=header_row,
        target_row=target_row,
        target_id=MENU_TARGET_ID,
        product_id=target_identity[0],
        variant_id=target_identity[1],
        legacy_row=legacy_row,
        legacy_duplicate_of=legacy_duplicate_of,
        selector_old=selector_old,
        selector_new=selector_new,
        formulas_old=formulas_old,
        formulas_new=formulas_new,
        operations=operations,
        product_count_after=len(after_identities),
        unique_ids_after=len(after_ids),
        notes=[
            "Không sửa I:M ngoài K38/J39/J40.",
            "Không chèn/xóa dòng, không đổi merge, chiều cao hay chiều rộng.",
        ],
    )


def _restore_plan(operations: Sequence[PlannedChange]) -> list[str]:
    plan: list[str] = []
    for change in operations:
        if change.kind == "validation":
            plan.append(
                f"Restore dropdown {change.range_a1} từ data_validations trong "
                "structures.json (MENU)."
            )
        else:
            plan.append(
                f"Restore giá trị/công thức {change.range_a1} từ snapshot.json "
                f"({change.tab}) bằng đúng valueRenderOption gốc."
            )
    plan.append("Restore không chèn/xóa dòng và không phục hồi định dạng ô.")
    return plan


def create_migration_backup(
    gateway: Any,
    spreadsheet_id: str,
    *,
    tabs: Sequence[str] = MIGRATION_TABS,
    root: str | Path | None = None,
    operations: Sequence[PlannedChange] = (),
    baseline_path: str = "",
    secrets: Sequence[str] = (),
) -> BackupReadiness:
    """Chụp dữ liệu + metadata của đúng các tab migration. Không ghi Google."""

    blockers: list[str] = []
    try:
        snapshot = gateway.backup_snapshot(spreadsheet_id)
    except Exception as exc:
        return BackupReadiness(
            status="BLOCKED",
            directory="",
            blockers=[f"Không đọc được snapshot spreadsheet: {exc}"],
        )
    if not isinstance(snapshot, dict):
        return BackupReadiness(
            status="BLOCKED",
            directory="",
            blockers=["Snapshot spreadsheet không phải dict."],
        )
    snapshot_tabs = {
        str(item.get("title"))
        for item in snapshot.get("sheets", [])
        if isinstance(item, dict) and item.get("title")
    }
    missing = [tab for tab in tabs if tab not in snapshot_tabs]
    if missing:
        blockers.append("Snapshot thiếu tab: " + ", ".join(missing))
    structures: dict[str, dict[str, Any]] = {}
    for tab in tabs:
        try:
            structure = gateway.get_sheet_structure(spreadsheet_id, tab)
        except Exception as exc:
            blockers.append(f"Không đọc được cấu trúc {tab}: {exc}")
            continue
        if not isinstance(structure, dict):
            blockers.append(f"Cấu trúc {tab} không hợp lệ.")
            continue
        for key in (
            "values",
            "formulas",
            "data_validations",
            "merges",
            "protected_ranges",
            "column_widths",
            "row_heights",
        ):
            if key not in structure:
                blockers.append(f"Cấu trúc {tab} thiếu '{key}'.")
        structures[tab] = structure
    if baseline_path and TAB_MENU in structures:
        try:
            saved = load_menu_baseline(baseline_path)
            current = menu_baseline_from_structure(
                structures[TAB_MENU],
                MENU_HEADERS,
            )
            differences = menu_structure_diff(saved, current)
            if differences:
                blockers.append(
                    f"MENU lệch baseline '{baseline_path}': "
                    + "; ".join(differences[:5])
                    + (f" (+{len(differences) - 5} nữa)" if len(differences) > 5 else "")
                )
        except Exception as exc:
            blockers.append(f"Không so sánh được baseline '{baseline_path}': {exc}")
    payload = {"snapshot": snapshot, "structures": structures}
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    if "BEGIN PRIVATE KEY" in text or "END PRIVATE KEY" in text:
        blockers.append("Snapshot còn dấu khóa riêng tư. Không lưu backup.")
    for secret in secrets:
        if len(str(secret)) >= 8 and str(secret) in text:
            blockers.append("Snapshot còn bí mật cấu hình. Không lưu backup.")
    stamp = format_timestamp(now_vietnam()).replace(":", "").replace("-", "")
    directory = Path(root) if root else (PROJECT_ROOT / "backups")
    folder = directory / f"migration-{stamp}"
    suffix = 2
    while folder.exists():
        folder = directory / f"migration-{stamp}-{suffix}"
        suffix += 1
    folder.mkdir(parents=True, exist_ok=False)
    snapshot_path = folder / "snapshot.json"
    structures_path = folder / "structures.json"
    snapshot_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    structures_path.write_text(
        json.dumps(structures, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    snapshot_checksum = checksum(snapshot)
    structures_checksum = checksum(structures)
    restore = _restore_plan(operations)
    manifest = {
        "status": "BLOCKED" if blockers else "READY",
        "created_at": format_timestamp(now_vietnam()),
        "spreadsheet_id": spreadsheet_id,
        "reused_snapshot": False,
        "complete_fidelity": False,
        "scope": (
            "values+formulas của tất cả tab; metadata cấu trúc cho các tab "
            "migration. Không chứa style/ảnh/comment."
        ),
        "tabs": list(tabs),
        "snapshot_checksum": snapshot_checksum,
        "structures_checksum": structures_checksum,
        "blockers": blockers,
        "restore_plan": restore,
    }
    (folder / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        reloaded_snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        reloaded_structures = json.loads(structures_path.read_text(encoding="utf-8"))
        if checksum(reloaded_snapshot) != snapshot_checksum:
            blockers.append("Checksum snapshot không khớp sau khi ghi.")
        if checksum(reloaded_structures) != structures_checksum:
            blockers.append("Checksum structures không khớp sau khi ghi.")
    except Exception as exc:
        blockers.append(f"Không đọc lại được backup: {exc}")
    if blockers and manifest["status"] != "BLOCKED":
        manifest["status"] = "BLOCKED"
        manifest["blockers"] = blockers
        (folder / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return BackupReadiness(
        status="BLOCKED" if blockers else "READY",
        directory=str(folder),
        checksums={
            "snapshot": snapshot_checksum,
            "structures": structures_checksum,
        },
        tabs=list(tabs),
        blockers=blockers,
        restore_plan=restore,
    )


def _range_cell(range_a1: str) -> tuple[str, int, int]:
    match = re.match(r"^'([^']+)'!([A-Za-z]{1,3})(\d+)$", range_a1.strip())
    if match is None:
        raise MigrationError(f"Vùng '{range_a1}' không phải một ô A1 hợp lệ.")
    return match.group(1), ord(match.group(2).upper()) - ord("A"), int(match.group(3))


def execute_migration(
    gateway: Any,
    spreadsheet_id: str,
    changes: Sequence[PlannedChange],
) -> None:
    """Ghi đúng các thay đổi đã duyệt. Chỉ gọi khi live=True."""

    raw_items: list[tuple[str, list[list[Any]]]] = []
    user_items: list[tuple[str, list[list[Any]]]] = []
    validations: list[PlannedChange] = []
    for change in changes:
        if change.kind == "validation":
            validations.append(change)
        elif change.value_input_option == "USER_ENTERED":
            user_items.append((change.range_a1, change.values))
        else:
            raw_items.append((change.range_a1, change.values))
    if raw_items:
        gateway.batch_update_values(
            spreadsheet_id,
            raw_items,
            value_input_option="RAW",
        )
    if user_items:
        gateway.batch_update_values(
            spreadsheet_id,
            user_items,
            value_input_option="USER_ENTERED",
        )
    if validations:
        meta = {
            str(item.get("title")): item
            for item in gateway.list_sheet_meta(spreadsheet_id)
            if isinstance(item, dict)
        }
        requests: list[dict[str, Any]] = []
        for change in validations:
            tab, column, row = _range_cell(change.range_a1)
            sheet_id = meta.get(tab, {}).get("sheetId")
            if not isinstance(sheet_id, int):
                raise MigrationError(f"Không thấy sheetId của tab {tab}.")
            requests.append(
                {
                    "setDataValidation": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": row - 1,
                            "endRowIndex": row,
                            "startColumnIndex": column,
                            "endColumnIndex": column + 1,
                        },
                        "rule": {
                            "condition": {
                                "type": change.validation_type,
                                "values": [
                                    {"userEnteredValue": value}
                                    for value in change.validation_values
                                ],
                            },
                            "strict": True,
                            "showCustomUi": True,
                        },
                    }
                }
            )
        gateway.batch_update(spreadsheet_id, requests)


def run_migration(
    gateway: Any,
    spreadsheet_id: str,
    shop_rows: Sequence[dict[str, Any]],
    *,
    manual_rows: Sequence[dict[str, Any]] | None = None,
    live: bool = False,
    backup_root: str | Path | None = None,
    baseline_path: str = "",
    secrets: Sequence[str] = (),
) -> MigrationRun:
    """Chạy dry-run mặc định. Chỉ ghi khi live=True và backup READY."""

    titles = set(gateway.list_sheet_titles(spreadsheet_id))
    missing_tabs = [tab for tab in MIGRATION_TABS if tab not in titles]
    if missing_tabs:
        return MigrationRun(
            status="BLOCKED",
            dry_run=not live,
            applied=False,
            blockers=["Thiếu tab: " + ", ".join(missing_tabs)],
        )
    grids = {
        tab: gateway.get_values(spreadsheet_id, f"'{tab}'!A1:ZZ")
        for tab in MIGRATION_TABS
    }
    if manual_rows is None:
        manual_rows, _manual_notes = manual_quote_rows(grids[TAB_MANUAL])
    source_plans = plan_source_repairs(grids, shop_rows, manual_rows)
    structure = gateway.get_sheet_structure(spreadsheet_id, TAB_MENU)
    baseline = menu_baseline_from_structure(structure, MENU_HEADERS)
    menu_plan = plan_menu_migration(
        grids[TAB_MENU],
        baseline,
        shop_rows,
        manual_rows,
    )
    operations = [
        change for plan in source_plans for change in plan.operations
    ] + list(menu_plan.operations)
    allowed: set[str] = set()
    for plan in source_plans:
        allowed.add(f"'{plan.tab}'!A1:G{plan.target_rows}")
        contract = SOURCE_CONTRACTS[plan.tab]
        for column in contract.extra_clear_columns:
            allowed.add(
                f"'{plan.tab}'!{column.upper()}1:{column.upper()}{plan.target_rows}"
            )
    allowed.update(
        {
            f"'{TAB_MENU}'!A{MENU_TARGET_ROW}:H{MENU_TARGET_ROW}",
            f"'{TAB_MENU}'!A{MENU_LEGACY_ROW}:H{MENU_LEGACY_ROW}",
            f"'{TAB_MENU}'!K38",
            f"'{TAB_MENU}'!J39",
            f"'{TAB_MENU}'!J40",
        }
    )
    MigrationGuard(allowed).check_all(operations)
    backup = create_migration_backup(
        gateway,
        spreadsheet_id,
        root=backup_root,
        operations=operations,
        baseline_path=baseline_path,
        secrets=secrets,
    )
    blockers = list(backup.blockers)
    notes = [
        "Migration không đổi I:M ngoài K38/J39/J40.",
        "Không chèn/xóa dòng, không đổi merge/chiều cao/chiều rộng.",
        "Không cập nhật baseline chính thức trong lượt này.",
    ]
    if blockers:
        return MigrationRun(
            status="BLOCKED",
            dry_run=not live,
            applied=False,
            source_plans=source_plans,
            menu_plan=menu_plan,
            backup=backup,
            operations=operations,
            blockers=blockers,
            notes=notes,
        )
    applied = False
    if live:
        execute_migration(gateway, spreadsheet_id, operations)
        applied = True
    status = "UP_TO_DATE" if not operations else ("APPLIED" if live else "PLANNED")
    return MigrationRun(
        status=status,
        dry_run=not live,
        applied=applied,
        source_plans=source_plans,
        menu_plan=menu_plan,
        backup=backup,
        operations=operations,
        blockers=[],
        notes=notes,
    )
