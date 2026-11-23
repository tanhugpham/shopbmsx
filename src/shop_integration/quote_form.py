"""Báo giá khách trên Google Docs: tiêu đề, bảng và giá bán đã định dạng.

Không chứa giá gốc, lợi nhuận, mã sản phẩm hay khóa API.
"""

from __future__ import annotations

from typing import Any

from shop_integration.pricing import round_pretty_vnd

LAYOUT_MARKER = "Mẫu báo giá: 4"
MANUAL_BEGIN = "PHAN_SUA_TAY_BEGIN"
MANUAL_END = "PHAN_SUA_TAY_END"
TABLE_HEADER = ["STT", "Sản phẩm", "Giá bán", "Tình trạng"]
HEADER_LINE = " | ".join(TABLE_HEADER)

_TITLE = "BẢNG BÁO GIÁ"
_SHOP = "Shop Xoài Non"
_CONTENT_PREFIX = "Mã nội dung:"


def manual_section_text() -> str:
    """Phần người dùng sửa. Nằm sau vùng tool ghi và không bị thay."""

    return f"\n{MANUAL_BEGIN}\n\n{MANUAL_END}\n"


def display_updated(updated_at: str) -> str:
    """2026-11-23T18:41:14+07:00 thành 23/11/2026 18:41. Chuỗi khác giữ nguyên."""

    text = updated_at.strip()
    if len(text) >= 16 and text[4] == "-" and text[7] == "-" and text[10] == "T":
        return f"{text[8:10]}/{text[5:7]}/{text[0:4]} {text[11:16]}"
    return text


def format_vnd(value: object) -> str:
    """203000 thành 203.000. Không nhận giá gốc."""

    if isinstance(value, bool) or not isinstance(value, int):
        if isinstance(value, str) and value.isdigit():
            value = int(value)
        else:
            return "Chưa có dữ liệu"
    return f"{value:,}".replace(",", ".")


def format_selling(record: dict[str, Any]) -> str:
    low = record.get("Giá bán thấp nhất", "")
    high = record.get("Giá bán cao nhất", "")
    if low in ("", None) and high in ("", None):
        return "Chưa có dữ liệu"
    if high in ("", None) or low == high:
        return _pretty_vnd(low)
    if low in ("", None):
        return _pretty_vnd(high)
    return f"{_pretty_vnd(low)} – {_pretty_vnd(high)}"


