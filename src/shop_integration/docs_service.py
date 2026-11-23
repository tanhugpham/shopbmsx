"""Cập nhật một vùng báo cáo trong Google Doc có sẵn. Không tạo file mới."""

from __future__ import annotations

from typing import Any, Protocol

from shop_integration.errors import ConfigError, GoogleWorkspaceError
from shop_integration.google_auth import call_google
from shop_integration.logging_utils import redact
from shop_integration.quote_form import (
    LAYOUT_MARKER,
    MANUAL_BEGIN,
    MANUAL_END,
    find_exact_paragraph,
    find_prefix_paragraph,
    find_quote_table,
    heading_style_requests,
    manual_section_text,
    pipe_section_range,
    table_delete_requests,
    table_fill_requests,
    tables_inside,
)

_SECRET_MIN_LENGTH = 8
REPORT_BEGIN = "SHOP_XOAI_NON_BAO_CAO_BEGIN"
REPORT_END = "SHOP_XOAI_NON_BAO_CAO_END"
CUSTOMER_BEGIN = "BANG_GIA_KHACH_BEGIN"
CUSTOMER_END = "BANG_GIA_KHACH_END"


class DocsGateway(Protocol):
    def get_document(self, document_id: str) -> dict[str, Any]: ...

    def batch_update(self, document_id: str, requests: list[dict[str, Any]]) -> None: ...


class GoogleDocsGateway:
    def __init__(self, credentials: Any) -> None:
        from googleapiclient.discovery import build

        self._service = build("docs", "v1", credentials=credentials, cache_discovery=False)

    def get_document(self, document_id: str) -> dict[str, Any]:
        document = call_google(
            lambda: self._service.documents().get(documentId=document_id).execute(),
            retry=True,
        )
        if not isinstance(document, dict):
            from shop_integration.errors import GoogleWorkspaceError

            raise GoogleWorkspaceError("Google Docs trả về tài liệu không hợp lệ.")
        return document

    def batch_update(self, document_id: str, requests: list[dict[str, Any]]) -> None:
        call_google(
            lambda: self._service.documents()
            .batchUpdate(documentId=document_id, body={"requests": requests})
            .execute(),
            retry=False,
        )

    def backup_snapshot(self, document_id: str) -> dict[str, Any]:
        document = self.get_document(document_id)
        return {
            "document_id": document_id,
            "title": str(document.get("title") or ""),
            "plain_text": document_plain_text(document),
            "end_index": _raw_end_index(document),
            "blocks": structural_blocks(document),
        }


