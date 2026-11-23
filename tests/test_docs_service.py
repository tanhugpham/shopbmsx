from __future__ import annotations

from shop_integration.docs_service import DocsService
from shop_integration.errors import GoogleWorkspaceError
from shop_integration.models import CatalogSummary, SyncReport
from shop_integration.report_service import render_report
import pytest


class MemoryDocs:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.reads = 0
        self.plain = ""
        self.fail_next = False

    def get_document(self, document_id: str) -> dict:
        self.reads += 1
        assert document_id == "doc-id"
        if not self.plain:
            return {"body": {"content": [{"endIndex": 2}]}}
        end = 1 + len(self.plain)
        return {
            "body": {
                "content": [
                    {
                        "startIndex": 1,
                        "endIndex": end,
                        "paragraph": {
                            "elements": [
                                {
                                    "startIndex": 1,
                                    "endIndex": end,
                                    "textRun": {"content": self.plain},
                                }
                            ]
                        },
                    }
                ]
            }
        }

    def batch_update(self, document_id: str, requests: list[dict]) -> None:
        from shop_integration.errors import GoogleWorkspaceError

        if self.fail_next:
            self.fail_next = False
            raise GoogleWorkspaceError("Docs từ chối ghi.")
        self.requests.extend(requests)
        for request in requests:
            delete = request.get("deleteContentRange")
            if isinstance(delete, dict):
                span = delete.get("range", {})
                start = int(span["startIndex"])
                end = int(span["endIndex"])
                self.plain = self.plain[: start - 1] + self.plain[end - 1 :]
            insert = request.get("insertText")
            if isinstance(insert, dict):
                index = int(insert["location"]["index"])
                text = str(insert.get("text") or "")
                offset = max(index - 1, 0)
                self.plain = self.plain[:offset] + text + self.plain[offset:]


def _report() -> SyncReport:
    return SyncReport(
        started_at="2026-11-23T10:00:00+07:00",
        finished_at="2026-11-23T10:00:01+07:00",
        environment="test",
        dry_run=False,
        summary=CatalogSummary(product_count=2, variant_count=3, in_stock=1, out_of_stock=1),
        warnings=["Cảnh báo có sxn_live_test_key_value"],
        sheets_note="Đã ghi",
        doc_note="Đã thêm báo cáo vào cuối Google Doc.",
    )


def test_append_inserts_vietnamese_report_at_document_end() -> None:
    gateway = MemoryDocs()
    service = DocsService(gateway, "doc-id", dry_run=False, secret="sxn_live_test_key_value")
    text = render_report(_report(), secrets=["sxn_live_test_key_value"])
    service.append_report(text)

    assert gateway.reads == 1
    request = gateway.requests[0]["insertText"]
    assert request["location"]["index"] == 1
    body = request["text"]
    assert "BÁO CÁO ĐỒNG BỘ SHOP XOÀI NON" in body
    assert "Số sản phẩm: 2" in body
    assert "Mua hàng tự động: tắt." in body
    assert "sxn_live_test_key_value" not in body
    assert "***" in body


def test_dry_run_does_not_call_docs_api() -> None:
    gateway = MemoryDocs()
    service = DocsService(gateway, "doc-id", dry_run=True, secret="sxn_live_test_key_value")
    service.append_report("BÁO CÁO ĐỒNG BỘ SHOP XOÀI NON")

    assert gateway.reads == 0
    assert gateway.requests == []


