"""Đồng bộ danh mục. Không gọi báo giá và không gọi tạo đơn."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from shop_integration.api_client import ShopApiClient
from shop_integration.backup_service import BackupService
from shop_integration.combined_quote import CombinedQuoteWriter, nastele_quote_rows, shop_quote_rows
from shop_integration.config import Settings
from shop_integration.docs_service import REPORT_END, DocsService
from shop_integration.errors import BackupError, ConfigError, GoogleWorkspaceError, PartialSyncError
from shop_integration.models import PriceChange, Product, SyncReport
from shop_integration.pricing import PricingPolicy, policy_from_settings
from shop_integration.product_service import PRODUCT_HEADERS, ProductService, resolve_variant_headers
from shop_integration.quote_form import LAYOUT_MARKER
from shop_integration.report_service import (
    catalog_fingerprint,
    product_catalog_lines,
    render_customer_quote,
    render_report,
)
from shop_integration.sheet_layout import (
    ALL_TABS,
    TAB_CONFIG,
    TAB_CUSTOMER_PRICES,
    TAB_PRICE_HISTORY,
    TAB_PRODUCTS,
    TAB_SYNC_LOG,
    TAB_VARIANTS,
    classify_rows,
    diff_variant_prices,
)
from shop_integration.sheets_service import SheetsService, config_value_contains_secret
from shop_integration.time_utils import format_timestamp, now_vietnam


class SyncService:
    def __init__(
        self,
        settings: Settings,
        api_client: ShopApiClient,
        product_service: ProductService,
        *,
        sheets_service: SheetsService | None = None,
        docs_service: DocsService | None = None,
        clock: Callable[[], datetime] | None = None,
        page_size: int = 200,
        backup_service: BackupService | None = None,
        quote_gateway: Any | None = None,
    ) -> None:
        self._settings = settings
        self._api = api_client
        self._products = product_service
        self._sheets = sheets_service
        self._docs = docs_service
        self._clock = clock or now_vietnam
        self._page_size = page_size
        self._backup = backup_service
        self._quote_gateway = quote_gateway

    def run(self, *, write_sheets: bool, write_doc: bool) -> SyncReport:
        started = self._clock()
        started_text = format_timestamp(started)
        raw_items = self._api.list_all_products(limit=self._page_size)
        products, parse_warnings = self._products.parse_many(raw_items)
        warnings = [item.message for item in parse_warnings]
        warnings.extend(self._api.notices)
        warnings.extend(self._policy_warnings())

        summary = self._products.summarize(products)
        price_changes: list[dict] = []
        sheets_written = False
        sheets_note = "Bỏ qua Google Sheets vì chưa có kết nối."
        existing_ids: list[str] = []
        if self._sheets is not None:
            existing_products = self._sheets.read_records(TAB_PRODUCTS)
            existing_variants = self._sheets.read_records(TAB_VARIANTS)
            existing_customer = self._sheets.read_records(TAB_CUSTOMER_PRICES)
            existing_history = self._sheets.read_records(TAB_PRICE_HISTORY)
            existing_log = self._sheets.read_records(TAB_SYNC_LOG)
            existing_config = self._sheets.read_records(TAB_CONFIG)
            existing_ids = [
                str(row.get("Product ID", "")).strip()
                for row in existing_products.records
                if str(row.get("Product ID", "")).strip()
            ]
            variant_headers = resolve_variant_headers(existing_variants.headers, products)
        else:
            existing_products = existing_variants = existing_customer = None
            existing_history = existing_log = existing_config = None
            variant_headers = resolve_variant_headers([], products)

        policy = policy_from_settings(self._settings)
        product_records = self._products.build_product_records(products, started_text, policy)
        variant_records = self._products.build_variant_records(
            products, started_text, variant_headers, policy
        )
        customer_records = self._products.build_customer_records(products, started_text, policy)
        if existing_variants is not None:
            price_changes = diff_variant_prices(
                existing_variants.records, variant_records, started_text
            )
        diff = classify_rows(
            existing_products.records if existing_products is not None else [],
            product_records,
            ["Product ID"],
        )
        blocked = catalog_block_reason(products, existing_ids, list(self._api.notices))
        missing_ids = [] if blocked else list(diff.missing_ids)
        if blocked and not ((write_sheets or write_doc) and not self._settings.dry_run):
            warnings.append(blocked)

        content_id = catalog_fingerprint(
            product_records,
            variant_records,
            price_changes,
            warnings,
            variant_headers=variant_headers,
        )
        writing = (write_sheets or write_doc) and not self._settings.dry_run
        if writing:
            if blocked:
                raise ConfigError(blocked)
            self._ensure_backup()

        if self._sheets is not None:
            if write_sheets and self._settings.dry_run:
                sheets_note = "Dry-run: đã đọc Sheets, không ghi ô nào."
            elif write_sheets:
                config_pairs = self._config_pairs(started_text)
                secret = self._settings.shop_xoai_non_api_key.get_secret_value()
                if config_value_contains_secret(config_pairs, secret):
                    raise ConfigError("Cấu hình Sheets chứa bí mật. Đã dừng trước khi ghi.")
                note = _log_note(warnings, missing_ids)
                log_record = {
                    "Thời điểm": started_text,
                    "Môi trường": self._settings.app_env,
                    "Dry run": "Không",
                    "Số sản phẩm": summary.product_count,
                    "Số biến thể": summary.variant_count,
                    "Số thay đổi giá": len(price_changes),
                    "Kết quả": "Sheets đã xác minh. Docs thực hiện sau bước này.",
                    "Ghi chú": note,
                    "Số thêm mới": diff.added,
                    "Số cập nhật": diff.updated,
                    "Số không đổi": diff.unchanged,
                    "Lỗi": len(warnings),
                }
                try:
                    counts = self._sheets.apply_catalog(
                        existing_products=existing_products,
                        existing_variants=existing_variants,
                        existing_history=existing_history,
                        existing_log=existing_log,
                        existing_config=existing_config,
                        product_headers=PRODUCT_HEADERS,
                        variant_headers=variant_headers,
                        product_records=product_records,
                        variant_records=variant_records,
                        price_changes=price_changes,
                        config_pairs=config_pairs,
                        log_record=log_record,
                        updated_at=started_text,
                        include_log=False,
                        existing_customer=existing_customer,
                        customer_records=customer_records,
                    )
                    self._sheets.assert_tab_count(TAB_PRODUCTS, counts["products"])
                    self._sheets.assert_tab_count(TAB_VARIANTS, counts["variants"])
                    self._sheets.assert_tab_count(TAB_CUSTOMER_PRICES, counts["customer"])
                    self._sheets.append_sync_log(log_record)
                except PartialSyncError:
                    raise
                except Exception as exc:
                    raise PartialSyncError(
                        "Google Sheets ghi chưa trọn vẹn. Google Docs không được cập nhật. "
                        "Không tự khôi phục để tránh xóa dữ liệu mới. "
                        f"Chi tiết: {exc}"
                    ) from None
                self._write_journal(content_id, doc_appended_text="")
                sheets_written = True
                sheets_note = (
                    "Đã ghi theo batch và đọc lại số dòng SAN_PHAM, BIEN_THE, BANG_GIA_BAN. "
                    "Không ghi đè Trang tính1."
                )
            else:
                sheets_note = "Đã đọc Sheets để so sánh giá, không ghi trong lệnh này."
        elif write_sheets and not self._settings.dry_run:
            warnings.append("Chưa đọc được Google Sheets vì thiếu spreadsheet hoặc thông tin xác thực.")
            sheets_note = "Không ghi được vì thiếu cấu hình Google Sheets."
        elif write_sheets:
            warnings.append("Chưa đọc được Google Sheets vì thiếu spreadsheet hoặc thông tin xác thực.")
            sheets_note = "Không ghi được vì thiếu cấu hình Google Sheets."

        quote_note = self._update_combined_quote(
            products,
            policy,
            write_sheets=write_sheets,
            write_doc=write_doc,
        )
        if quote_note:
            warnings.append(quote_note)

        finished_text = format_timestamp(self._clock())
        changes = [
            PriceChange(
                product_id=str(item["Product ID"]),
                variant_id=str(item["Variant ID"]),
                old_price=int(item["Giá cũ (VND)"]),
                new_price=int(item["Giá mới (VND)"]),
                changed_at=str(item["Thời điểm"]),
            )
            for item in price_changes
        ]
        region_state = "absent"
        doc_note = "Không ghi Google Docs trong lệnh này."
        if write_doc and self._settings.dry_run:
            doc_note = "Dry-run: không ghi Google Docs."
        elif write_doc and self._docs is None:
            doc_note = "Bỏ qua Google Docs vì thiếu document hoặc thông tin xác thực."
            warnings.append(doc_note)
        elif write_doc and self._docs is not None:
            try:
                region_state = self._docs.region_state(content_id)
            except Exception as exc:
                raise _docs_failure(sheets_written, exc) from None
            if region_state == "current" and not self._docs.contains_marker(LAYOUT_MARKER):
                region_state = "stale"
            if region_state == "current":
                doc_note = "Báo cáo không đổi, không nối thêm."
            else:
                doc_note = "Đã cập nhật vùng báo cáo Google Docs."

        report = SyncReport(
            started_at=started_text,
            finished_at=finished_text,
            environment=self._settings.app_env,
            dry_run=self._settings.dry_run,
            summary=summary,
            price_changes=changes,
            warnings=warnings,
            sheets_written=sheets_written,
            doc_written=False,
            sheets_note=sheets_note,
            doc_note=doc_note,
            content_id=content_id,
            product_lines=product_catalog_lines(product_records),
            added_products=list(diff.added_ids),
            missing_products=missing_ids,
            added_count=diff.added,
            updated_count=diff.updated,
            unchanged_count=diff.unchanged,
        )
        report.text = render_report(report, secrets=self._settings.secret_values())
        quote_text = render_customer_quote(
            product_records,
            updated_at=finished_text,
            content_id=content_id,
            secrets=self._settings.secret_values(),
        )
        if (
            write_doc
            and self._docs is not None
            and not self._settings.dry_run
            and region_state != "current"
        ):
            try:
                outcome = self._docs.replace_customer_quote(quote_text, [])
                if outcome == "skipped":
                    report.doc_note = "Báo cáo không đổi, không nối thêm."
                    report.text = render_report(report, secrets=self._settings.secret_values())
                else:
                    if not self._docs.contains_marker(REPORT_END) or not self._docs.contains_marker(
                        f"Mã nội dung: {content_id}"
                    ):
                        raise PartialSyncError(
                            "Google Sheets đã ghi nhưng chưa đọc lại được vùng báo cáo trên Google Docs. "
                            "Không xem là thành công toàn bộ. Có thể chạy lại. Không tự khôi phục Sheets."
                        )
                    report.doc_written = True
                    try:
                        self._write_journal(
                            content_id,
                            doc_appended_text=self._docs.last_inserted_text,
                        )
                    except BackupError:
                        report.warnings.append(
                            "Docs đã đọc lại nhưng không cập nhật được nhật ký khôi phục cục bộ."
                        )
                        report.text = render_report(report, secrets=self._settings.secret_values())
            except PartialSyncError:
                raise
            except Exception as exc:
                raise _docs_failure(sheets_written, exc) from None
        return report

    def _update_combined_quote(
        self,
        products: list[Product],
        policy: PricingPolicy,
        *,
        write_sheets: bool,
        write_doc: bool,
    ) -> str:
        spreadsheet_id = self._settings.last_spreadsheet_id.strip()
        if not spreadsheet_id or not (write_sheets or write_doc):
            return ""
        gateway = self._quote_gateway
        if gateway is None and self._sheets is not None:
            gateway = self._sheets.gateway
        if gateway is None:
            return "Bỏ qua báo giá tổng hợp vì chưa có kết nối Google Sheets."
        nastele_rows, nastele_note = self._nastele_quote_rows(policy)
        try:
            result = CombinedQuoteWriter(gateway, spreadsheet_id).update(
                shop_quote_rows(products, policy),
                dry_run=self._settings.dry_run,
                nastele_rows=nastele_rows,
            )
        except Exception as extra:
            return f"Báo giá tổng hợp chưa ghi được. Chi tiết: {extra}"
        notes = [nastele_note, result.note(dry_run=self._settings.dry_run)]
        return " ".join(part for part in notes if part)

    def _nastele_quote_rows(self, policy: PricingPolicy) -> tuple[list[dict] | None, str]:
        from shop_integration.suppliers.source_sync import collect_nastele
        from shop_integration.time_utils import format_timestamp

        try:
            result = collect_nastele(self._settings, format_timestamp(self._clock()))
        except Exception as exc:
            return None, f"Không đọc được Nastele cho MENU. Chi tiết: {exc}"
        if result.blocked or result.error:
            return None, result.error or "Bỏ qua Nastele vì thiếu cấu hình."
        if result.catalog_status == "NASTELE_CATALOG_EMPTY" or not result.offers:
            return None, "Catalog Nastele rỗng. Giữ NASTELEID hiện có. Không xóa dòng tay."
        return nastele_quote_rows(result.offers, policy), ""

    def _ensure_backup(self) -> None:
        if self._backup is None:
            raise ConfigError("Chưa có sao lưu đã kiểm tra. Dừng trước khi ghi Google Sheets hoặc Google Docs.")
        try:
            manifest = self._backup.create_and_verify()
        except BackupError:
            raise
        except Exception as exc:
            raise BackupError(f"Backup thất bại. Dừng trước khi ghi. Chi tiết: {exc}") from None
        if manifest.get("status") != "verified":
            raise BackupError("Backup chưa hoàn tất hoặc không kiểm tra được tính toàn vẹn. Dừng trước khi ghi.")

    def _write_journal(self, content_id: str, *, doc_appended_text: str) -> None:
        if self._backup is None or self._sheets is None:
            return
        tabs: dict[str, list] = {}
        try:
            for tab in ALL_TABS:
                tabs[tab] = self._sheets.gateway.get_values(
                    self._sheets.spreadsheet_id,
                    f"'{tab}'!A:ZZ",
                )
            self._backup.write_journal(
                {"content_id": content_id, "tabs": tabs, "doc_appended_text": doc_appended_text}
            )
        except BackupError:
            raise
        except GoogleWorkspaceError as exc:
            raise BackupError(
                "Không cập nhật được nhật ký khôi phục cục bộ sau khi ghi Docs. "
                "Báo giá đã ghi. Không tự khôi phục."
            ) from exc

    def _policy_warnings(self) -> list[str]:
        warnings: list[str] = []
        if self._settings.enable_purchase:
            warnings.append(
                "ENABLE_PURCHASE=true bị bỏ qua. Hệ thống không gọi POST /orders và không trừ ví."
            )
        if self._settings.enable_telegram:
            warnings.append(
                "ENABLE_TELEGRAM=true bị bỏ qua. Bộ điều hợp Telegram chỉ có giao diện, chưa kết nối mạng."
            )
        return warnings

    def _config_pairs(self, synced_at: str) -> list[tuple[str, str]]:
        secret = self._settings.shop_xoai_non_api_key.get_secret_value().strip()
        purchase_note = "Mua hàng bị khóa trong mã nguồn"
        if self._settings.enable_purchase:
            purchase_note = "ENABLE_PURCHASE=true bị bỏ qua. Mua hàng vẫn bị khóa trong mã nguồn"
        return [
            ("base_url", self._settings.shop_xoai_non_base_url),
            ("app_env", self._settings.app_env),
            ("dry_run", "true" if self._settings.dry_run else "false"),
            ("enable_purchase", "false"),
            ("purchase_policy", purchase_note),
            ("enable_telegram", "false"),
            ("telegram_policy", "Bộ điều hợp Telegram chưa kích hoạt"),
            ("api_key_configured", "có" if secret else "không"),
            ("last_sync_at", synced_at),
            ("catalog_source", "GET /products"),
            ("pricing_rate_low", format(self._settings.pricing_rate_low, "f")),
            ("pricing_rate_high", format(self._settings.pricing_rate_high, "f")),
            ("pricing_middle_min", str(self._settings.pricing_middle_min)),
            ("pricing_middle_max", str(self._settings.pricing_middle_max)),
            ("pricing_middle_profit", str(self._settings.pricing_middle_profit)),
        ]


def catalog_block_reason(products: list, existing_ids: list[str], notices: list[str]) -> str | None:
    for notice in notices:
        if "có thể chưa đủ" in notice or "trả lại một trang" in notice:
            return (
                "Danh mục API không đầy đủ. Không ghi và không đánh dấu sản phẩm cũ là thiếu."
            )
    if not products and existing_ids:
        return "API không trả sản phẩm trong khi bảng đang có dữ liệu. Không ghi đè."
    return None


def _docs_failure(sheets_written: bool, exc: Exception) -> PartialSyncError:
    prefix = (
        "Google Sheets đã ghi nhưng Google Docs thất bại. "
        if sheets_written
        else "Google Docs thất bại. "
    )
    return PartialSyncError(
        prefix
        + "Không xem là thành công toàn bộ. Có thể chạy lại. "
        "Không tự khôi phục Sheets. "
        f"Chi tiết: {exc}"
    )


def _log_note(warnings: list[str], missing_ids: list[str]) -> str:
    parts = ["; ".join(warnings) if warnings else "Không có cảnh báo."]
    if missing_ids:
        shown = ", ".join(missing_ids[:30])
        extra = f" (+{len(missing_ids) - 30})" if len(missing_ids) > 30 else ""
        parts.append(
            f"Không còn trong API: {shown}{extra}. Giá và tồn kho lần trước được giữ, không kết luận hết hàng."
        )
    text = " ".join(parts)
    if len(text) > 500:
        return text[:497] + "..."
    return text