class DocsService:
    def __init__(
        self,
        gateway: DocsGateway,
        document_id: str,
        *,
        dry_run: bool,
        secret: str = "",
    ) -> None:
        if not document_id.strip():
            raise ConfigError("Thiếu GOOGLE_DOCUMENT_ID.")
        self.gateway = gateway
        self.document_id = document_id.strip()
        self.dry_run = dry_run
        self._secret = secret if len(secret) >= _SECRET_MIN_LENGTH else ""
        self.last_inserted_text = ""

    def region_state(self, content_id: str) -> str:
        """absent, current hoặc stale. Vùng lệch một dấu thì từ chối, không đoán."""

        plain = self.plain_text()
        begins = plain.count(REPORT_BEGIN)
        ends = plain.count(REPORT_END)
        if begins == 0 and ends == 0:
            return "absent"
        if begins != 1 or ends != 1 or plain.find(REPORT_END) < plain.find(REPORT_BEGIN):
            from shop_integration.errors import GoogleWorkspaceError

            raise GoogleWorkspaceError(
                "Google Docs có vùng báo cáo không đủ một cặp định danh. Không ghi đè nội dung ngoài vùng."
            )
        start, end = managed_region_char_span(plain) or (0, 0)
        region = plain[start:end]
        marker = f"Mã nội dung: {content_id}"
        if content_id and marker in region:
            return "current"
        return "stale"

    def replace_managed_report(self, text: str) -> str:
        """Ghi hoặc thay đúng một vùng. Trả về written hoặc skipped."""

        if self.dry_run:
            return "skipped"
        if "BEGIN PRIVATE KEY" in text:
            from shop_integration.errors import GoogleWorkspaceError

            raise GoogleWorkspaceError("Từ chối ghi nội dung có vẻ là khóa riêng tư.")
        safe_text = redact(text, [self._secret])
        body = f"{REPORT_BEGIN}\n{safe_text.rstrip()}\n{REPORT_END}\n"
        document = self.gateway.get_document(self.document_id)
        plain = document_plain_text(document)
        begins = plain.count(REPORT_BEGIN)
        ends = plain.count(REPORT_END)
        if begins == 0 and ends == 0:
            chunk = "\n" + body
            self.last_inserted_text = chunk
            self.gateway.batch_update(
                self.document_id,
                [{"insertText": {"location": {"index": _insertion_index(document)}, "text": chunk}}],
            )
            return "written"
        if begins != 1 or ends != 1:
            from shop_integration.errors import GoogleWorkspaceError

            raise GoogleWorkspaceError(
                "Google Docs có vùng báo cáo không đủ một cặp định danh. Không ghi đè nội dung ngoài vùng."
            )
        span = managed_region_char_span(plain)
        if span is None:
            from shop_integration.errors import GoogleWorkspaceError

            raise GoogleWorkspaceError(
                "Không xác định được vùng báo cáo. Không ghi đè Google Docs."
            )
        start_index, end_index = _doc_span(document, span[0], span[1])
        self.last_inserted_text = body
        self.gateway.batch_update(
            self.document_id,
            [
                {
                    "deleteContentRange": {
                        "range": {"startIndex": start_index, "endIndex": end_index}
                    }
                },
                {"insertText": {"location": {"index": start_index}, "text": body}},
            ],
        )
        return "written"

    def replace_customer_quote(self, text: str, rows: list[list[str]]) -> str:
        """Ghi tiêu đề bảng báo giá. Không ghi shop gốc và không đụng phần sửa tay."""

        del rows
        if self.dry_run:
            return "skipped"
        if MANUAL_BEGIN in text or MANUAL_END in text:
            raise GoogleWorkspaceError("Nội dung tool không được chứa phần sửa tay.")
        self._delete_tables_in_report()
        outcome = self.replace_managed_report(text)
        if outcome != "written":
            return outcome
        self._ensure_manual_section()
        return "written"

    def _ensure_manual_section(self) -> None:
        """Tạo phần sửa tay sau vùng báo giá nếu chưa có. Đã có thì giữ nguyên chữ."""

        document = self.gateway.get_document(self.document_id)
        plain = document_plain_text(document)
        begins = plain.count(MANUAL_BEGIN)
        ends = plain.count(MANUAL_END)
        if begins == 1 and ends == 1 and plain.find(MANUAL_END) > plain.find(MANUAL_BEGIN):
            return
        if begins or ends:
            raise GoogleWorkspaceError(
                "Phần sửa tay không đủ một cặp dấu. Không ghi đè phần đã nhập."
            )
        self.gateway.batch_update(
            self.document_id,
            [
                {
                    "insertText": {
                        "location": {"index": _insertion_index(document)},
                        "text": manual_section_text(),
                    }
                }
            ],
        )

    def _apply(self, requests: list[dict[str, Any]]) -> None:
        if requests:
            self.gateway.batch_update(self.document_id, requests)

    def _delete_tables_in_report(self) -> None:
        document = self.gateway.get_document(self.document_id)
        bounds = managed_region_doc_bounds(document)
        if bounds is None:
            return
        self._apply(table_delete_requests(tables_inside(document, bounds[0], bounds[1])))

    def _install_quote_table(self, rows: list[list[str]]) -> None:
        document = self.gateway.get_document(self.document_id)
        if find_exact_paragraph(document, "BÁO GIÁ") is None:
            return
        if not rows:
            self._apply(heading_style_requests(document))
            return
        section = pipe_section_range(document)
        if section is None:
            raise GoogleWorkspaceError("Không thấy danh sách báo giá để chuyển thành bảng.")
        self._apply(
            [{"deleteContentRange": {"range": {"startIndex": section[0], "endIndex": section[1]}}}]
        )
        document = self.gateway.get_document(self.document_id)
        footer = find_prefix_paragraph(document, "Mã nội dung:")
        if footer is None:
            raise GoogleWorkspaceError("Không thấy mã nội dung sau khi dọn danh sách báo giá.")
        self._apply(
            [
                {
                    "insertTable": {
                        "rows": len(rows) + 1,
                        "columns": 4,
                        "location": {"index": footer[0]},
                    }
                }
            ]
        )
        document = self.gateway.get_document(self.document_id)
        table = find_quote_table(document)
        if table is None:
            raise GoogleWorkspaceError("Không chèn được bảng báo giá.")
        self._apply(heading_style_requests(document) + table_fill_requests(table, rows))

    def _remove_exact_line(self, line: str) -> None:
        try:
            document = self.gateway.get_document(self.document_id)
        except Exception:
            return
        found = find_exact_paragraph(document, line)
        if found is None:
            return
        try:
            self._apply(
                [
                    {
                        "deleteContentRange": {
                            "range": {"startIndex": found[0], "endIndex": found[1]}
                        }
                    }
                ]
            )
        except Exception:
            return

    def customer_region_state(self, content_id: str) -> str:
        """absent, current hoặc stale. Vùng khách lệch một dấu thì từ chối."""

        plain = self.plain_text()
        return _marker_state(plain, CUSTOMER_BEGIN, CUSTOMER_END, content_id)

    def replace_customer_prices(self, text: str, content_id: str) -> str:
        """Thay vùng bảng giá khách. Không đụng vùng báo cáo Shop Xoài Non."""

        if self.dry_run:
            return "skipped"
        if "BEGIN PRIVATE KEY" in text:
            from shop_integration.errors import GoogleWorkspaceError

            raise GoogleWorkspaceError("Từ chối ghi nội dung có vẻ là khóa riêng tư.")
        safe_text = redact(text, [self._secret])
        document = self.gateway.get_document(self.document_id)
        plain = document_plain_text(document)
        state = _marker_state(plain, CUSTOMER_BEGIN, CUSTOMER_END, content_id)
        if state == "current":
            return "skipped"
        body = f"{CUSTOMER_BEGIN}\n{safe_text.rstrip()}\n{CUSTOMER_END}\n"
        begins = plain.count(CUSTOMER_BEGIN)
        ends = plain.count(CUSTOMER_END)
        if begins == 0 and ends == 0:
            chunk = "\n" + body
            self.last_inserted_text = chunk
            self.gateway.batch_update(
                self.document_id,
                [{"insertText": {"location": {"index": _insertion_index(document)}, "text": chunk}}],
            )
            return "written"
        span = _region_char_span(plain, CUSTOMER_BEGIN, CUSTOMER_END)
        if span is None:
            from shop_integration.errors import GoogleWorkspaceError

            raise GoogleWorkspaceError(
                "Google Docs có vùng bảng giá khách không đủ một cặp định danh. Không ghi đè nội dung ngoài vùng."
            )
        start_index, end_index = _doc_span(document, span[0], span[1])
        self.last_inserted_text = body
        self.gateway.batch_update(
            self.document_id,
            [
                {
                    "deleteContentRange": {
                        "range": {"startIndex": start_index, "endIndex": end_index}
                    }
                },
                {"insertText": {"location": {"index": start_index}, "text": body}},
            ],
        )
        return "written"

    def append_report(self, text: str) -> None:
        if self.dry_run:
            return
        if "BEGIN PRIVATE KEY" in text:
            from shop_integration.errors import GoogleWorkspaceError

            raise GoogleWorkspaceError("Từ chối ghi nội dung có vẻ là khóa riêng tư.")
        safe_text = redact(text, [self._secret])
        document = self.gateway.get_document(self.document_id)
        index = _insertion_index(document)
        self.gateway.batch_update(
            self.document_id,
            [
                {
                    "insertText": {
                        "location": {"index": index},
                        "text": "\n" + safe_text.rstrip() + "\n",
                    }
                }
            ],
        )

    def plain_text(self) -> str:
        return document_plain_text(self.gateway.get_document(self.document_id))

    def contains_marker(self, marker: str) -> bool:
        if not marker.strip():
            return False
        return marker in self.plain_text()


