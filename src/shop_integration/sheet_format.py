"""Định dạng tab Google Sheets dùng chung.

Một bộ request cho header, đóng băng, lọc, dải màu, tiền tệ và tồn kho.
Không chứa dữ liệu nghiệp vụ và không chứa bí mật.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from shop_integration.sheet_layout import SheetTable

HEADER_BLUE = {"red": 0.05, "green": 0.16, "blue": 0.36}
_CURRENCY = {"Retail Cost (VND)", "Wholesale Unit Price", "Giá cũ (VND)", "Giá mới (VND)"}
_WIDTHS = {
    "Product Name": 280,
    "Description": 320,
    "Product Description": 320,
    "Availability": 150,
    "Last Updated": 180,
    "Chi tiết": 360,
}


def build_format_requests(
    sheet_id: int,
    headers: list[str],
    row_count: int,
    *,
    banding_ids: list[int] | None = None,
    conditional_format_count: int = 0,
) -> list[dict[str, Any]]:
    """Tô header, đóng băng, lọc, dải màu, tiền tệ và tồn kho. Không chứa bí mật."""

    requests: list[dict[str, Any]] = []
    for band_id in banding_ids or []:
        requests.append({"deleteBanding": {"bandedRangeId": band_id}})
    for index in range(conditional_format_count - 1, -1, -1):
        requests.append({"deleteConditionalFormatRule": {"sheetId": sheet_id, "index": index}})
    columns = max(len(headers), 1)
    end_row = max(row_count, 2)
    header_range = {
        "sheetId": sheet_id,
        "startRowIndex": 0,
        "endRowIndex": 1,
        "startColumnIndex": 0,
        "endColumnIndex": columns,
    }
    requests.append(
        {
            "repeatCell": {
                "range": header_range,
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": HEADER_BLUE,
                        "textFormat": {
                            "bold": True,
                            "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                        },
                        "wrapStrategy": "WRAP",
                        "horizontalAlignment": "CENTER",
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,wrapStrategy,horizontalAlignment)",
            }
        }
    )
    requests.append(
        {
            "updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount",
            }
        }
    )
    requests.append(
        {
            "setBasicFilter": {
                "filter": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": end_row,
                        "startColumnIndex": 0,
                        "endColumnIndex": columns,
                    }
                }
            }
        }
    )
    requests.append(
        {
            "addBanding": {
                "bandedRange": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": end_row,
                        "startColumnIndex": 0,
                        "endColumnIndex": columns,
                    },
                    "rowProperties": {
                        "headerColor": HEADER_BLUE,
                        "firstBandColor": {"red": 1, "green": 1, "blue": 1},
                        "secondBandColor": {"red": 0.90, "green": 0.94, "blue": 0.98},
                    },
                }
            }
        }
    )
    if end_row > 1:
        requests.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": end_row,
                        "startColumnIndex": 0,
                        "endColumnIndex": columns,
                    },
                    "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
                    "fields": "userEnteredFormat.wrapStrategy",
                }
            }
        )
    for name in _CURRENCY:
        if name not in headers:
            continue
        column = headers.index(name)
        requests.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": end_row,
                        "startColumnIndex": column,
                        "endColumnIndex": column + 1,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "numberFormat": {"type": "NUMBER", "pattern": "#,##0"}
                        }
                    },
                    "fields": "userEnteredFormat.numberFormat",
                }
            }
        )
    if "Stock Quantity" in headers and end_row > 1:
        column = headers.index("Stock Quantity")
        stock_range = {
            "sheetId": sheet_id,
            "startRowIndex": 1,
            "endRowIndex": end_row,
            "startColumnIndex": column,
            "endColumnIndex": column + 1,
        }
        requests.append(_stock_rule(stock_range, "NUMBER_EQ", "0", 0.96, 0.80, 0.80))
        requests.append(_stock_rule(stock_range, "NUMBER_GREATER", "0", 0.80, 0.93, 0.80))
    for index, header in enumerate(headers):
        requests.append(
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": index,
                        "endIndex": index + 1,
                    },
                    "properties": {"pixelSize": _WIDTHS.get(header, 150)},
                    "fields": "pixelSize",
                }
            }
        )
    return requests




def _stock_rule(
    cell_range: dict[str, int],
    condition: str,
    value: str,
    red: float,
    green: float,
    blue: float,
) -> dict[str, Any]:
    return {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [cell_range],
                "booleanRule": {
                    "condition": {"type": condition, "values": [{"userEnteredValue": value}]},
                    "format": {"backgroundColor": {"red": red, "green": green, "blue": blue}},
                },
            },
            "index": 0,
        }
    }


def apply_tab_formatting(
    gateway: Any,
    spreadsheet_id: str,
    header_of: dict[str, list[str]],
    read_table: Callable[[str], SheetTable],
) -> None:
    """Áp định dạng cho các tab đã có. Gateway không hỗ trợ meta thì bỏ qua."""

    meta_fn = getattr(gateway, "list_sheet_meta", None)
    update_fn = getattr(gateway, "batch_update", None)
    if meta_fn is None or update_fn is None:
        return
    meta = {
        str(item.get("title")): item
        for item in meta_fn(spreadsheet_id)
        if isinstance(item, dict)
    }
    requests: list[dict[str, Any]] = []
    for title, headers in header_of.items():
        info = meta.get(title)
        sheet_id = info.get("sheetId") if isinstance(info, dict) else None
        if not isinstance(sheet_id, int):
            continue
        table = read_table(title)
        requests.extend(
            build_format_requests(
                sheet_id,
                headers,
                max(table.row_count, 1),
                banding_ids=list(info.get("bandingIds") or []),
                conditional_format_count=int(info.get("conditionalFormatCount") or 0),
            )
        )
    if requests:
        update_fn(spreadsheet_id, requests)
