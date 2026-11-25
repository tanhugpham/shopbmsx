"""Hồi quy Phase 4.3E: migration nguồn + MENU, backup, guard và idempotency.

Không ghi Google Sheets. Mọi test dùng gateway giả.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from shop_integration.combined_quote import (
    MENU_HEADERS,
    QUOTE_HEADERS,
    SOURCE_MANUAL,
    SOURCE_SHOP,
    TAB_AUTO,
    TAB_COMBINED,
    TAB_MANUAL,
    TAB_MENU,
    CombinedQuoteWriter,
    shop_quote_rows,
)
from shop_integration.menu_migration import (
    MENU_EXTENDED_LAST_ROW,
    MENU_LEGACY_ROW,
    MENU_TARGET_ID,
    MENU_TARGET_ROW,
    MigrationBlocked,
    MigrationError,
    MigrationGuard,
    PlannedChange,
    create_migration_backup,
    plan_menu_migration,
    plan_source_repairs,
    run_migration,
    validate_source_identities,
)
from shop_integration.menu_layout import menu_baseline_from_structure
from shop_integration.models import Product, Variant
from shop_integration.pricing import PricingPolicy, quote_cost, round_pretty_vnd
from tests.test_phase43d_menu_capacity import (
    _live_grid,
    _source_rows,
    _structure,
)

_RANGE = re.compile(r"^'([^']+)'!([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$")


def _manual_rows() -> list[dict[str, Any]]:
    return [
        {
            "ID": 1,
            "Nguồn": SOURCE_MANUAL,
            "PRODUCT": "AI PRO",
            "DESCRIPTION": "test",
            "DURATION": "TESST",
            "WARRANTY": "HELLO",
            "PRICE": 100000,
            "STOCK": 23,
            "ProductID": "",
            "VariantID": "",
        }
    ]


def _shifted_rows(rows: list[dict[str, Any]]) -> list[list[Any]]:
    return [
        [
            row["ID"],
            SOURCE_SHOP,
            row["PRODUCT"],
            row["PRODUCT"],
            row["DESCRIPTION"],
            row["PRICE"],
            row["STOCK"],
        ]
        for row in rows
    ]


def _breaked_source_grids() -> dict[str, list[list[Any]]]:
    """Mô phỏng đúng lỗi live: B là tên nguồn, H của BAO_GIA trùng STOCK."""

    rows = _source_rows()
    return {
        TAB_AUTO: [[*QUOTE_HEADERS, "STOCK"], *_shifted_rows(rows[:23])],
        TAB_COMBINED: [
            list(QUOTE_HEADERS),
            ["1", "AI PRO", "test", "TESST", "HELLO", 100000, 23],
            *_shifted_rows(rows[:23]),
        ],
    }


def _source_structure(tab: str, values: list[list[Any]], sheet_id: int) -> dict[str, Any]:
    return {
        "title": tab,
        "sheet_id": sheet_id,
        "grid_properties": {"rowCount": 100, "columnCount": 26},
        "conditional_format_count": 0,
        "merges": [],
        "protected_ranges": [],
        "values": [list(row) for row in values],
        "formulas": [],
        "data_validations": [],
        "column_widths": {},
        "row_heights": {},
    }


class MigrationBook:
    """Gateway giả: values + cấu trúc MENU + snapshot, không gọi Google."""

    def __init__(
        self,
        titles: list[str],
        values: dict[str, list[list[Any]]],
        menu_structure: dict[str, Any] | None = None,
    ) -> None:
        self.titles = list(titles)
        self.values = {
            tab: [list(row) for row in rows]
            for tab, rows in values.items()
        }
        self.menu_structure = menu_structure or _structure(
            grid=self.values.get(TAB_MENU)
        )
        self.value_batches: list[tuple[list[tuple[str, list[list[Any]]]], str]] = []
        self.batches: list[list[dict[str, Any]]] = []

    def list_sheet_titles(self, spreadsheet_id: str) -> list[str]:
        del spreadsheet_id
        return list(self.titles)

    def get_values(self, spreadsheet_id: str, range_a1: str) -> list[list[Any]]:
        del spreadsheet_id
        tab = range_a1.split("!", 1)[0].strip("'")
        return [list(row) for row in self.values.get(tab, [])]

    def list_sheet_meta(self, spreadsheet_id: str) -> list[dict[str, Any]]:
        del spreadsheet_id
        return [
            {"title": tab, "sheetId": index, "bandingIds": []}
            for index, tab in enumerate(self.titles)
        ]

    def get_sheet_structure(
        self,
        spreadsheet_id: str,
        title: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        del spreadsheet_id
        if title == TAB_MENU:
            return self.menu_structure
        return _source_structure(
            title,
            self.values.get(title, []),
            self.titles.index(title),
        )

    def _formula_grid(self, tab: str) -> list[list[Any]]:
        grid = [list(row) for row in self.values.get(tab, [])]
        if tab != TAB_MENU:
            return grid
        for item in self.menu_structure.get("formulas", []):
            match = re.match(r"^([A-Z]+)(\d+)$", str(item.get("cell") or ""))
            if match is None or not item.get("formula"):
                continue
            column = ord(match.group(1)) - ord("A")
            row = int(match.group(2))
            while len(grid) < row:
                grid.append([])
            while len(grid[row - 1]) <= column:
                grid[row - 1].append("")
            grid[row - 1][column] = item["formula"]
        return grid

    def backup_snapshot(self, spreadsheet_id: str) -> dict[str, Any]:
        return {
            "spreadsheet_id": spreadsheet_id,
            "title": "Test",
            "locale": "vi_VN",
            "time_zone": "Asia/Ho_Chi_Minh",
            "sheets": [
                {
                    "sheet_id": index,
                    "title": tab,
                    "index": index,
                    "hidden": False,
                    "grid_properties": {},
                    "formulas": self._formula_grid(tab),
                    "values": [list(row) for row in self.values.get(tab, [])],
                }
                for index, tab in enumerate(self.titles)
            ],
        }

    def batch_update_values(
        self,
        spreadsheet_id: str,
        items: list[tuple[str, list[list[Any]]]],
        *,
        value_input_option: str = "RAW",
    ) -> None:
        del spreadsheet_id
        self.value_batches.append((items, value_input_option))

    def batch_update(
        self,
        spreadsheet_id: str,
        requests: list[dict[str, Any]],
    ) -> None:
        del spreadsheet_id
        self.batches.append(requests)


def _book(titles: list[str] | None = None) -> MigrationBook:
    return MigrationBook(
        titles or [TAB_MENU, TAB_MANUAL, TAB_AUTO, TAB_COMBINED],
        {
            TAB_MENU: _live_grid(),
            TAB_MANUAL: [
                list(QUOTE_HEADERS),
                ["1", "AI PRO", "test", "TESST", "HELLO", 100000, 23],
            ],
            **_breaked_source_grids(),
        },
    )


def _apply(book: MigrationBook, changes: list[PlannedChange]) -> None:
    """Mô phỏng ghi đúng các thao tác để kiểm tra idempotency."""

    for change in changes:
        if change.kind == "validation":
            book.menu_structure["data_validations"] = [
                {
                    "cell": "K38",
                    "type": change.validation_type,
                    "values": [
                        {"userEnteredValue": value}
                        for value in change.validation_values
                    ],
                }
            ]
            continue
        if change.tab == TAB_MENU and change.range_a1 in {
            f"'{TAB_MENU}'!J39",
            f"'{TAB_MENU}'!J40",
        }:
            cell = change.range_a1.split("!", 1)[1]
            formula = change.values[0][0]
            for item in book.menu_structure["formulas"]:
                if item.get("cell") == cell:
                    item["formula"] = formula
                    break
            else:
                book.menu_structure["formulas"].append(
                    {"cell": cell, "formula": formula}
                )
            continue
        match = _RANGE.match(change.range_a1)
        assert match is not None, change.range_a1
        tab, start_col, start_row, _end_col, end_row = match.groups()
        start_column = ord(start_col) - ord("A")
        last_row = int(end_row or start_row)
        rows = book.values.setdefault(tab, [])
        while len(rows) < last_row:
            rows.append([])
        for offset, line in enumerate(change.values):
            row_number = int(start_row) + offset
            row = rows[row_number - 1]
            for column_offset, value in enumerate(line):
                target = start_column + column_offset
                while len(row) <= target:
                    row.append("")
                row[target] = value


def test_backup_restore_covers_exactly_eight_operations_with_formulas_and_dropdown(
    tmp_path: Path,
) -> None:
    book = _book()
    result = run_migration(book, "sheet-id", _source_rows(), backup_root=tmp_path)
    assert len(result.operations) == 8
    assert result.backup is not None and result.backup.ready
    folder = Path(result.backup.directory)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    snapshot = json.loads((folder / "snapshot.json").read_text(encoding="utf-8"))
    structures = json.loads((folder / "structures.json").read_text(encoding="utf-8"))
    sheets = {item["title"]: item for item in snapshot["sheets"]}

    restore_text = " ".join(manifest["restore_plan"])
    restored_ranges = set(
        re.findall(r"'[^']+'![A-Z]+\d+(?::[A-Z]+\d+)?", restore_text)
    )
    assert restored_ranges == {change.range_a1 for change in result.operations}
    menu_cells = {
        item.split("!", 1)[1]
        for item in restored_ranges
        if item.startswith(f"'{TAB_MENU}'!")
    }
    assert menu_cells <= {"A31:H31", "A36:H36", "K38", "J39", "J40"}

    menu_values = sheets[TAB_MENU]["values"]
    assert all(not str(cell or "").strip() for cell in menu_values[MENU_TARGET_ROW - 1][:8])
    assert str(menu_values[MENU_LEGACY_ROW - 1][0]).strip() == "SXN-24"
    assert str(sheets[TAB_AUTO]["values"][0][7]).strip() == "STOCK"

    old_validation = next(
        item
        for item in structures[TAB_MENU]["data_validations"]
        if item.get("cell") == "K38"
    )
    assert old_validation["values"][0]["userEnteredValue"] == "=MENU!$B$8:$B$30"

    for cell, old_formula in result.menu_plan.formulas_old.items():
        row = int(cell[1:])
        column = ord(cell[0]) - ord("A")
        assert sheets[TAB_MENU]["formulas"][row - 1][column] == old_formula

    # Snapshot has enough source rows to restore the migrated grids; the new
    # 25th/26th row did not exist before migration and restores as blank.
    assert len(sheets[TAB_AUTO]["values"]) == 24
    assert len(sheets[TAB_COMBINED]["values"]) == 25
    assert len(sheets[TAB_AUTO]["values"][0]) >= 7
    assert len(sheets[TAB_COMBINED]["values"][0]) == 7


def test_source_prices_are_customer_selling_prices_not_costs() -> None:
    products = [
        Product(
            id="local_1",
            title="ChatGPT Plus",
            variants=[
                Variant(
                    id="local_1",
                    price_vnd=159000,
                    in_stock=True,
                    in_stock_presence="value",
                    available_quantity=17,
                    quantity_presence="value",
                )
            ],
        ),
        Product(
            id="local_41",
            title="Adobe All Apps",
            variants=[
                Variant(
                    id="local_41",
                    price_vnd=69000,
                    in_stock=True,
                    in_stock_presence="value",
                    available_quantity=7,
                    quantity_presence="value",
                )
            ],
        ),
    ]
    shop_rows = shop_quote_rows(products, PricingPolicy())
    plans = plan_source_repairs(
        {
            TAB_AUTO: [list(QUOTE_HEADERS)],
            TAB_COMBINED: [list(QUOTE_HEADERS)],
        },
        shop_rows,
        [],
    )
    for plan in plans:
        write = next(change for change in plan.operations if change.kind == "values")
        target_by_serial = {row[0]: row for row in write.values[1:]}
        for source in shop_rows:
            cost = next(
                variant.price_vnd
                for product in products
                if product.id == source["ProductID"]
                for variant in product.variants
                if variant.id == source["VariantID"]
            )
            expected = round_pretty_vnd(quote_cost(cost).selling or 0)
            assert source["PRICE"] == expected
            assert target_by_serial[source["ID"]][5] == expected
            assert target_by_serial[source["ID"]][5] != cost


def test_ai_pro_manual_row_survives_migration_and_next_sync_plan(
    tmp_path: Path,
) -> None:
    book = _book()
    first = run_migration(book, "sheet-id", _source_rows(), backup_root=tmp_path)
    _apply(book, first.operations)
    ai_pro = [1, "AI PRO", "test", "TESST", "HELLO", 100000, 23]
    assert book.values[TAB_COMBINED][1][:7] == ai_pro

    changed_shop = [dict(row) for row in _source_rows()]
    changed_shop[-1]["STOCK"] = 9
    update = CombinedQuoteWriter(book, "sheet-id").update(changed_shop, dry_run=True)

    assert book.values[TAB_COMBINED][1][:7] == ai_pro
    assert f"'{TAB_COMBINED}'!A1" not in update.ranges
    row_two = {
        f"'{TAB_COMBINED}'!{column}2" for column in ("A", "B", "C", "D", "E", "F", "G")
    }
    assert not (row_two & set(update.ranges))
    assert f"'{TAB_COMBINED}'!G26" in update.ranges


def test_source_repair_uses_separate_contracts_and_product_ids() -> None:
    plans = plan_source_repairs(_breaked_source_grids(), _source_rows(), _manual_rows())
    by_tab = {plan.tab: plan for plan in plans}
    assert by_tab[TAB_AUTO].target_rows == 25
    assert by_tab[TAB_COMBINED].target_rows == 26
    auto_grid = by_tab[TAB_AUTO].operations[0].values
    combined_grid = by_tab[TAB_COMBINED].operations[0].values
    assert auto_grid[0] == QUOTE_HEADERS
    assert combined_grid[0] == QUOTE_HEADERS
    assert [row[0] for row in auto_grid[1:]] == list(range(1, 25))
    assert [row[0] for row in combined_grid[1:]] == list(range(1, 26))
    assert combined_grid[1][1] == "AI PRO"
    adobe = next(row for row in auto_grid[1:] if "Adobe" in str(row[1]))
    assert adobe[1] == "Adobe All Apps 1 Tháng riêng tư - BH7D"
    assert adobe[2] == "Mô tả Adobe"
    assert adobe[3] == "1 Tháng"
    assert adobe[4] == "BH7D"
    assert adobe[5] == 90000
    assert adobe[6] == 7
    assert by_tab[TAB_AUTO].operations[-1].range_a1 == f"'{TAB_AUTO}'!H1:H25"
    assert by_tab[TAB_COMBINED].operations[-1].kind == "values"


def test_source_identity_validation_rejects_missing_or_duplicate_ids() -> None:
    rows = [dict(row) for row in _source_rows()]
    assert validate_source_identities(rows) == []
    missing = [dict(row) for row in rows]
    missing[0]["ProductID"] = ""
    assert any("thiếu ProductID" in item for item in validate_source_identities(missing))
    duplicate = [dict(row) for row in rows]
    duplicate[1]["ProductID"] = duplicate[0]["ProductID"]
    duplicate[1]["VariantID"] = duplicate[0]["VariantID"]
    assert any(
        "Trùng ProductID/VariantID" in item
        for item in validate_source_identities(duplicate)
    )


def test_source_diagnostic_finds_only_local_41_missing() -> None:
    plans = plan_source_repairs(_breaked_source_grids(), _source_rows(), _manual_rows())
    for plan in plans:
        missing = [item["product_id"] for item in plan.diagnostic["missing_identities"]]
        assert missing == ["local_41"]
        assert plan.diagnostic["layout_problems"]
        assert plan.diagnostic["unresolved"] == []


def test_menu_plan_assigns_sxn24_to_local41_at_row31() -> None:
    book = _book()
    baseline = menu_baseline_from_structure(book.menu_structure, MENU_HEADERS)
    plan = plan_menu_migration(
        book.values[TAB_MENU],
        baseline,
        _source_rows(),
        _manual_rows(),
    )
    assert plan.target_row == MENU_TARGET_ROW == 31
    assert plan.target_id == MENU_TARGET_ID == "SXN-24"
    assert plan.product_id == "local_41"
    assert plan.variant_id == "local_41"
    assert plan.product_id != "local_1"
    write = next(
        change
        for change in plan.operations
        if change.range_a1 == f"'{TAB_MENU}'!A31:H31"
    )
    assert write.values[0][0] == "SXN-24"
    assert write.values[0][1] == "Adobe All Apps 1 Tháng riêng tư - BH7D"
    assert "ChatGPT" not in write.values[0][1]
    assert write.values[0][5] == 90000
    assert write.values[0][6] == 7
    assert write.values[0][7] == "IN STOCK"


def test_menu_plan_clears_legacy_row_36_only_after_duplicate_check() -> None:
    book = _book()
    baseline = menu_baseline_from_structure(book.menu_structure, MENU_HEADERS)
    plan = plan_menu_migration(
        book.values[TAB_MENU],
        baseline,
        _source_rows(),
        _manual_rows(),
    )
    assert plan.legacy_row == MENU_LEGACY_ROW == 36
    assert plan.legacy_duplicate_of == "SXN-01"
    clear = next(
        change
        for change in plan.operations
        if change.range_a1 == f"'{TAB_MENU}'!A36:H36"
    )
    assert clear.kind == "clear"
    assert clear.values == [[""] * 8]
    assert "không xóa dòng" in clear.reason
    assert all("A36:M36" not in change.range_a1 for change in plan.operations)


def test_menu_plan_extends_selector_and_formulas_for_24_products() -> None:
    book = _book()
    baseline = menu_baseline_from_structure(book.menu_structure, MENU_HEADERS)
    plan = plan_menu_migration(
        book.values[TAB_MENU],
        baseline,
        _source_rows(),
        _manual_rows(),
    )
    assert plan.selector_new == f"B8:B{MENU_EXTENDED_LAST_ROW}"
    validation = next(
        change for change in plan.operations if change.kind == "validation"
    )
    assert validation.validation_values == ["=MENU!$B$8:$B$35"]
    assert plan.target_row <= MENU_EXTENDED_LAST_ROW
    assert "B8:H35" in plan.formulas_new["J39"]
    assert "B8:C35" in plan.formulas_new["J40"]
    assert "BAO_GIA_TONG_HOP!B2:E26" in plan.formulas_new["J40"]
    assert "C2:E24" not in plan.formulas_new["J40"]
    assert plan.product_count_after == 24
    assert plan.unique_ids_after == 24
    assert plan.product_count_after == plan.unique_ids_after


def test_menu_plan_does_not_touch_policy_columns_except_allowed_cells() -> None:
    book = _book()
    baseline = menu_baseline_from_structure(book.menu_structure, MENU_HEADERS)
    plan = plan_menu_migration(
        book.values[TAB_MENU],
        baseline,
        _source_rows(),
        _manual_rows(),
    )
    allowed = {"K38", "J39", "J40"}
    for change in plan.operations:
        cell = change.range_a1.split("!", 1)[1]
        if cell in allowed:
            continue
        assert re.match(r"^[A-H](31|36)(:[A-H](31|36))?$", cell), cell


def test_guard_rejects_outside_allowlist_and_layout_changes() -> None:
    guard = MigrationGuard({f"'{TAB_MENU}'!A31:H31", f"'{TAB_MENU}'!K38"})
    with pytest.raises(MigrationError):
        guard.check(
            PlannedChange(
                kind="values",
                tab=TAB_MENU,
                range_a1=f"'{TAB_MENU}'!A30:H30",
                values=[["x"]],
            )
        )
    with pytest.raises(MigrationError):
        guard.check(
            PlannedChange(
                kind="format",
                tab=TAB_MENU,
                range_a1=f"'{TAB_MENU}'!A31:H31",
            )
        )
    with pytest.raises(MigrationError):
        guard.check(
            PlannedChange(
                kind="validation",
                tab=TAB_MENU,
                range_a1=f"'{TAB_MENU}'!J39",
                validation_type="ONE_OF_RANGE",
                validation_values=["=MENU!$B$8:$B$35"],
            )
        )


def test_migration_is_idempotent_after_apply_simulation() -> None:
    book = _book()
    first = run_migration(book, "sheet-id", _source_rows())
    assert first.status == "PLANNED"
    assert first.operations
    _apply(book, first.operations)
    second = run_migration(book, "sheet-id", _source_rows())
    assert second.status == "UP_TO_DATE"
    assert second.operations == []


def test_backup_ready_has_checksums_and_restore_plan(tmp_path: Path) -> None:
    book = _book()
    first = run_migration(book, "sheet-id", _source_rows(), backup_root=tmp_path)
    assert first.backup is not None
    assert first.backup.status == "READY"
    folder = Path(first.backup.directory)
    assert (folder / "manifest.json").is_file()
    assert (folder / "snapshot.json").is_file()
    assert (folder / "structures.json").is_file()
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "READY"
    assert manifest["reused_snapshot"] is False
    assert manifest["complete_fidelity"] is False
    assert manifest["restore_plan"]
    assert set(first.backup.checksums) == {"snapshot", "structures"}
    for change in first.operations:
        assert any(change.range_a1 in item for item in first.backup.restore_plan)


def test_backup_blocks_when_required_tab_missing(tmp_path: Path) -> None:
    book = MigrationBook(
        [TAB_MENU, TAB_MANUAL, TAB_AUTO],
        {
            TAB_MENU: _live_grid(),
            TAB_MANUAL: [list(QUOTE_HEADERS)],
            TAB_AUTO: [[*QUOTE_HEADERS, "STOCK"]],
        },
    )
    readiness = create_migration_backup(book, "sheet-id", root=tmp_path)
    assert readiness.status == "BLOCKED"
    assert any("thiếu tab" in item.casefold() for item in readiness.blockers)


def test_menu_plan_accepts_one_unique_duration_rename() -> None:
    book = _book()
    rows = [dict(row) for row in _source_rows()]
    capcut = next(row for row in rows if row["PRODUCT"].startswith("Capcut Pro 30d"))
    capcut["PRODUCT"] = "Capcut Pro 24d full Date - BHF"
    capcut["PRICE"] = 38000
    baseline = menu_baseline_from_structure(book.menu_structure, MENU_HEADERS)
    plan = plan_menu_migration(
        book.values[TAB_MENU],
        baseline,
        rows,
        _manual_rows(),
    )
    assert plan.product_count_after == 24
    assert plan.unique_ids_after == 24
    assert plan.product_id == "local_41"


def test_menu_plan_blocks_unrelated_rename() -> None:
    book = _book()
    rows = [dict(row) for row in _source_rows()]
    capcut = next(row for row in rows if row["PRODUCT"].startswith("Capcut Pro 30d"))
    capcut["PRODUCT"] = "Windows 11 Pro retail key"
    capcut["PRICE"] = 38000
    baseline = menu_baseline_from_structure(book.menu_structure, MENU_HEADERS)
    with pytest.raises(MigrationBlocked, match="Capcut Pro 30d"):
        plan_menu_migration(
            book.values[TAB_MENU],
            baseline,
            rows,
            _manual_rows(),
        )


def test_menu_plan_blocks_when_target_row_is_not_empty() -> None:
    book = _book()
    book.values[TAB_MENU][MENU_TARGET_ROW - 1] = [
        "SXN-99",
        "Sản phẩm khác",
        "Mô tả",
        "1 Tháng",
        "BHF",
        1,
        1,
        "IN STOCK",
    ]
    baseline = menu_baseline_from_structure(book.menu_structure, MENU_HEADERS)
    with pytest.raises(MigrationBlocked):
        plan_menu_migration(
            book.values[TAB_MENU],
            baseline,
            _source_rows(),
            _manual_rows(),
        )


def test_operations_never_insert_or_delete_rows() -> None:
    book = _book()
    result = run_migration(book, "sheet-id", _source_rows())
    assert {change.kind for change in result.operations} <= {
        "values",
        "clear",
        "validation",
    }
    assert all(
        "insert" not in change.kind and "delete" not in change.kind
        for change in result.operations
    )
    assert all(
        "insert" not in change.reason.casefold()
        and "delete" not in change.reason.casefold()
        for change in result.operations
    )


def test_no_duplicate_ids_in_source_target_grids() -> None:
    plans = plan_source_repairs(_breaked_source_grids(), _source_rows(), _manual_rows())
    for plan in plans:
        write = next(
            change for change in plan.operations if change.kind == "values"
        )
        ids = [str(row[0]) for row in write.values[1:]]
        assert ids == [str(index) for index in range(1, len(ids) + 1)]