def managed_region_char_span(text: str) -> tuple[int, int] | None:
    """Khoảng ký tự của vùng báo cáo Shop, gồm newline ngăn cách ngay trước dấu bắt đầu."""

    return _region_char_span(text, REPORT_BEGIN, REPORT_END)


def _region_char_span(text: str, begin: str, end: str) -> tuple[int, int] | None:
    if text.count(begin) != 1 or text.count(end) != 1:
        return None
    start = text.find(begin)
    stop = text.find(end)
    if stop < start:
        return None
    end_exclusive = stop + len(end)
    if end_exclusive < len(text) and text[end_exclusive] == "\n":
        end_exclusive += 1
    if start > 0 and text[start - 1] == "\n":
        start -= 1
    return start, end_exclusive


def _marker_state(plain: str, begin: str, end: str, content_id: str) -> str:
    begins = plain.count(begin)
    ends = plain.count(end)
    if begins == 0 and ends == 0:
        return "absent"
    if begins != 1 or ends != 1 or plain.find(end) < plain.find(begin):
        from shop_integration.errors import GoogleWorkspaceError

        raise GoogleWorkspaceError(
            "Google Docs có vùng bảng giá không đủ một cặp định danh. Không ghi đè nội dung ngoài vùng."
        )
    span = _region_char_span(plain, begin, end) or (0, 0)
    region = plain[span[0] : span[1]]
    marker = f"Mã nội dung: {content_id}"
    if content_id and marker in region:
        return "current"
    return "stale"


def text_without_managed_region(text: str) -> str | None:
    span = managed_region_char_span(text)
    if span is None:
        return None
    start, end = span
    return text[:start] + text[end:]


