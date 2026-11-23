"""Đối chiếu nguồn trên tab riêng. Không ghi SAN_PHAM, BIEN_THE hay Trang tính1.

CAN_GHEP_SAN_PHAM giữ Review Status và Approved Canonical Product ID do người dùng nhập.
Một nhà cung cấp lỗi thì dữ liệu cũ của nhà cung cấp đó được giữ, không ghi tồn kho 0.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from shop_integration.api_client import ShopApiClient
from shop_integration.config import Settings
from shop_integration.backup_service import live_sync_block_reason
from shop_integration.errors import ConfigError, IntegrationError, PartialSyncError
from shop_integration.logging_utils import redact
from shop_integration.pricing import PricingPolicy, policy_from_settings, quote_cost
from shop_integration.product_service import ABSENT_LABEL, MISSING_TEXT, NULL_LABEL
from shop_integration.sheet_layout import (
    ALL_TABS,
    SheetTable,
    grid_from_records,
    records_from_grid,
    stamp_stt,
)
from shop_integration.suppliers.matching import compare_catalog, group_exact_matches, signature_of
from shop_integration.suppliers.models import (
    DIFFERENT_PRODUCT,
    EXACT_MATCH,
    NASTELE_CATALOG_EMPTY,
    REVIEW_REQUIRED,
    SUPPLIER_NASTELE,
    SUPPLIER_SHOP,
    ApprovedMatch,
    MatchDecision,
    NormalizedOffer,
    ProviderResult,
    SourceChoice,
)
from shop_integration.suppliers.nastele_client import NasteleClient
from shop_integration.suppliers.nastele_sheet import snapshot_nastele_values
from shop_integration.suppliers.providers import NasteleProvider, ShopXoaiNonProvider, safe_fetch
from shop_integration.suppliers.selection import eligible_unit_cost, select_group
from shop_integration.text_normalize import FORMULA_PREFIX, guard_formula

TAB_COMPARE = "SO_SANH_NGUON"
TAB_CUSTOMER = "BANG_GIA_TOI_UU"
TAB_REVIEW = "CAN_GHEP_SAN_PHAM"
TAB_CATALOG = TAB_COMPARE
TAB_APPROVAL = TAB_REVIEW
SOURCE_TABS = (TAB_COMPARE, TAB_CUSTOMER, TAB_REVIEW)
PROTECTED_TAB = "Trang tính1"
MISSING_THIS_READ = "Không còn trong lần đọc này"
REVIEW_GONE = "Không còn là ứng viên"
VERIFY = "CẦN XÁC MINH"
CUSTOMER_VERIFY = "Cần xác minh"

COMPARE_HEADERS = [
    "Canonical Product ID",
    "Product Name",
    "Shop Xoài Non Product ID",
    "Shop Xoài Non Cost",
    "Shop Xoài Non Stock",
    "Nastele Product ID",
    "Nastele Cost",
    "Nastele Stock",
    "Matching Confidence",
    "Matching Status",
    "Selected Supplier",
    "Selected Cost",
    "Cost Savings",
    "Profit",
    "Suggested Selling Price",
    "Last Checked",
]
CUSTOMER_HEADERS = [
    "STT",
    "Canonical Product ID",
    "Product Name",
    "Product Description",
    "Selling Price",
    "Availability",
    "Updated At",
]
REVIEW_HEADERS = [
    "Supplier A Product",
    "Supplier B Product",
    "Proposed Match",
    "Confidence",
    "Reasons",
    "Conflicting Attributes",
    "Review Status",
    "Approved Canonical Product ID",
]
CATALOG_HEADERS = COMPARE_HEADERS
APPROVAL_HEADERS = REVIEW_HEADERS
_ID_COLUMNS = {
    "Canonical Product ID",
    "Shop Xoài Non Product ID",
    "Nastele Product ID",
    "Supplier A Product",
    "Supplier B Product",
    "Approved Canonical Product ID",
}
_PRESERVE_ON_STALE = (
    "Selected Supplier",
    "Selected Cost",
    "Cost Savings",
    "Profit",
    "Suggested Selling Price",
)
_SHOP_FIELDS = (
    "Shop Xoài Non Product ID",
    "Shop Xoài Non Cost",
    "Shop Xoài Non Stock",
)
_NASTELE_FIELDS = (
    "Nastele Product ID",
    "Nastele Cost",
    "Nastele Stock",
)
_USER_REVIEW_FIELDS = ("Review Status", "Approved Canonical Product ID")


@dataclass
class ComparisonBuild:
    comparison: list[dict[str, Any]]
    customer: list[dict[str, Any]]
    review: list[dict[str, Any]]
    exact_count: int = 0
    review_count: int = 0
    selected_count: int = 0
    only_shop: int = 0
    only_nastele: int = 0
    cheaper_shop: int = 0
    cheaper_nastele: int = 0
    savings: int = 0


@dataclass
class SourcePlan:
    catalog_records: list[dict[str, Any]]
    review_records: list[dict[str, Any]]
    customer_records: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    exact_count: int = 0
    review_count: int = 0
    selected_count: int = 0
    only_shop: int = 0
    only_nastele: int = 0
    cheaper_shop: int = 0
    cheaper_nastele: int = 0
    savings: int = 0
    sheets_written: bool = False
    tabs_to_create: list[str] = field(default_factory=list)
    nastele_status: str = ""
    text: str = ""


def build_source_plan(
    offers: list[NormalizedOffer],
    approvals: list[ApprovedMatch],
    *,
    quantity: int,
    preferred_supplier_id: str,
    policy: PricingPolicy | None = None,
    updated_at: str,
) -> ComparisonBuild:
    rules = policy or PricingPolicy()
    decisions = compare_catalog(offers, approvals)
    groups = group_exact_matches(offers, decisions)
    by_id = {offer.composite_id: offer for offer in offers}
    review_ids = {
        side
        for decision in decisions
        if decision.kind == REVIEW_REQUIRED
        for side in (decision.left_id, decision.right_id)
    }
    review_score = {
        side: decision.score
        for decision in decisions
        if decision.kind == REVIEW_REQUIRED
        for side in (decision.left_id, decision.right_id)
    }
    comparison: list[dict[str, Any]] = []
    seen_canonical: dict[str, dict[str, Any]] = {}
    selected_count = 0
    for group in groups:
        row = _comparison_row(
            group,
            approvals=approvals,
            quantity=quantity,
            preferred_supplier_id=preferred_supplier_id,
            policy=rules,
            updated_at=updated_at,
            review_ids=review_ids,
            review_score=review_score,
        )
        canonical = str(row["Canonical Product ID"])
        if canonical in seen_canonical:
            row["Canonical Product ID"] = make_canonical(
                [canonical, str(row.get("Shop Xoài Non Product ID")), str(row.get("Nastele Product ID")), "conflict"]
            )
            row["Matching Status"] = VERIFY
            row["Availability"] = CUSTOMER_VERIFY
            seen_canonical[canonical]["Matching Status"] = VERIFY
            seen_canonical[canonical]["Availability"] = CUSTOMER_VERIFY
        seen_canonical[str(row["Canonical Product ID"])] = row
        if str(row.get("Selected Supplier") or "").strip():
            selected_count += 1
        comparison.append(row)
    review = [
        _review_row(decision, by_id)
        for decision in decisions
        if decision.kind == REVIEW_REQUIRED
    ]
    customer = customer_rows_from_comparison(comparison, [], updated_at)
    only_shop, only_nastele, cheaper_shop, cheaper_nastele, savings = _price_counts(comparison)
    return ComparisonBuild(
        comparison=comparison,
        customer=customer,
        review=review,
        exact_count=sum(1 for decision in decisions if decision.kind == EXACT_MATCH),
        review_count=len(review),
        selected_count=selected_count,
        only_shop=only_shop,
        only_nastele=only_nastele,
        cheaper_shop=cheaper_shop,
        cheaper_nastele=cheaper_nastele,
        savings=savings,
    )


def make_canonical(parts: list[str]) -> str:
    payload = "\n".join(sorted(part for part in parts if part))
    return "cxn_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def customer_content_id(rows: list[dict[str, Any]]) -> str:
    lines = []
    for row in rows:
        lines.append(
            "|".join(
                str(row.get(field) or "")
                for field in (
                    "Canonical Product ID",
                    "Product Name",
                    "Product Description",
                    "Selling Price",
                    "Availability",
                )
            )
        )
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]


def render_customer_document(
    rows: list[dict[str, Any]],
    *,
    updated_at: str,
    content_id: str,
) -> str:
    """Báo giá cho khách. STT thay cho mã sản phẩm. Không có giá vốn, lợi nhuận hay khóa API."""

    lines = [
        "BÁO GIÁ",
        f"Mã nội dung: {content_id}",
        f"Cập nhật: {updated_at}",
        "",
    ]
    for index, row in enumerate(rows, start=1):
        price = row.get("Selling Price")
        price_line = f"Giá bán: {price} VND" if price not in ("", None) else "Giá bán: Chưa có dữ liệu"
        lines.extend(
            [
                f"{index}. {row.get('Product Name') or MISSING_TEXT}",
                str(row.get("Product Description") or MISSING_TEXT),
                price_line,
                f"Tình trạng: {row.get('Availability') or MISSING_TEXT}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def customer_rows_from_comparison(
    comparison: list[dict[str, Any]],
    previous: list[dict[str, Any]],
    updated_at: str,
) -> list[dict[str, Any]]:
    previous_by = {_cell_id(row.get("Canonical Product ID")): row for row in previous}
    rows: list[dict[str, Any]] = []
    for item in comparison:
        canonical = _cell_id(item.get("Canonical Product ID"))
        old = previous_by.get(canonical, {})
        selling = item.get("Suggested Selling Price", "")
        status = str(item.get("Matching Status") or "")
        if status in {VERIFY, NASTELE_CATALOG_EMPTY} and selling in ("", None):
            selling = old.get("Selling Price", "")
        if status in {VERIFY, NASTELE_CATALOG_EMPTY}:
            availability = CUSTOMER_VERIFY
        else:
            availability = item.get("Availability") or old.get("Availability") or MISSING_TEXT
        rows.append(
            {
                "Canonical Product ID": canonical,
                "Product Name": item.get("Product Name") or old.get("Product Name") or MISSING_TEXT,
                "Product Description": item.get("Product Description") or old.get("Product Description") or MISSING_TEXT,
                "Selling Price": "" if selling is None else selling,
                "Availability": availability,
                "Updated At": updated_at,
            }
        )
    return rows


def apply_stale_guard(
    fresh: list[dict[str, Any]],
    existing: list[dict[str, Any]],
    failed_suppliers: set[str],
    empty_catalog_suppliers: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Nguồn lỗi hoặc catalog rỗng bất thường không được dùng để chọn lại hay ghi tồn kho 0."""

    empty_catalog_suppliers = set(empty_catalog_suppliers or ())
    preserve = set(failed_suppliers) | empty_catalog_suppliers
    if not preserve:
        return [dict(row) for row in fresh]
    blocked: set[int] = set()
    hit_counts: dict[int, int] = {}
    hits_of: list[list[int]] = []
    for old in existing:
        hits = [index for index, row in enumerate(fresh) if _rows_match(old, row)]
        hits_of.append(hits)
        for index in hits:
            hit_counts[index] = hit_counts.get(index, 0) + 1
    output: list[dict[str, Any]] = []
    consumed: set[int] = set()
    for old, hits in zip(existing, hits_of):
        owners = _row_suppliers(old)
        if owners and not (owners & preserve):
            continue
        status = _preservation_status(owners, failed_suppliers, empty_catalog_suppliers)
        if len(hits) != 1 or hit_counts.get(hits[0], 0) != 1:
            kept = dict(old)
            kept["Matching Status"] = status
            kept["Availability"] = CUSTOMER_VERIFY
            output.append(kept)
            blocked.update(hits)
            continue
        match = dict(fresh[hits[0]])
        consumed.add(hits[0])
        merged = dict(match)
        merged["Canonical Product ID"] = old.get("Canonical Product ID") or merged.get("Canonical Product ID")
        if SUPPLIER_SHOP in failed_suppliers:
            _restore_side(merged, old, _SHOP_FIELDS)
        if SUPPLIER_NASTELE in failed_suppliers or SUPPLIER_NASTELE in empty_catalog_suppliers:
            _restore_side(merged, old, _NASTELE_FIELDS)
        for column in _PRESERVE_ON_STALE:
            merged[column] = old.get(column, "")
        merged["Matching Status"] = status
        merged["Availability"] = CUSTOMER_VERIFY
        for key, value in old.items():
            if key not in merged:
                merged[key] = value
        output.append(merged)
    for index, row in enumerate(fresh):
        if index in consumed or index in blocked:
            continue
        stamped = dict(row)
        if failed_suppliers:
            stamped["Matching Status"] = VERIFY
            stamped["Availability"] = CUSTOMER_VERIFY
        output.append(stamped)
    return output