def test_managed_region_replaces_in_place_and_keeps_user_text() -> None:
    from shop_integration.docs_service import REPORT_BEGIN, REPORT_END

    gateway = MemoryDocs()
    gateway.plain = "Giữ nguyên người dùng.\n"
    service = DocsService(gateway, "doc-id", dry_run=False, secret="sxn_live_test_key_value")
    first = "BÁO CÁO ĐỒNG BỘ SHOP XOÀI NON\nMã nội dung: abc\nSố sản phẩm: 1"
    assert service.region_state("abc") == "absent"
    assert service.replace_managed_report(first) == "written"
    assert gateway.plain.startswith("Giữ nguyên người dùng.")
    assert gateway.plain.count(REPORT_BEGIN) == 1
    assert gateway.plain.count(REPORT_END) == 1
    assert service.region_state("abc") == "current"

    second = "BÁO CÁO ĐỒNG BỘ SHOP XOÀI NON\nMã nội dung: def\nSố sản phẩm: 2"
    assert service.replace_managed_report(second) == "written"
    assert gateway.plain.startswith("Giữ nguyên người dùng.")
    assert gateway.plain.count(REPORT_BEGIN) == 1
    assert "Số sản phẩm: 2" in gateway.plain
    assert "Số sản phẩm: 1" not in gateway.plain
    assert "sxn_live_test_key_value" not in gateway.plain


def test_customer_region_does_not_replace_the_shop_report() -> None:
    from shop_integration.docs_service import CUSTOMER_BEGIN, REPORT_BEGIN, REPORT_END

    gateway = MemoryDocs()
    gateway.plain = f"Ngoài vùng.\n{REPORT_BEGIN}\nBáo cáo\n{REPORT_END}\n"
    service = DocsService(gateway, "doc-id", dry_run=False, secret="sxn_live_test_key_value")
    text = "BẢNG GIÁ\nMã nội dung: gia-1\nSản phẩm A\nGiá bán: 13000 VND\nTình trạng: Còn hàng"
    assert service.replace_customer_prices(text, "gia-1") == "written"
    assert gateway.plain.count(REPORT_BEGIN) == 1
    assert gateway.plain.count(CUSTOMER_BEGIN) == 1
    assert "Ngoài vùng." in gateway.plain
    assert "Báo cáo" in gateway.plain
    assert service.replace_customer_prices(text, "gia-1") == "skipped"
    assert gateway.requests[-1]["insertText"]["text"].count("BẢNG GIÁ") == 1


def test_half_marker_does_not_overwrite_document() -> None:
    from shop_integration.docs_service import REPORT_BEGIN
    from shop_integration.errors import GoogleWorkspaceError

    gateway = MemoryDocs()
    gateway.plain = "Giữ nguyên\n" + REPORT_BEGIN + "\n"
    service = DocsService(gateway, "doc-id", dry_run=False)
    with pytest.raises(GoogleWorkspaceError, match="một cặp định danh"):
        service.region_state("abc")
    assert gateway.requests == []
    assert gateway.plain.startswith("Giữ nguyên")