def managed_region_doc_bounds(document: dict[str, Any]) -> tuple[int, int] | None:
    plain = document_plain_text(document)
    span = managed_region_char_span(plain)
    if span is None:
        return None
    try:
        return _doc_span(document, span[0], span[1])
    except Exception:
        return None


def document_plain_text(document: dict[str, Any]) -> str:
    parts: list[str] = []
    for _start, _end, text in _iter_text_runs(document):
        parts.append(text)
    return "".join(parts)


def structural_blocks(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Tóm tắt cấu trúc. Không đủ để dựng lại bảng, ảnh hay định dạng ký tự."""

    blocks: list[dict[str, Any]] = []
    for element in _body_content(document):
        if not isinstance(element, dict):
            continue
        if "paragraph" in element:
            style = element.get("paragraph", {}).get("paragraphStyle", {})
            named = style.get("namedStyleType", "") if isinstance(style, dict) else ""
            text = "".join(
                run.get("textRun", {}).get("content", "")
                for run in element.get("paragraph", {}).get("elements", [])
                if isinstance(run, dict)
            )
            blocks.append({"type": "paragraph", "named_style": named, "text": text})
        elif "table" in element:
            rows = element.get("table", {}).get("tableRows", [])
            columns = 0
            if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                cells = rows[0].get("tableCells", [])
                columns = len(cells) if isinstance(cells, list) else 0
            blocks.append(
                {
                    "type": "table",
                    "rows": len(rows) if isinstance(rows, list) else 0,
                    "columns": columns,
                }
            )
        elif "sectionBreak" in element:
            blocks.append({"type": "section_break"})
    return blocks


def _body_content(document: dict[str, Any]) -> list[Any]:
    content = document.get("body", {}).get("content", [])
    return content if isinstance(content, list) else []


def _iter_text_runs(document: dict[str, Any]):
    for element in _body_content(document):
        if not isinstance(element, dict):
            continue
        if "paragraph" in element:
            yield from _paragraph_runs(element.get("paragraph", {}))
        elif "table" in element:
            for row in element.get("table", {}).get("tableRows", []):
                if not isinstance(row, dict):
                    continue
                for cell in row.get("tableCells", []):
                    if not isinstance(cell, dict):
                        continue
                    for block in cell.get("content", []):
                        if isinstance(block, dict) and "paragraph" in block:
                            yield from _paragraph_runs(block.get("paragraph", {}))


def _paragraph_runs(paragraph: Any):
    if not isinstance(paragraph, dict):
        return
    for element in paragraph.get("elements", []):
        if not isinstance(element, dict):
            continue
        run = element.get("textRun")
        if not isinstance(run, dict):
            continue
        text = run.get("content")
        if not isinstance(text, str):
            continue
        yield element.get("startIndex"), element.get("endIndex"), text


def _raw_end_index(document: dict[str, Any]) -> int:
    content = _body_content(document)
    end_index = 1
    if content and isinstance(content[-1], dict):
        raw_end = content[-1].get("endIndex", 1)
        if isinstance(raw_end, int):
            end_index = raw_end
    return end_index


def _doc_span(document: dict[str, Any], char_start: int, char_end: int) -> tuple[int, int]:
    from shop_integration.errors import GoogleWorkspaceError

    mapping: list[int] = []
    for start, _end, text in _iter_text_runs(document):
        if not isinstance(start, int):
            raise GoogleWorkspaceError(
                "Không đủ chỉ số cấu trúc Docs để thay vùng báo cáo. Không ghi đè."
            )
        mapping.extend(start + offset for offset in range(len(text)))
    if char_end <= char_start or char_end > len(mapping):
        raise GoogleWorkspaceError(
            "Không đủ chỉ số cấu trúc Docs để thay vùng báo cáo. Không ghi đè."
        )
    start_index = mapping[char_start]
    end_index = mapping[char_end - 1] + 1
    raw_end = _raw_end_index(document)
    if end_index >= raw_end:
        end_index = raw_end - 1
    if end_index <= start_index:
        raise GoogleWorkspaceError(
            "Không đủ chỉ số cấu trúc Docs để thay vùng báo cáo. Không ghi đè."
        )
    return start_index, end_index


def _insertion_index(document: dict[str, Any]) -> int:
    """Docs API: endIndex trỏ sau ký tự xuống dòng cuối. Chèn tại endIndex - 1."""

    content = document.get("body", {}).get("content", [])
    end_index = 1
    if isinstance(content, list) and content and isinstance(content[-1], dict):
        raw_end = content[-1].get("endIndex", 1)
        if isinstance(raw_end, int):
            end_index = raw_end
    return max(end_index - 1, 1)