def _pretty_vnd(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        if isinstance(value, str) and value.isdigit():
            value = int(value)
        else:
            return "Chưa có dữ liệu"
    return format_vnd(round_pretty_vnd(value))


def one_line(value: object) -> str:
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    return text or "Chưa có dữ liệu"


def quote_rows(records: list[dict[str, Any]]) -> list[list[str]]:
    """Mỗi dòng là STT, tên, giá bán, tình trạng. Không có mã sản phẩm."""

    rows: list[list[str]] = []
    for index, record in enumerate(records, start=1):
        name = record.get("Tên sản phẩm đã decode") or record.get("Tên sản phẩm") or "Chưa có dữ liệu"
        stock = record.get("Trạng thái hàng") or record.get("Trạng thái còn hàng") or "Chưa có dữ liệu"
        rows.append([str(index), one_line(name), format_selling(record), one_line(stock)])
    return rows


def quote_lines(records: list[dict[str, Any]], *, updated_at: str, content_id: str) -> list[str]:
    """Chỉ tiêu đề bảng báo giá. Không đưa tên shop gốc hay danh sách sản phẩm."""

    del records, updated_at
    lines = [_TITLE]
    if content_id:
        lines.extend([f"{_CONTENT_PREFIX} {content_id}", LAYOUT_MARKER])
    return lines


def paragraph_line(element: dict[str, Any]) -> str | None:
    """Một đoạn một dòng. Đoạn gộp nhiều dòng thì bỏ, để không đoán nhầm."""

    if "paragraph" not in element:
        return None
    parts: list[str] = []
    for run in element.get("paragraph", {}).get("elements", []):
        if not isinstance(run, dict):
            continue
        text_run = run.get("textRun")
        if not isinstance(text_run, dict):
            continue
        content = text_run.get("content")
        if isinstance(content, str):
            parts.append(content)
    raw = "".join(parts)
    if not raw:
        return None
    body = raw[:-1] if raw.endswith("\n") else raw
    if "\n" in body:
        return None
    return body


def _body(document: dict[str, Any]) -> list[Any]:
    content = document.get("body", {}).get("content", [])
    return content if isinstance(content, list) else []


def find_exact_paragraph(document: dict[str, Any], line: str) -> tuple[int, int] | None:
    found: list[tuple[int, int]] = []
    for element in _body(document):
        if not isinstance(element, dict) or paragraph_line(element) != line:
            continue
        start = element.get("startIndex")
        end = element.get("endIndex")
        if isinstance(start, int) and isinstance(end, int) and end > start:
            found.append((start, end))
    if len(found) != 1:
        return None
    return found[0]


def find_prefix_paragraph(document: dict[str, Any], prefix: str) -> tuple[int, int] | None:
    found: list[tuple[int, int]] = []
    for element in _body(document):
        if not isinstance(element, dict):
            continue
        line = paragraph_line(element)
        if line is None or not line.startswith(prefix):
            continue
        start = element.get("startIndex")
        end = element.get("endIndex")
        if isinstance(start, int) and isinstance(end, int) and end > start:
            found.append((start, end))
    if len(found) != 1:
        return None
    return found[0]


def pipe_section_range(document: dict[str, Any]) -> tuple[int, int] | None:
    """Từ dòng tiêu đề cột tới đúng trước mã nội dung."""

    header = find_exact_paragraph(document, HEADER_LINE)
    footer = find_prefix_paragraph(document, _CONTENT_PREFIX)
    if header is None or footer is None or footer[0] <= header[0]:
        return None
    return header[0], footer[0]


def tables_inside(document: dict[str, Any], start: int, end: int) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for element in _body(document):
        if not isinstance(element, dict) or "table" not in element:
            continue
        table_start = element.get("startIndex")
        table_end = element.get("endIndex")
        if (
            isinstance(table_start, int)
            and isinstance(table_end, int)
            and table_end > table_start
            and table_start >= start
            and table_end <= end
        ):
            ranges.append((table_start, table_end))
    return ranges


def table_delete_requests(ranges: list[tuple[int, int]]) -> list[dict[str, Any]]:
    ordered = sorted(ranges, key=lambda item: item[0], reverse=True)
    return [
        {"deleteContentRange": {"range": {"startIndex": start, "endIndex": end}}}
        for start, end in ordered
    ]


def find_quote_table(document: dict[str, Any]) -> dict[str, Any] | None:
    title = find_exact_paragraph(document, _TITLE)
    footer = find_prefix_paragraph(document, _CONTENT_PREFIX)
    if title is None or footer is None:
        return None
    found: list[dict[str, Any]] = []
    for element in _body(document):
        if not isinstance(element, dict) or "table" not in element:
            continue
        start = element.get("startIndex")
        if isinstance(start, int) and title[0] < start < footer[0]:
            found.append(element)
    if len(found) != 1:
        return None
    return found[0]


def _rgb(red: int, green: int, blue: int) -> dict[str, float]:
    return {"red": red / 255, "green": green / 255, "blue": blue / 255}


def _solid(red: int, green: int, blue: int) -> dict[str, Any]:
    return {
        "color": {"color": {"rgbColor": _rgb(red, green, blue)}},
        "width": {"magnitude": 0.5, "unit": "PT"},
        "dashStyle": "SOLID",
    }


def _text_style(start: int, end: int, *, bold: bool, size: float, color: dict[str, float]) -> dict[str, Any]:
    return {
        "updateTextStyle": {
            "range": {"startIndex": start, "endIndex": end},
            "textStyle": {
                "bold": bold,
                "fontSize": {"magnitude": size, "unit": "PT"},
                "foregroundColor": {"color": {"rgbColor": color}},
                "weightedFontFamily": {"fontFamily": "Arial"},
            },
            "fields": "bold,fontSize,foregroundColor,weightedFontFamily",
        }
    }


def _align(start: int, end: int, alignment: str, *, above: float = 0, below: float = 0) -> dict[str, Any]:
    return {
        "updateParagraphStyle": {
            "range": {"startIndex": start, "endIndex": end},
            "paragraphStyle": {
                "alignment": alignment,
                "spaceAbove": {"magnitude": above, "unit": "PT"},
                "spaceBelow": {"magnitude": below, "unit": "PT"},
            },
            "fields": "alignment,spaceAbove,spaceBelow",
        }
    }


def _visible_range(element: dict[str, Any]) -> tuple[int, int] | None:
    line = paragraph_line(element)
    start = element.get("startIndex")
    if line is None or not line or not isinstance(start, int):
        return None
    return start, start + len(line)


def heading_style_requests(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Tô tiêu đề và dòng cuối. Không đụng chữ ngoài các dòng đã biết."""

    green = _rgb(31, 77, 58)
    muted = _rgb(107, 114, 128)
    brown = _rgb(92, 74, 50)
    requests: list[dict[str, Any]] = []
    for element in _body(document):
        if not isinstance(element, dict):
            continue
        line = paragraph_line(element)
        span = _visible_range(element)
        if line is None or span is None:
            continue
        start, end = span
        if line == _TITLE:
            requests.append(_align(start, end, "CENTER", above=6, below=0))
            requests.append(_text_style(start, end, bold=True, size=22, color=green))
        elif line == _SHOP:
            requests.append(_align(start, end, "CENTER", below=0))
            requests.append(_text_style(start, end, bold=False, size=12, color=brown))
        elif line.startswith("Cập nhật:"):
            requests.append(_align(start, end, "CENTER", below=8))
            requests.append(_text_style(start, end, bold=False, size=10, color=muted))
        elif line.startswith(_CONTENT_PREFIX) or line == LAYOUT_MARKER:
            requests.append(_align(start, end, "START", above=8 if line.startswith(_CONTENT_PREFIX) else 0))
            requests.append(_text_style(start, end, bold=False, size=8, color=muted))
        elif line in {"SHOP_XOAI_NON_BAO_CAO_BEGIN", "SHOP_XOAI_NON_BAO_CAO_END"}:
            requests.append(_text_style(start, end, bold=False, size=8, color=muted))
    return requests


def _cell_starts(table_element: dict[str, Any]) -> list[list[int]]:
    from shop_integration.errors import GoogleWorkspaceError

    rows: list[list[int]] = []
    for row in table_element.get("table", {}).get("tableRows", []):
        if not isinstance(row, dict):
            continue
        starts: list[int] = []
        for cell in row.get("tableCells", []):
            if not isinstance(cell, dict):
                continue
            content = cell.get("content")
            if not isinstance(content, list) or not content or not isinstance(content[0], dict):
                raise GoogleWorkspaceError("Ô bảng báo giá không có đoạn văn. Không ghi đè ngoài vùng.")
            start = content[0].get("startIndex")
            if not isinstance(start, int):
                raise GoogleWorkspaceError("Ô bảng báo giá thiếu chỉ số. Không ghi đè ngoài vùng.")
            starts.append(start)
        rows.append(starts)
    return rows


def _stock_color(stock: str) -> dict[str, float]:
    if stock == "Còn hàng":
        return _rgb(15, 122, 69)
    if stock == "Hết hàng":
        return _rgb(180, 35, 24)
    return _rgb(92, 74, 50)


def table_fill_requests(table_element: dict[str, Any], rows: list[list[str]]) -> list[dict[str, Any]]:
    """Điền và tô bảng. Chèn từ ô cuối để chỉ số phía trước không bị đẩy."""

    from shop_integration.errors import GoogleWorkspaceError

    table_start = table_element.get("startIndex")
    if not isinstance(table_start, int):
        raise GoogleWorkspaceError("Bảng báo giá thiếu vị trí. Không ghi đè ngoài vùng.")
    grid = [list(TABLE_HEADER), *rows]
    starts = _cell_starts(table_element)
    width_ok = len(starts) == len(grid) and all(
        len(item) == 4 and len(row) == 4 for item, row in zip(starts, grid, strict=True)
    )
    if not width_ok:
        raise GoogleWorkspaceError("Bảng báo giá không đủ ô. Không ghi đè ngoài vùng.")

    requests: list[dict[str, Any]] = []
    border = _solid(231, 224, 212)
    for row_index in range(len(grid)):
        if row_index == 0:
            color = _rgb(31, 77, 58)
        elif row_index % 2 == 0:
            color = _rgb(248, 244, 236)
        else:
            color = _rgb(255, 255, 255)
        requests.append(
            {
                "updateTableCellStyle": {
                    "tableRange": {
                        "tableCellLocation": {
                            "tableStartLocation": {"index": table_start},
                            "rowIndex": row_index,
                            "columnIndex": 0,
                        },
                        "rowSpan": 1,
                        "columnSpan": 4,
                    },
                    "tableCellStyle": {
                        "backgroundColor": {"color": {"rgbColor": color}},
                        "borderTop": border,
                        "borderBottom": border,
                        "borderLeft": border,
                        "borderRight": border,
                        "paddingTop": {"magnitude": 5, "unit": "PT"},
                        "paddingBottom": {"magnitude": 5, "unit": "PT"},
                        "paddingLeft": {"magnitude": 6, "unit": "PT"},
                        "paddingRight": {"magnitude": 6, "unit": "PT"},
                        "contentAlignment": "MIDDLE",
                    },
                    "fields": (
                        "backgroundColor,borderTop,borderBottom,borderLeft,borderRight,"
                        "paddingTop,paddingBottom,paddingLeft,paddingRight,contentAlignment"
                    ),
                }
            }
        )
    widths = (36, 214, 100, 90)
    for column, width in enumerate(widths):
        requests.append(
            {
                "updateTableColumnProperties": {
                    "tableStartLocation": {"index": table_start},
                    "columnIndices": [column],
                    "tableColumnProperties": {
                        "widthType": "FIXED_WIDTH",
                        "width": {"magnitude": width, "unit": "PT"},
                    },
                    "fields": "widthType,width",
                }
            }
        )

    indexed: list[tuple[int, int, int, str]] = []
    for row_index, row in enumerate(grid):
        for column, value in enumerate(row):
            indexed.append((starts[row_index][column], row_index, column, value))
    indexed.sort(key=lambda item: item[0], reverse=True)
    alignments = ("CENTER", "START", "END", "CENTER")
    dark = _rgb(31, 41, 51)
    green = _rgb(31, 77, 58)
    white = _rgb(255, 255, 255)
    for index, row_index, column, value in indexed:
        end = index + len(value)
        requests.append({"insertText": {"location": {"index": index}, "text": value}})
        if row_index == 0:
            color = white
            bold = True
            alignment = "CENTER"
        elif column == 2:
            color = green
            bold = True
            alignment = alignments[column]
        elif column == 3:
            color = _stock_color(value)
            bold = True
            alignment = alignments[column]
        else:
            color = dark
            bold = False
            alignment = alignments[column]
        requests.append(_text_style(index, end, bold=bold, size=10, color=color))
        requests.append(_align(index, end, alignment))
    return requests