def _preservation_status(owners: set[str], failed_suppliers: set[str], empty_catalog_suppliers: set[str]) -> str:
    if not owners or owners & failed_suppliers:
        return VERIFY
    if owners & empty_catalog_suppliers:
        return NASTELE_CATALOG_EMPTY
    return VERIFY


def align_canonical_ids(
    rows: list[dict[str, Any]],
    existing: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    index: dict[str, list[str]] = {}
    for old in existing:
        canonical = _cell_id(old.get("Canonical Product ID"))
        if not canonical:
            continue
        for field_name in ("Shop Xoài Non Product ID", "Nastele Product ID"):
            for token in _split_ids(old.get(field_name)):
                index.setdefault(token, []).append(canonical)
    aligned: list[dict[str, Any]] = []
    for row in rows:
        copied = dict(row)
        if copied.get("_pinned"):
            aligned.append(copied)
            continue
        found: list[str] = []
        for field_name in ("Shop Xoài Non Product ID", "Nastele Product ID"):
            for token in _split_ids(copied.get(field_name)):
                found.extend(index.get(token, []))
        unique = list(dict.fromkeys(found))
        if len(unique) == 1:
            copied["Canonical Product ID"] = unique[0]
        elif len(unique) > 1:
            copied["Matching Status"] = VERIFY
            copied["Availability"] = CUSTOMER_VERIFY
        aligned.append(copied)
    return aligned


def parse_approvals(table: SheetTable) -> tuple[list[ApprovedMatch], list[str]]:
    matches: list[ApprovedMatch] = []
    warnings: list[str] = []
    for record in table.records:
        left = composite_from_cell(record.get("Supplier A Product"))
        right = composite_from_cell(record.get("Supplier B Product"))
        decision = str(record.get("Review Status") or "").strip()
        canonical = _cell_id(record.get("Approved Canonical Product ID"))
        if not left and not right and not decision and not canonical:
            continue
        if decision not in {EXACT_MATCH, DIFFERENT_PRODUCT}:
            continue
        if not left or not right or left == right:
            warnings.append("Bỏ qua một dòng duyệt vì thiếu khóa nguồn.")
            continue
        matches.append(ApprovedMatch(left, right, decision, "", canonical))
    return matches, warnings


def composite_from_cell(value: object) -> str:
    text = _cell_id(value)
    head = text.split(" — ", 1)[0].strip()
    if head.startswith("shop_xoai_non|") or head.startswith("nastele|"):
        return head
    return ""


def failed_supplier_ids(*results: ProviderResult) -> set[str]:
    return {result.supplier_id for result in results if result.error or result.blocked}


class SourceSheetWriter:
    def __init__(
        self,
        gateway: Any,
        spreadsheet_id: str,
        *,
        dry_run: bool,
        secrets: list[str] | None = None,
    ) -> None:
        if not spreadsheet_id.strip():
            raise ConfigError("Thiếu GOOGLE_SPREADSHEET_ID.")
        self.gateway = gateway
        self.spreadsheet_id = spreadsheet_id.strip()
        self.dry_run = dry_run
        self._secrets = [secret for secret in (secrets or []) if len(secret) >= 8]
        self.tabs_to_create: list[str] = []
        self.last_comparison: list[dict[str, Any]] = []
        self.last_customer: list[dict[str, Any]] = []

    def read_approvals(self) -> tuple[list[ApprovedMatch], list[str]]:
        titles = set(self.gateway.list_sheet_titles(self.spreadsheet_id))
        if TAB_REVIEW not in titles:
            return [], []
        grid = self.gateway.get_values(self.spreadsheet_id, f"'{TAB_REVIEW}'!A:ZZ")
        return parse_approvals(records_from_grid(grid))

    def read_records(self, tab: str) -> list[dict[str, Any]]:
        return self._read(tab).records

    def prepare(self) -> list[str]:
        for title in SOURCE_TABS:
            self._ensure(title)
        return list(self.tabs_to_create)

    def apply(
        self,
        comparison: list[dict[str, Any]],
        review: list[dict[str, Any]],
        *,
        failed_suppliers: set[str],
        updated_at: str,
        customer: list[dict[str, Any]] | None = None,
        restabilize: bool = True,
        empty_catalog_suppliers: set[str] | None = None,
    ) -> dict[str, int]:
        empty_catalog_suppliers = set(empty_catalog_suppliers or ())
        existing_compare = self._read(TAB_COMPARE)
        existing_customer = self._read(TAB_CUSTOMER)
        existing_review = self._read(TAB_REVIEW)
        rows = list(comparison)
        if restabilize:
            rows = align_canonical_ids(rows, existing_compare.records)
            rows = apply_stale_guard(
                rows,
                existing_compare.records,
                failed_suppliers,
                empty_catalog_suppliers,
            )
        customer_rows = customer_rows_from_comparison(rows, existing_customer.records, updated_at)
        if customer is not None and not restabilize:
            customer_rows = customer
        self.last_comparison = rows
        self.last_customer = customer_rows
        self.prepare()
        if self.dry_run:
            return {"comparison": 0, "customer": 0, "review": 0}
        compare_grid = _merge_compare(
            existing_compare,
            rows,
            failed_suppliers,
            updated_at,
            empty_catalog_suppliers,
        )
        customer_grid = _merge_customer(existing_customer, customer_rows, updated_at)
        review_grid = _merge_review(existing_review, review, failed_suppliers)
        self._write(TAB_COMPARE, compare_grid)
        self._write(TAB_CUSTOMER, customer_grid)
        self._write(TAB_REVIEW, review_grid)
        self._format_new_tabs()
        return {
            "comparison": len(records_from_grid(compare_grid).records),
            "customer": len(records_from_grid(customer_grid).records),
            "review": len(records_from_grid(review_grid).records),
        }

    def _read(self, tab: str) -> SheetTable:
        assert_source_tab(tab)
        titles = set(self.gateway.list_sheet_titles(self.spreadsheet_id))
        if tab not in titles:
            return SheetTable()
        return records_from_grid(self.gateway.get_values(self.spreadsheet_id, f"'{tab}'!A:ZZ"))

    def _ensure(self, title: str) -> None:
        assert_source_tab(title)
        titles = set(self.gateway.list_sheet_titles(self.spreadsheet_id))
        if title in titles:
            return
        if self.dry_run:
            if title not in self.tabs_to_create:
                self.tabs_to_create.append(title)
            return
        self.gateway.add_sheet(self.spreadsheet_id, title)

    def _write(self, tab: str, grid: list[list[Any]]) -> None:
        assert_source_tab(tab)
        if self.dry_run:
            return
        self.gateway.update_values(
            self.spreadsheet_id,
            f"'{tab}'!A1",
            _guard_grid(grid, self._secrets),
        )

    def _format_new_tabs(self) -> None:
        if self.dry_run:
            return
        meta_fn = getattr(self.gateway, "list_sheet_meta", None)
        update_fn = getattr(self.gateway, "batch_update", None)
        if meta_fn is None or update_fn is None:
            return
        from shop_integration.suppliers.nastele_sheet import build_format_requests

        meta = {item["title"]: item for item in meta_fn(self.spreadsheet_id)}
        header_of = {
            TAB_COMPARE: COMPARE_HEADERS,
            TAB_CUSTOMER: CUSTOMER_HEADERS,
            TAB_REVIEW: REVIEW_HEADERS,
        }
        requests: list[dict[str, Any]] = []
        for title, headers in header_of.items():
            info = meta.get(title)
            if not info:
                continue
            table = self._read(title)
            requests.extend(
                build_format_requests(
                    info["sheetId"],
                    headers,
                    max(table.row_count, 1),
                    banding_ids=list(info.get("bandingIds") or []),
                    conditional_format_count=int(info.get("conditionalFormatCount") or 0),
                )
            )
        if requests:
            update_fn(self.spreadsheet_id, requests)


def assert_source_tab(tab: str) -> None:
    if tab == PROTECTED_TAB or tab in ALL_TABS or tab not in SOURCE_TABS:
        raise ConfigError(f"Từ chối ghi tab '{tab}'. Tab nguồn không được đè danh mục Shop Xoài Non.")


class SourceSyncService:
    def __init__(
        self,
        settings: Settings,
        shop: ProviderResult,
        nastele: ProviderResult,
        *,
        writer: SourceSheetWriter | None = None,
        nastele_writer: Any = None,
        docs: Any = None,
        backup: Any = None,
        updated_at: str,
        approvals: list[ApprovedMatch] | None = None,
        approval_warnings: list[str] | None = None,
    ) -> None:
        self.settings = settings
        self.shop = shop
        self.nastele = nastele
        self.writer = writer
        self.nastele_writer = nastele_writer
        self.docs = docs
        self.backup = backup
        self.updated_at = updated_at
        self.approvals = list(approvals or [])
        self.approval_warnings = list(approval_warnings or [])

    def run(self, *, write_sheets: bool) -> SourcePlan:
        offers = _unique_offers([*self.shop.offers, *self.nastele.offers])
        built = build_source_plan(
            offers,
            self.approvals,
            quantity=self.settings.comparison_quantity,
            preferred_supplier_id=self.settings.preferred_supplier_id,
            policy=policy_from_settings(self.settings),
            updated_at=self.updated_at,
        )
        warnings = [*self.shop.warnings, *self.nastele.warnings, *self.approval_warnings]
        failed = failed_supplier_ids(self.shop, self.nastele)
        empty_catalogs = empty_catalog_supplier_ids(self.shop, self.nastele)
        if self.shop.error:
            warnings.append(f"Shop Xoài Non: {self.shop.error}")
        if self.nastele.error:
            warnings.append(f"Nastele: {self.nastele.error}")
        if self.nastele.catalog_status == NASTELE_CATALOG_EMPTY:
            warnings.append(
                "NASTELE_CATALOG_EMPTY. Không tạo sản phẩm giả, không kết luận Nastele rẻ hơn, không xóa dữ liệu cũ."
            )
        if failed:
            warnings.append(
                "Dữ liệu một nhà cung cấp đang cũ hoặc thiếu. Khuyến nghị cần xác minh. Không ghi tồn kho 0."
            )
        plan = SourcePlan(
            catalog_records=built.comparison,
            review_records=built.review,
            customer_records=built.customer,
            warnings=warnings,
            exact_count=built.exact_count,
            review_count=built.review_count,
            selected_count=built.selected_count,
            only_shop=built.only_shop,
            only_nastele=built.only_nastele,
            cheaper_shop=built.cheaper_shop,
            cheaper_nastele=built.cheaper_nastele,
            savings=built.savings,
            nastele_status=_nastele_sheet_status(self.settings, self.nastele, writing=False),
        )
        plan.text = render_source_report(plan, self.shop, self.nastele, self.settings)
        if not write_sheets:
            return plan
        if self.writer is None:
            if self.settings.dry_run:
                plan.warnings.append("Không có spreadsheet để ghi tab nguồn.")
                plan.text = render_source_report(plan, self.shop, self.nastele, self.settings)
                return plan
            raise ConfigError("Thiếu GOOGLE_SPREADSHEET_ID.")
        if self.settings.dry_run:
            self.writer.dry_run = True
            self.writer.apply(
                built.comparison,
                built.review,
                failed_suppliers=failed,
                updated_at=self.updated_at,
                empty_catalog_suppliers=empty_catalogs,
            )
            plan.tabs_to_create = list(self.writer.tabs_to_create)
            plan.catalog_records = self.writer.last_comparison or plan.catalog_records
            plan.customer_records = self.writer.last_customer or plan.customer_records
            _refresh_counts(plan)
            plan.sheets_written = False
            self._run_nastele_sheet(failed, writing=False)
            plan.nastele_status = _nastele_sheet_status(self.settings, self.nastele, writing=False)
            plan.text = render_source_report(plan, self.shop, self.nastele, self.settings)
            return plan
        if self.backup is None:
            raise ConfigError("Thiếu sao lưu trước khi ghi tab nguồn.")
        blocked = live_sync_block_reason()
        if blocked:
            raise ConfigError(blocked)
        self.backup.create_and_verify()
        self.writer.dry_run = False
        self.writer.apply(
            built.comparison,
            built.review,
            failed_suppliers=failed,
            updated_at=self.updated_at,
            empty_catalog_suppliers=empty_catalogs,
        )
        plan.catalog_records = self.writer.last_comparison
        plan.customer_records = self.writer.last_customer
        _refresh_counts(plan)
        plan.sheets_written = True
        self._run_nastele_sheet(failed, writing=True)
        self._run_customer_doc(plan)
        plan.nastele_status = _nastele_sheet_status(self.settings, self.nastele, writing=True)
        plan.text = render_source_report(plan, self.shop, self.nastele, self.settings)
        return plan

    def _run_nastele_sheet(self, failed: set[str], *, writing: bool) -> None:
        if self.nastele_writer is None:
            return
        nastele_failed = SUPPLIER_NASTELE in failed
        nastele_empty = self.nastele.catalog_status == NASTELE_CATALOG_EMPTY
        if writing and not self.settings.dry_run:
            try:
                snapshot_nastele_values(
                    self.nastele_writer.gateway,
                    self.nastele_writer.spreadsheet_id,
                    self.settings.secret_values(),
                )
            except IntegrationError:
                raise PartialSyncError(
                    "Đã ghi bảng so sánh nhưng chưa sao lưu được spreadsheet Nastele. Không ghi đè dữ liệu Nastele."
                ) from None
        self.nastele_writer.dry_run = not writing or self.settings.dry_run
        if nastele_empty and not self.nastele.error:
            failure_message = (
                "NASTELE_CATALOG_EMPTY. Giữ dữ liệu Nastele cũ. Danh mục rỗng không phải bằng chứng đã xóa."
            )
        else:
            failure_message = self.nastele.error or "Giữ dữ liệu Nastele cũ."
        self.nastele_writer.apply(
            self.nastele.offers,
            updated_at=self.updated_at,
            config_pairs=nastele_config_pairs(self.settings),
            failed=nastele_failed or nastele_empty,
            failure_message=failure_message,
        )

    def _run_customer_doc(self, plan: SourcePlan) -> None:
        if self.docs is None or self.settings.dry_run:
            return
        content_id = customer_content_id(plan.customer_records)
        text = redact(
            render_customer_document(
                plan.customer_records,
                updated_at=self.updated_at,
                content_id=content_id,
            ),
            self.settings.secret_values(),
        )
        try:
            self.docs.replace_customer_prices(text, content_id)
        except IntegrationError:
            raise PartialSyncError(
                "Đã ghi bảng so sánh nhưng chưa ghi xong Google Docs. Không tự khôi phục."
            ) from None


def nastele_config_pairs(settings: Settings) -> list[tuple[str, str]]:
    configured = "có" if settings.nastele_api_key.get_secret_value().strip() else "không"
    return [
        ("comparison_quantity", str(settings.comparison_quantity)),
        ("preferred_supplier", settings.preferred_supplier_id),
        ("nastele_rate_limit_per_minute", str(settings.nastele_rate_limit_per_minute)),
        ("api_key_configured", configured),
        ("nastele_base_url", settings.nastele_base_url),
    ]


def render_source_report(
    plan: SourcePlan,
    shop: ProviderResult,
    nastele: ProviderResult,
    settings: Settings,
) -> str:
    lines = [
        "Đối chiếu nhiều nhà cung cấp. Không tạo đơn.",
        f"Số lượng so sánh: {settings.comparison_quantity}",
        f"Nhà cung cấp ưu tiên khi hòa giá: {settings.preferred_supplier_id}",
        _supplier_line("Shop Xoài Non", shop),
        _supplier_line("Nastele", nastele),
        f"EXACT_MATCH: {plan.exact_count}",
        f"REVIEW_REQUIRED: {plan.review_count}",
        f"Sản phẩm đã chọn nguồn: {plan.selected_count}",
        f"Chỉ có ở Shop Xoài Non: {plan.only_shop}",
        f"Chỉ có ở Nastele: {plan.only_nastele}",
        f"Rẻ hơn ở Shop Xoài Non: {plan.cheaper_shop}",
        f"Rẻ hơn ở Nastele: {plan.cheaper_nastele}",
        f"Chênh lệch giá đã cộng: {plan.savings}",
        f"Dòng so sánh: {len(plan.catalog_records)}",
    ]
    if not settings.nastele_spreadsheet_id.strip():
        lines.append("BLOCKED Google Sheets 2: thiếu NASTELE_SPREADSHEET_ID.")
    elif plan.nastele_status:
        lines.append(f"Google Sheets 2: {plan.nastele_status}")
    if plan.sheets_written:
        lines.append("Đã ghi SO_SANH_NGUON, BANG_GIA_TOI_UU và CAN_GHEP_SAN_PHAM.")
    elif settings.dry_run:
        lines.append("Dry-run: không ghi ô nào.")
    if plan.tabs_to_create:
        lines.append("Tab sẽ tạo: " + ", ".join(plan.tabs_to_create))
    for warning in plan.warnings[:40]:
        lines.append(f"Cảnh báo: {warning}")
    return redact("\n".join(lines), settings.secret_values())


def _nastele_sheet_status(settings: Settings, nastele: ProviderResult, *, writing: bool) -> str:
    if nastele.catalog_status == NASTELE_CATALOG_EMPTY:
        return NASTELE_CATALOG_EMPTY
    if not settings.nastele_spreadsheet_id.strip():
        return "BLOCKED"
    if nastele.blocked or nastele.error:
        return "PRESERVED" if writing and not settings.dry_run else "BLOCKED" if nastele.blocked else "PRESERVED"
    if settings.dry_run or not writing:
        return "DRY_RUN"
    return "PASS"


def _supplier_line(name: str, result: ProviderResult) -> str:
    if result.catalog_status == NASTELE_CATALOG_EMPTY and not result.error and not result.blocked:
        return f"{name}: 0 nguồn. Trạng thái: {NASTELE_CATALOG_EMPTY}"
    if result.blocked:
        return f"BLOCKED {name}: {result.error or 'thiếu dữ liệu'}"
    if result.error:
        return f"LỖI {name}: {result.error}"
    return f"{name}: {len(result.offers)} nguồn"


def empty_catalog_supplier_ids(*results: ProviderResult) -> set[str]:
    return {result.supplier_id for result in results if result.catalog_status == NASTELE_CATALOG_EMPTY}


def _refresh_counts(plan: SourcePlan) -> None:
    only_shop, only_nastele, cheaper_shop, cheaper_nastele, savings = _price_counts(plan.catalog_records)
    plan.only_shop = only_shop
    plan.only_nastele = only_nastele
    plan.cheaper_shop = cheaper_shop
    plan.cheaper_nastele = cheaper_nastele
    plan.savings = savings
    plan.selected_count = sum(
        1 for row in plan.catalog_records if str(row.get("Selected Supplier") or "").strip()
    )


def _unique_offers(offers: list[NormalizedOffer]) -> list[NormalizedOffer]:
    seen: set[str] = set()
    unique: list[NormalizedOffer] = []
    for offer in offers:
        if offer.composite_id in seen:
            continue
        seen.add(offer.composite_id)
        unique.append(offer)
    return unique


def _comparison_row(
    group: list[NormalizedOffer],
    *,
    approvals: list[ApprovedMatch],
    quantity: int,
    preferred_supplier_id: str,
    policy: PricingPolicy,
    updated_at: str,
    review_ids: set[str],
    review_score: dict[str, str],
) -> dict[str, Any]:
    choice = select_group(
        group,
        quantity=quantity,
        preferred_supplier_id=preferred_supplier_id,
        policy=policy,
    )
    multi = len(group) > 1
    incomplete = any(offer.available is None or offer.cost_price_vnd is None for offer in group)
    approved_id, approved_conflict = _approved_canonical(group, approvals)
    if multi:
        status = EXACT_MATCH
        confidence = "1"
    elif any(offer.composite_id in review_ids for offer in group):
        status = "chờ đối chiếu"
        confidence = next(
            (review_score.get(offer.composite_id, "") for offer in group if offer.composite_id in review_ids),
            "",
        )
    else:
        status = "KHÔNG CÓ CẶP"
        confidence = "1"
    if incomplete or approved_conflict:
        status = VERIFY
    canonical = approved_id or make_canonical([offer.composite_id for offer in group])
    shop_offers = [offer for offer in group if offer.supplier_id == SUPPLIER_SHOP]
    nastele_offers = [offer for offer in group if offer.supplier_id == SUPPLIER_NASTELE]
    quote = quote_cost(choice.selected_cost, policy) if choice.selected_cost is not None else None
    savings: Any = choice.cost_difference if choice.cost_difference is not None else ""
    return {
        "Canonical Product ID": canonical,
        "Product Name": _group_name(group, choice),
        "Shop Xoài Non Product ID": _join_product_ids(shop_offers),
        "Shop Xoài Non Cost": _side_cost(shop_offers, quantity),
        "Shop Xoài Non Stock": _side_stock(shop_offers),
        "Nastele Product ID": _join_product_ids(nastele_offers),
        "Nastele Cost": _side_cost(nastele_offers, quantity),
        "Nastele Stock": _side_stock(nastele_offers),
        "Matching Confidence": confidence,
        "Matching Status": status,
        "Selected Supplier": choice.selected.supplier_name if choice.selected is not None else "",
        "Selected Cost": choice.selected_cost if choice.selected_cost is not None else "",
        "Cost Savings": savings,
        "Profit": quote.profit if quote is not None and not quote.error else "",
        "Suggested Selling Price": quote.selling if quote is not None and not quote.error else "",
        "Last Checked": updated_at,
        "Product Description": _group_description(group, choice),
        "Availability": _availability_label(group, choice, status),
        "_pinned": bool(approved_id),
    }


def _review_row(decision: MatchDecision, by_id: dict[str, NormalizedOffer]) -> dict[str, Any]:
    left = by_id[decision.left_id]
    right = by_id[decision.right_id]
    first, second = sorted((left, right), key=lambda offer: offer.composite_id)
    return {
        "Supplier A Product": _supplier_product_cell(first),
        "Supplier B Product": _supplier_product_cell(second),
        "Proposed Match": REVIEW_REQUIRED,
        "Confidence": decision.score,
        "Reasons": decision.reason,
        "Conflicting Attributes": _conflicting_attributes(first, second),
    }


def _supplier_product_cell(offer: NormalizedOffer) -> str:
    name = offer.normalized_product_name or MISSING_TEXT
    return f"{offer.composite_id} — {name}"


def _conflicting_attributes(left: NormalizedOffer, right: NormalizedOffer) -> str:
    left_sig = signature_of(left)
    right_sig = signature_of(right)
    parts: list[str] = []
    if left_sig.brand and right_sig.brand and left_sig.brand != right_sig.brand:
        parts.append(f"brand:{left_sig.brand}!={right_sig.brand}")
    for key in ("plan", "duration", "users", "region", "account"):
        left_value = left_sig.fields.get(key)
        right_value = right_sig.fields.get(key)
        if left_value and right_value and left_value != right_value:
            parts.append(f"{key}:{left_value}!={right_value}")
        elif bool(left_value) != bool(right_value) and (left_value or right_value):
            parts.append(f"{key}:chỉ có một bên")
    return "; ".join(parts) or MISSING_TEXT


def _approved_canonical(group: list[NormalizedOffer], approvals: list[ApprovedMatch]) -> tuple[str, bool]:
    ids = {offer.composite_id for offer in group}
    found: list[str] = []
    for approval in approvals:
        if set(approval.pair_key()) <= ids and approval.canonical_id.strip():
            found.append(approval.canonical_id.strip())
    unique = list(dict.fromkeys(found))
    if len(unique) == 1:
        return unique[0], False
    if len(unique) > 1:
        return "", True
    return "", False


def _group_name(group: list[NormalizedOffer], choice: SourceChoice) -> str:
    if choice.selected is not None and choice.selected.normalized_product_name:
        return choice.selected.normalized_product_name
    for offer in group:
        if offer.normalized_product_name:
            return offer.normalized_product_name
    return MISSING_TEXT


def _group_description(group: list[NormalizedOffer], choice: SourceChoice) -> str:
    if choice.selected is not None and choice.selected.description:
        return choice.selected.description
    for offer in group:
        if offer.description:
            return offer.description
    return MISSING_TEXT


def _availability_label(group: list[NormalizedOffer], choice: SourceChoice, status: str) -> str:
    if status == VERIFY:
        return CUSTOMER_VERIFY
    if choice.selected is not None and choice.selected.available is True:
        return "Còn hàng"
    if group and all(offer.available is False for offer in group):
        return "Hết hàng"
    return CUSTOMER_VERIFY


def _join_product_ids(offers: list[NormalizedOffer]) -> str:
    cells: list[str] = []
    for offer in sorted(offers, key=lambda item: item.composite_id):
        if offer.source_variant_id:
            cells.append(f"{offer.source_product_id}|{offer.source_variant_id}")
        else:
            cells.append(offer.source_product_id)
    return "; ".join(cells)


def _side_cost(offers: list[NormalizedOffer], quantity: int) -> Any:
    costs = []
    for offer in offers:
        cost, _tier = eligible_unit_cost(offer, quantity)
        if cost is not None:
            costs.append(cost)
    if not costs:
        return ""
    return min(costs)


def _side_stock(offers: list[NormalizedOffer]) -> Any:
    if not offers:
        return ""
    parts: list[Any] = []
    for offer in sorted(offers, key=lambda item: item.composite_id):
        if offer.quantity_presence == "value" and offer.available_quantity is not None:
            parts.append(offer.available_quantity)
        else:
            parts.append(_stock_label(offer))
    if len(parts) == 1:
        return parts[0]
    return "; ".join(str(part) for part in parts)


def _stock_label(offer: NormalizedOffer) -> str:
    if offer.available is True:
        return "Còn hàng"
    if offer.available is False:
        return "Hết hàng"
    if offer.available_presence == "null":
        return NULL_LABEL
    if offer.available_presence == "invalid":
        return "Không rõ"
    if offer.available_presence == "absent":
        return ABSENT_LABEL
    return "Không rõ"


def _price_counts(rows: list[dict[str, Any]]) -> tuple[int, int, int, int, int]:
    only_shop = 0
    only_nastele = 0
    cheaper_shop = 0
    cheaper_nastele = 0
    savings = 0
    for row in rows:
        if str(row.get("Matching Status") or "") == NASTELE_CATALOG_EMPTY:
            continue
        shop_id = str(row.get("Shop Xoài Non Product ID") or "").strip()
        nastele_id = str(row.get("Nastele Product ID") or "").strip()
        if shop_id and not nastele_id:
            only_shop += 1
        elif nastele_id and not shop_id:
            only_nastele += 1
        difference = row.get("Cost Savings")
        if not isinstance(difference, int) or isinstance(difference, bool) or difference <= 0:
            continue
        savings += difference
        selected = str(row.get("Selected Supplier") or "")
        if selected == "Shop Xoài Non":
            cheaper_shop += 1
        elif selected == "Nastele":
            cheaper_nastele += 1
    return only_shop, only_nastele, cheaper_shop, cheaper_nastele, savings


def _rows_match(old: dict[str, Any], new: dict[str, Any]) -> bool:
    old_canonical = _cell_id(old.get("Canonical Product ID"))
    new_canonical = _cell_id(new.get("Canonical Product ID"))
    if old_canonical and old_canonical == new_canonical:
        return True
    for field_name in ("Shop Xoài Non Product ID", "Nastele Product ID"):
        if _split_ids(old.get(field_name)) & _split_ids(new.get(field_name)):
            return True
    return False


def _row_suppliers(row: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    if _split_ids(row.get("Shop Xoài Non Product ID")):
        found.add(SUPPLIER_SHOP)
    if _split_ids(row.get("Nastele Product ID")):
        found.add(SUPPLIER_NASTELE)
    return found


def _restore_side(merged: dict[str, Any], old: dict[str, Any], fields: tuple[str, ...]) -> None:
    for field_name in fields:
        old_value = old.get(field_name, "")
        new_value = merged.get(field_name, "")
        if field_name.endswith("Stock"):
            merged[field_name] = _keep_stock(old_value, new_value)
        elif old_value not in ("", None):
            merged[field_name] = old_value


def _keep_stock(old: Any, new: Any) -> Any:
    if new in (0, "0", "", None) and old not in (0, "0", "", None):
        return old
    if new in ("", None):
        return old
    return new


def _split_ids(value: object) -> set[str]:
    text = _cell_id(value)
    if not text:
        return set()
    return {part.strip() for part in text.split(";") if part.strip()}


def _cell_id(value: object) -> str:
    text = str(value or "").strip()
    if text.startswith(FORMULA_PREFIX):
        text = text[1:].strip()
    return text


def _merge_compare(
    existing: SheetTable,
    rows: list[dict[str, Any]],
    failed_suppliers: set[str],
    updated_at: str,
    empty_catalog_suppliers: set[str] | None = None,
) -> list[list[Any]]:
    empty_catalog_suppliers = set(empty_catalog_suppliers or ())
    headers = _preferred_headers(existing.headers, COMPARE_HEADERS)
    fresh = {_cell_id(row.get("Canonical Product ID")): row for row in rows if _cell_id(row.get("Canonical Product ID"))}
    covered: set[str] = set()
    for row in rows:
        covered.update(_split_ids(row.get("Shop Xoài Non Product ID")))
        covered.update(_split_ids(row.get("Nastele Product ID")))
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for old in existing.records:
        key = _cell_id(old.get("Canonical Product ID"))
        tokens = _split_ids(old.get("Shop Xoài Non Product ID")) | _split_ids(old.get("Nastele Product ID"))
        if key and key in fresh:
            combined = dict(old)
            for field_name, value in fresh[key].items():
                if field_name.startswith("_"):
                    continue
                combined[field_name] = value
            combined["Last Checked"] = updated_at
            ordered.append(combined)
            seen.add(key)
        elif key and key not in fresh and tokens and tokens <= covered:
            continue
        elif key:
            kept = dict(old)
            owners = _row_suppliers(old)
            if not owners or owners & failed_suppliers:
                kept["Matching Status"] = VERIFY
            elif owners & empty_catalog_suppliers:
                kept["Matching Status"] = NASTELE_CATALOG_EMPTY
                for field_name in _NASTELE_FIELDS:
                    if field_name.endswith("Stock"):
                        kept[field_name] = _keep_stock(old.get(field_name), kept.get(field_name))
            else:
                kept["Matching Status"] = MISSING_THIS_READ
            kept["Last Checked"] = updated_at
            ordered.append(kept)
            seen.add(key)
        else:
            ordered.append(dict(old))
    for row in rows:
        key = _cell_id(row.get("Canonical Product ID"))
        if key and key not in seen:
            ordered.append({key_name: value for key_name, value in row.items() if not str(key_name).startswith("_")})
            seen.add(key)
    return _pad(existing, headers, ordered)


def _merge_customer(
    existing: SheetTable,
    rows: list[dict[str, Any]],
    updated_at: str,
) -> list[list[Any]]:
    headers = _preferred_headers(existing.headers, CUSTOMER_HEADERS)
    fresh = {_cell_id(row.get("Canonical Product ID")): row for row in rows}
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for old in existing.records:
        key = _cell_id(old.get("Canonical Product ID"))
        if key and key in fresh:
            combined = dict(old)
            for field_name, value in fresh[key].items():
                if field_name in {"Selling Price", "Product Name", "Product Description", "Availability", "Updated At"}:
                    combined[field_name] = value
            combined["Updated At"] = updated_at
            ordered.append(combined)
            seen.add(key)
        elif key:
            kept = dict(old)
            kept["Availability"] = MISSING_THIS_READ
            kept["Updated At"] = updated_at
            ordered.append(kept)
            seen.add(key)
    for row in rows:
        key = _cell_id(row.get("Canonical Product ID"))
        if key and key not in seen:
            ordered.append(dict(row))
            seen.add(key)
    return stamp_stt(_pad(existing, headers, ordered))


def _merge_review(
    existing: SheetTable,
    rows: list[dict[str, Any]],
    failed_suppliers: set[str],
) -> list[list[Any]]:
    headers = _preferred_headers(existing.headers, REVIEW_HEADERS)
    fresh = {_review_key(row): row for row in rows if _review_key(row) != ("", "")}
    ordered: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for old in existing.records:
        key = _review_key(old)
        if key != ("", "") and key in fresh:
            combined = dict(old)
            for field_name, value in fresh[key].items():
                if field_name in _USER_REVIEW_FIELDS and str(old.get(field_name) or "").strip():
                    continue
                combined[field_name] = value
            ordered.append(combined)
            seen.add(key)
        elif key != ("", ""):
            kept = dict(old)
            status = str(old.get("Review Status") or "").strip()
            owners = _review_suppliers(old)
            if not owners or owners & failed_suppliers or status:
                ordered.append(kept)
            else:
                kept["Review Status"] = REVIEW_GONE
                ordered.append(kept)
            seen.add(key)
        else:
            ordered.append(dict(old))
    for row in rows:
        key = _review_key(row)
        if key not in seen:
            ordered.append(dict(row))
            seen.add(key)
    return _pad(existing, headers, ordered)


def _review_key(record: dict[str, Any]) -> tuple[str, str]:
    left = composite_from_cell(record.get("Supplier A Product"))
    right = composite_from_cell(record.get("Supplier B Product"))
    if not left or not right:
        return ("", "")
    ordered = tuple(sorted((left, right)))
    return ordered[0], ordered[1]


def _review_suppliers(record: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for field_name in ("Supplier A Product", "Supplier B Product"):
        composite = composite_from_cell(record.get(field_name))
        if composite.startswith("shop_xoai_non|"):
            found.add(SUPPLIER_SHOP)
        elif composite.startswith("nastele|"):
            found.add(SUPPLIER_NASTELE)
    return found


def _preferred_headers(existing: list[str], preferred: list[str]) -> list[str]:
    if not existing:
        return list(preferred)
    headers = list(preferred)
    for header in existing:
        if header and header not in headers:
            headers.append(header)
    return headers


def _pad(existing: SheetTable, headers: list[str], records: list[dict[str, Any]]) -> list[list[Any]]:
    grid = grid_from_records(headers, records)
    width = len(headers)
    if existing.row_count > len(grid):
        grid.extend([[""] * width for _ in range(existing.row_count - len(grid))])
    return grid


def _guard_grid(grid: list[list[Any]], secrets: list[str]) -> list[list[Any]]:
    headers = [str(cell).strip() if cell is not None else "" for cell in grid[0]] if grid else []
    guarded: list[list[Any]] = []
    for row_index, row in enumerate(grid):
        cleaned: list[Any] = []
        for col_index, cell in enumerate(row):
            if not isinstance(cell, str):
                cleaned.append(cell)
                continue
            text = redact(cell, secrets)
            column = headers[col_index] if col_index < len(headers) else ""
            if row_index != 0 and column not in _ID_COLUMNS:
                text = guard_formula(text)
            cleaned.append(text if text is not None else "")
        guarded.append(cleaned)
    return guarded


def formula_prefix() -> str:
    return FORMULA_PREFIX


def collect_suppliers(settings: Settings, updated_at: str) -> tuple[ProviderResult, ProviderResult]:
    """Đọc từng nhà cung cấp riêng. Lỗi của một bên không xóa kết quả bên kia."""

    return _collect_shop(settings, updated_at), _collect_nastele(settings, updated_at)


def _collect_shop(settings: Settings, updated_at: str) -> ProviderResult:
    if not settings.shop_xoai_non_api_key.get_secret_value().strip():
        return ProviderResult(
            SUPPLIER_SHOP,
            [],
            error="Thiếu SHOP_XOAI_NON_API_KEY.",
            blocked=True,
        )
    try:
        with ShopApiClient(settings) as client:
            return safe_fetch(ShopXoaiNonProvider(client, fetched_at=updated_at))
    except ConfigError as exc:
        return ProviderResult(SUPPLIER_SHOP, [], error=str(exc), blocked=True)
    except IntegrationError as exc:
        return ProviderResult(SUPPLIER_SHOP, [], error=str(exc))
    except Exception:
        return ProviderResult(SUPPLIER_SHOP, [], error="Lỗi không xác định khi đọc Shop Xoài Non.")


def _collect_nastele(settings: Settings, updated_at: str) -> ProviderResult:
    if not settings.nastele_api_key.get_secret_value().strip():
        return ProviderResult(
            SUPPLIER_NASTELE,
            [],
            error="Thiếu NASTELE_API_KEY.",
            blocked=True,
        )
    try:
        with NasteleClient(settings) as client:
            return safe_fetch(NasteleProvider(client, fetched_at=updated_at))
    except ConfigError as exc:
        return ProviderResult(SUPPLIER_NASTELE, [], error=str(exc), blocked=True)
    except IntegrationError as exc:
        return ProviderResult(SUPPLIER_NASTELE, [], error=str(exc))
    except Exception:
        return ProviderResult(SUPPLIER_NASTELE, [], error="Lỗi không xác định khi đọc Nastele.")