def test_quote_form_uses_selling_price_and_builds_a_table() -> None:
    from shop_integration.quote_form import (
        find_quote_table,
        pipe_section_range,
        table_delete_requests,
        table_fill_requests,
    )
    from shop_integration.report_service import render_customer_quote

    text = render_customer_quote(
        [
            {
                "Product ID": "local_1",
                "Tên sản phẩm đã decode": "ChatGPT Plus",
                "Giá gốc thấp nhất": 168000,
                "Giá gốc cao nhất": 168000,
                "Giá bán thấp nhất": 203000,
                "Giá bán cao nhất": 203000,
                "Trạng thái hàng": "Còn hàng",
            }
        ],
        updated_at="2026-11-23T18:41:14+07:00",
        content_id="abc",
        secrets=["sxn_live_test_key_value"],
    )
    assert "BẢNG BÁO GIÁ" in text
    assert "Shop Xoài Non" not in text
    assert "ChatGPT" not in text
    assert "203.000" not in text
    assert "168000" not in text
    assert "local_1" not in text
    assert "giá gốc" not in text.lower()
    assert "lợi nhuận" not in text.lower()
    assert "Mẫu báo giá: 4" in text
    assert "sxn_live_test_key_value" not in text

    def paragraph(start: int, content: str) -> dict:
        end = start + len(content)
        return {
            "startIndex": start,
            "endIndex": end,
            "paragraph": {
                "elements": [
                    {"startIndex": start, "endIndex": end, "textRun": {"content": content}}
                ]
            },
        }

    header = "STT | Sản phẩm | Giá bán | Tình trạng\n"
    document = {
        "body": {
            "content": [
                paragraph(1, "BẢNG BÁO GIÁ\n"),
                paragraph(10, header),
                paragraph(40, "1 | ChatGPT Plus | 203.000 | Còn hàng\n"),
                paragraph(80, "Mã nội dung: abc\n"),
            ]
        }
    }
    assert pipe_section_range(document) == (10, 80)
    assert table_delete_requests([(5, 9), (20, 40)])[0]["deleteContentRange"]["range"]["startIndex"] == 20

    def cell(start: int) -> dict:
        return {"content": [{"startIndex": start, "paragraph": {"elements": []}}]}

    table = {
        "startIndex": 10,
        "table": {
            "tableRows": [
                {"tableCells": [cell(101), cell(111), cell(121), cell(131)]},
                {"tableCells": [cell(201), cell(211), cell(221), cell(231)]},
            ]
        },
    }
    requests = table_fill_requests(table, [["1", "ChatGPT Plus", "203.000", "Còn hàng"]])
    inserts = [item["insertText"] for item in requests if "insertText" in item]
    assert [item["location"]["index"] for item in inserts] == [231, 221, 211, 201, 131, 121, 111, 101]
    assert inserts[1]["text"] == "203.000"
    blob = str(requests)
    assert "local_" not in blob
    assert "168000" not in blob
    styled = document["body"]["content"] + [table]
    assert find_quote_table({"body": {"content": styled}}) == table
    price_insert = next(item for item in requests if item.get("insertText", {}).get("text") == "203.000")
    price_style = requests[requests.index(price_insert) + 1]["updateTextStyle"]["textStyle"]
    assert price_style["bold"] is True
    status_insert = next(item for item in requests if item.get("insertText", {}).get("text") == "Còn hàng")
    status_color = requests[requests.index(status_insert) + 1]["updateTextStyle"]["textStyle"][
        "foregroundColor"
    ]["color"]["rgbColor"]
    assert status_color["green"] > status_color["red"]


def test_manual_section_survives_the_next_quote_write() -> None:
    from shop_integration.report_service import render_customer_quote

    gateway = MemoryDocs()
    service = DocsService(gateway, "doc-id", dry_run=False)
    first = render_customer_quote([], updated_at="2026-11-23T10:00:00+07:00", content_id="abc")
    assert service.replace_customer_quote(first, [["1", "ChatGPT", "203.000", "Còn hàng"]]) == "written"
    assert "ChatGPT" not in gateway.plain
    assert "BẢNG BÁO GIÁ" in gateway.plain
    gateway.plain = gateway.plain.replace(
        "PHAN_SUA_TAY_BEGIN\n\nPHAN_SUA_TAY_END",
        "PHAN_SUA_TAY_BEGIN\nGIU_NOI_DUNG_TAY\nPHAN_SUA_TAY_END",
    )
    second = render_customer_quote([], updated_at="2026-11-23T11:00:00+07:00", content_id="def")
    assert service.replace_customer_quote(second, []) == "written"
    assert "GIU_NOI_DUNG_TAY" in gateway.plain
    assert gateway.plain.count("PHAN_SUA_TAY_BEGIN") == 1
    assert "Mã nội dung: def" in gateway.plain
    assert "Mã nội dung: abc" not in gateway.plain


def test_private_key_marker_is_refused() -> None:
    gateway = MemoryDocs()
    service = DocsService(gateway, "doc-id", dry_run=False)
    with pytest.raises(GoogleWorkspaceError):
        service.append_report("BEGIN PRIVATE KEY")

    assert gateway.requests == []
