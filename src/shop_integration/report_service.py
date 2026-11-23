"""Dựng báo cáo tiếng Việt từ dữ liệu đã kiểm tra. Không chứa bí mật."""

from __future__ import annotations

import hashlib

from shop_integration.logging_utils import redact
from shop_integration.models import PriceChange, SyncReport
from shop_integration.product_service import PRODUCT_HEADERS, VARIANT_HEADERS
from shop_integration.quote_form import quote_lines

_MAX_CHANGES = 50
_MAX_WARNINGS = 30
_MAX_PRODUCTS = 500


def render_report(report: SyncReport, *, secrets: list[str] | None = None) -> str:
    summary = report.summary
    lines = [
        "BÁO CÁO ĐỒNG BỘ SHOP XOÀI NON",
        f"Thời điểm bắt đầu: {report.started_at}",
        f"Thời điểm kết thúc: {report.finished_at}",
        f"Môi trường: {report.environment}",
        f"Chế độ chạy thử: {'Có' if report.dry_run else 'Không'}",
        f"Số sản phẩm: {summary.product_count}",
        f"Số biến thể: {summary.variant_count}",
        f"Còn hàng: {summary.in_stock}",
        f"Hết hàng: {summary.out_of_stock}",
        f"Tồn kho không rõ: {summary.unknown_stock}",
        f"Sản phẩm thiếu giá: {summary.missing_price}",
        f"Số thay đổi giá: {len(report.price_changes)}",
        f"Số sản phẩm thêm mới: {report.added_count}",
        f"Số sản phẩm cập nhật: {report.updated_count}",
        f"Số sản phẩm không đổi: {report.unchanged_count}",
        f"Google Sheets: {report.sheets_note or _flag(report.sheets_written)}",
        f"Google Docs: {report.doc_note or _flag(report.doc_written)}",
        "Nguồn dữ liệu: Shop Xoài Non Customer API v1, chỉ các lệnh đọc và báo giá khi được yêu cầu.",
        "Mua hàng tự động: tắt.",
        "Lấy tài khoản đã giao: tắt.",
    ]
    if report.content_id:
        lines.append(f"Mã nội dung: {report.content_id}")
    lines.append("Danh sách sản phẩm:")
    lines.extend(_id_lines(report.product_lines, empty="- Không có sản phẩm.", noun="sản phẩm"))
    lines.append("Thay đổi giá:")
    if not report.price_changes:
        lines.append("- Không có thay đổi giá so với bảng biến thể hiện tại.")
    else:
        lines.extend(_change_lines(report.price_changes))
    lines.append("Sản phẩm mới:")
    lines.extend(
        _id_lines(
            [f"- {item}" for item in report.added_products],
            empty="- Không có sản phẩm mới.",
            noun="sản phẩm mới",
        )
    )
    lines.append("Sản phẩm không còn trong API:")
    lines.extend(
        _id_lines(
            [f"- {item}" for item in report.missing_products],
            empty="- Không có.",
            noun="sản phẩm",
        )
    )
    lines.append("Cảnh báo:")
    if not report.warnings:
        lines.append("- Không có cảnh báo.")
    else:
        visible = report.warnings[:_MAX_WARNINGS]
        lines.extend(f"- {warning}" for warning in visible)
        hidden = len(report.warnings) - len(visible)
        if hidden > 0:
            lines.append(f"- Còn {hidden} cảnh báo khác.")
    text = "\n".join(lines)
    return redact(text, secrets or [])


def catalog_fingerprint(
    product_records: list[dict],
    variant_records: list[dict],
    price_changes: list[dict],
    warnings: list[str],
    variant_headers: list[str] | None = None,
) -> str:
    """Mã ổn định của danh mục. Không gồm thời điểm, nên lần chạy lại không đổi mã."""

    headers = list(variant_headers or VARIANT_HEADERS)
    lines: list[str] = []
    skip = {"Ngày cập nhật", "STT"}
    for record in product_records:
        lines.append(
            "P:" + "|".join(_stable_cell(record, header) for header in PRODUCT_HEADERS if header not in skip)
        )
    for record in variant_records:
        lines.append(
            "V:" + "|".join(_stable_cell(record, header) for header in headers if header not in skip)
        )
    for change in price_changes:
        lines.append(
            "C:"
            + "|".join(
                str(change.get(key, ""))
                for key in ("Product ID", "Variant ID", "Giá cũ (VND)", "Giá mới (VND)")
            )
        )
    lines.extend(f"W:{warning}" for warning in warnings)
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    return digest[:16]


def render_customer_quote(
    records: list[dict],
    *,
    updated_at: str,
    content_id: str = "",
    secrets: list[str] | None = None,
) -> str:
    """Báo giá đưa vào Google Docs. Chỉ STT, tên, giá bán và tình trạng."""

    lines = quote_lines(records, updated_at=updated_at, content_id=content_id)
    text = "\n".join(lines).rstrip() + "\n"
    return redact(text, secrets or [])


def product_catalog_lines(records: list[dict]) -> list[str]:
    lines: list[str] = []
    for record in records:
        name = record.get("Tên sản phẩm đã decode") or record.get("Tên sản phẩm") or ""
        stock = record.get("Trạng thái hàng") or record.get("Trạng thái còn hàng") or ""
        cost = _price_span(
            record,
            "Giá gốc thấp nhất",
            "Giá gốc cao nhất",
            "Giá thấp nhất (VND)",
            "Giá cao nhất (VND)",
        )
        selling = _price_span(record, "Giá bán thấp nhất", "Giá bán cao nhất", "", "")
        lines.append(
            f"- {record.get('Product ID', '')} | {name} | giá bán {selling} | giá gốc {cost} | {stock}"
        )
    return lines


def _price_span(record: dict, low_key: str, high_key: str, legacy_low: str, legacy_high: str) -> str:
    low = record.get(low_key, "")
    high = record.get(high_key, "")
    if low == "" and high == "" and legacy_low:
        low = record.get(legacy_low, "")
        high = record.get(legacy_high, "")
    if low == "" and high == "":
        return "thiếu giá"
    if str(low) == str(high):
        return f"{low} VND"
    return f"{low}-{high} VND"


def _stable_cell(record: dict, header: str) -> str:
    value = record.get(header, "")
    if value is None:
        return ""
    return str(value).strip()


def _id_lines(lines: list[str], *, empty: str, noun: str) -> list[str]:
    if not lines:
        return [empty]
    visible = lines[:_MAX_PRODUCTS]
    rendered = list(visible)
    hidden = len(lines) - len(visible)
    if hidden > 0:
        rendered.append(f"- Còn {hidden} {noun} khác.")
    return rendered


def _flag(done: bool) -> str:
    return "đã ghi" if done else "không ghi"


def _change_lines(changes: list[PriceChange]) -> list[str]:
    visible = changes[:_MAX_CHANGES]
    lines = [
        (
            f"- {change.product_id} / {change.variant_id}: "
            f"{change.old_price} -> {change.new_price}"
        )
        for change in visible
    ]
    hidden = len(changes) - len(visible)
    if hidden > 0:
        lines.append(f"- Còn {hidden} thay đổi giá khác.")
    return lines
