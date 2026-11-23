"""Spreadsheet riêng của Nastele. Không ghi tab Shop Xoài Non và không lưu API key."""

from __future__ import annotations

import json
from typing import Any

from shop_integration.backup_service import redact_payload
from shop_integration.config import PROJECT_ROOT
from shop_integration.errors import BackupError, ConfigError
from shop_integration.time_utils import format_timestamp, now_vietnam
from shop_integration.logging_utils import redact
from shop_integration.product_service import ABSENT_LABEL, MISSING_TEXT, NULL_LABEL
from shop_integration.sheet_layout import (
    ALL_TABS,
    SheetTable,
    grid_from_records,
    merge_config,
    records_from_grid,
    stamp_stt,
)
from shop_integration.suppliers.models import SUPPLIER_NASTELE, NormalizedOffer
from shop_integration.text_normalize import FORMULA_PREFIX, guard_formula, public_description

TAB_PRODUCTS = "NASTELE_SAN_PHAM"
TAB_TIERS = "NASTELE_GIA_SI"
TAB_HISTORY = "NASTELE_LICH_SU_GIA"
TAB_LOG = "NASTELE_NHAT_KY"
TAB_CONFIG = "NASTELE_CAU_HINH"
NASTELE_TABS = (TAB_PRODUCTS, TAB_TIERS, TAB_HISTORY, TAB_LOG, TAB_CONFIG)
PROTECTED_TAB = "Trang tính1"

PRODUCT_HEADERS = [
    "STT",
    "Product ID",
    "Product Name",
    "Description",
    "Category ID",
    "Retail Cost (VND)",
    "Stock Quantity",
    "Availability",
    "Number of Wholesale Tiers",
    "Last Updated",
]
TIER_HEADERS = [
    "Product ID",
    "Product Name",
    "Minimum Quantity",
    "Wholesale Unit Price",
    "Last Updated",
]
HISTORY_HEADERS = [
    "Thời điểm",
    "Product ID",
    "Giá cũ (VND)",
    "Giá mới (VND)",
]
LOG_HEADERS = ["Thời điểm", "Sự kiện", "Chi tiết"]
CONFIG_HEADERS = ["Khóa", "Giá trị"]

_ID_COLUMNS = {"Product ID", "Khóa"}
_CURRENCY = {"Retail Cost (VND)", "Wholesale Unit Price", "Giá cũ (VND)", "Giá mới (VND)"}
HEADER_BLUE = {"red": 0.05, "green": 0.16, "blue": 0.36}
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


class NasteleSheetWriter:
    def __init__(
        self,
        gateway: Any,
        spreadsheet_id: str,
        *,
        dry_run: bool,
        secrets: list[str] | None = None,
    ) -> None:
        if not str(spreadsheet_id).strip():
            raise ConfigError("Thiếu NASTELE_SPREADSHEET_ID.")
        self.gateway = gateway
        self.spreadsheet_id = str(spreadsheet_id).strip()
        self.dry_run = dry_run
        self._secrets = [secret for secret in (secrets or []) if len(secret) >= 8]
        self.tabs_to_create: list[str] = []

    def prepare(self) -> list[str]:
        for title in NASTELE_TABS:
            self._ensure(title)
        return list(self.tabs_to_create)

    def apply(
        self,
        offers: list[NormalizedOffer],
        *,
        updated_at: str,
        config_pairs: list[tuple[str, str]],
        failed: bool,
        failure_message: str = "",
    ) -> dict[str, int]:
        if self.dry_run:
            if not failed:
                self.prepare()
            return {"products": 0, "tiers": 0, "preserved": 1 if failed else 0}
        if failed:
            self._append_log(updated_at, "CẢNH BÁO", failure_message or "Giữ dữ liệu Nastele cũ. Không ghi tồn kho 0.")
            return {"products": 0, "tiers": 0, "preserved": 1}
        self.prepare()
        own = [offer for offer in offers if offer.supplier_id == SUPPLIER_NASTELE]
        existing_products = self._read(TAB_PRODUCTS)
        existing_tiers = self._read(TAB_TIERS)
        product_rows = [_product_row(offer, updated_at) for offer in own]
        history = _price_history(existing_products, product_rows, updated_at)
        product_grid = _merge_products(existing_products, product_rows)
        tier_grid = _merge_tiers(existing_tiers, _tier_rows(own, updated_at), {row["Product ID"] for row in product_rows})
        self._write(TAB_PRODUCTS, product_grid)
        self._write(TAB_TIERS, tier_grid)
        self._append_rows(TAB_HISTORY, HISTORY_HEADERS, history)
        self._write(TAB_CONFIG, merge_config(self._read(TAB_CONFIG), _safe_config(config_pairs)))
        self._append_log(updated_at, "ĐỒNG BỘ", f"{len(product_rows)} sản phẩm")
        self._format_tabs()
        return {"products": len(product_rows), "tiers": len(tier_grid) - 1, "preserved": 0}

    def _read(self, tab: str) -> SheetTable:
        _assert_nastele_tab(tab)
        titles = set(self.gateway.list_sheet_titles(self.spreadsheet_id))
        if tab not in titles:
            return SheetTable()
        return records_from_grid(self.gateway.get_values(self.spreadsheet_id, f"'{tab}'!A:ZZ"))

    def _ensure(self, title: str) -> None:
        _assert_nastele_tab(title)
        titles = set(self.gateway.list_sheet_titles(self.spreadsheet_id))
        if title in titles:
            return
        if self.dry_run:
            if title not in self.tabs_to_create:
                self.tabs_to_create.append(title)
            return
        self.gateway.add_sheet(self.spreadsheet_id, title)
        self.tabs_to_create.append(title)

    def _write(self, tab: str, grid: list[list[Any]]) -> None:
        _assert_nastele_tab(tab)
        if self.dry_run:
            return
        self.gateway.update_values(
            self.spreadsheet_id,
            f"'{tab}'!A1",
            _guard_grid(grid, self._secrets),
        )

    def _append_rows(self, tab: str, headers: list[str], rows: list[dict[str, Any]]) -> None:
        if not rows or self.dry_run:
            return
        _assert_nastele_tab(tab)
        existing = self._read(tab)
        if not existing.headers:
            grid = grid_from_records(headers, rows)
        else:
            grid = grid_from_records(existing.headers, [*existing.records, *rows])
        self._write(tab, grid)

    def _append_log(self, updated_at: str, event: str, detail: str) -> None:
        self._ensure(TAB_LOG)
        if self.dry_run:
            return
        safe_detail = redact(detail, self._secrets)
        self._append_rows(
            TAB_LOG,
            LOG_HEADERS,
            [{"Thời điểm": updated_at, "Sự kiện": event, "Chi tiết": safe_detail}],
        )

    def _format_tabs(self) -> None:
        if self.dry_run:
            return
        meta_fn = getattr(self.gateway, "list_sheet_meta", None)
        update_fn = getattr(self.gateway, "batch_update", None)
        if meta_fn is None or update_fn is None:
            return
        meta = {item["title"]: item for item in meta_fn(self.spreadsheet_id)}
        header_of = {
            TAB_PRODUCTS: PRODUCT_HEADERS,
            TAB_TIERS: TIER_HEADERS,
            TAB_HISTORY: HISTORY_HEADERS,
            TAB_LOG: LOG_HEADERS,
            TAB_CONFIG: CONFIG_HEADERS,
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


def snapshot_nastele_values(gateway: Any, spreadsheet_id: str, secrets: list[str]) -> str:
    """Đọc toàn bộ tab hiện có trước khi ghi. Không ghi file Google."""

    try:
        stamp = format_timestamp(now_vietnam()).replace(":", "")
        folder = PROJECT_ROOT / "backups" / f"nastele-{stamp}"
        folder.mkdir(parents=True, exist_ok=True)
        titles = list(gateway.list_sheet_titles(spreadsheet_id))
        payload: dict[str, Any] = {"tabs": {}}
        for title in titles:
            payload["tabs"][title] = gateway.get_values(spreadsheet_id, f"'{title}'!A:ZZ")
        target = folder / "values.json"
        target.write_text(
            json.dumps(redact_payload(payload, secrets), ensure_ascii=False),
            encoding="utf-8",
        )
    except BackupError:
        raise
    except Exception:
        raise BackupError("Không sao lưu được spreadsheet Nastele. Không ghi đè dữ liệu Nastele.") from None
    return str(folder)


def _assert_nastele_tab(tab: str) -> None:
    if tab == PROTECTED_TAB or tab in ALL_TABS or tab not in NASTELE_TABS:
        raise ConfigError(f"Từ chối ghi tab '{tab}' trên spreadsheet Nastele.")


def _product_row(offer: NormalizedOffer, updated_at: str) -> dict[str, Any]:
    stock: Any = ""
    if offer.quantity_presence == "value":
        stock = offer.available_quantity
    return {
        "Product ID": offer.source_product_id,
        "Product Name": offer.normalized_product_name or MISSING_TEXT,
        "Description": public_description(offer.description) or MISSING_TEXT,
        "Category ID": offer.category_id or MISSING_TEXT,
        "Retail Cost (VND)": offer.cost_price_vnd if offer.cost_price_vnd is not None else "",
        "Stock Quantity": stock,
        "Availability": _availability(offer),
        "Number of Wholesale Tiers": len(offer.wholesale_tiers),
        "Last Updated": updated_at,
    }


def _tier_rows(offers: list[NormalizedOffer], updated_at: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for offer in offers:
        name = offer.normalized_product_name or MISSING_TEXT
        for tier in offer.wholesale_tiers:
            rows.append(
                {
                    "Product ID": offer.source_product_id,
                    "Product Name": name,
                    "Minimum Quantity": tier.min_quantity,
                    "Wholesale Unit Price": tier.unit_price_vnd,
                    "Last Updated": updated_at,
                }
            )
    return rows


def _availability(offer: NormalizedOffer) -> str:
    if offer.available is True:
        return "Còn hàng"
    if offer.available is False:
        return "Hết hàng"
    if offer.available_presence == "null":
        return NULL_LABEL
    if offer.available_presence == "absent":
        return ABSENT_LABEL
    return "Không rõ"


def _merge_products(existing: SheetTable, fresh: list[dict[str, Any]]) -> list[list[Any]]:
    headers = _preferred_headers(existing.headers, PRODUCT_HEADERS)
    fresh_by_id = {_cell_id(row.get("Product ID")): row for row in fresh}
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for old in existing.records:
        product_id = _cell_id(old.get("Product ID"))
        if product_id and product_id in fresh_by_id:
            combined = dict(old)
            combined.update(fresh_by_id[product_id])
            ordered.append(combined)
            seen.add(product_id)
        else:
            ordered.append(dict(old))
            if product_id:
                seen.add(product_id)
    for row in fresh:
        product_id = _cell_id(row.get("Product ID"))
        if product_id not in seen:
            ordered.append(dict(row))
            seen.add(product_id)
    grid = grid_from_records(headers, ordered)
    width = len(headers)
    if existing.row_count > len(grid):
        grid.extend([[""] * width for _ in range(existing.row_count - len(grid))])
    return stamp_stt(grid)


def _merge_tiers(
    existing: SheetTable,
    fresh: list[dict[str, Any]],
    fetched_ids: set[str],
) -> list[list[Any]]:
    headers = _preferred_headers(existing.headers, TIER_HEADERS)
    kept = [
        dict(row)
        for row in existing.records
        if _cell_id(row.get("Product ID")) not in fetched_ids
    ]
    grid = grid_from_records(headers, [*kept, *fresh])
    width = len(headers)
    if existing.row_count > len(grid):
        grid.extend([[""] * width for _ in range(existing.row_count - len(grid))])
    return grid


def _price_history(
    existing: SheetTable,
    fresh: list[dict[str, Any]],
    updated_at: str,
) -> list[dict[str, Any]]:
    old_costs: dict[str, int] = {}
    for record in existing.records:
        product_id = _cell_id(record.get("Product ID"))
        cost = record.get("Retail Cost (VND)")
        if product_id and isinstance(cost, int) and not isinstance(cost, bool):
            old_costs[product_id] = cost
    changes: list[dict[str, Any]] = []
    for row in fresh:
        product_id = _cell_id(row.get("Product ID"))
        new_cost = row.get("Retail Cost (VND)")
        old_cost = old_costs.get(product_id)
        if (
            old_cost is None
            or not isinstance(new_cost, int)
            or isinstance(new_cost, bool)
            or old_cost == new_cost
        ):
            continue
        changes.append(
            {
                "Thời điểm": updated_at,
                "Product ID": product_id,
                "Giá cũ (VND)": old_cost,
                "Giá mới (VND)": new_cost,
            }
        )
    return changes


def _safe_config(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    cleaned: list[tuple[str, str]] = []
    for key, value in pairs:
        if "key" in key.casefold() and key != "api_key_configured":
            continue
        if str(value).startswith("AIza") or "PRIVATE KEY" in str(value):
            continue
        cleaned.append((key, value))
    return cleaned


def _preferred_headers(existing: list[str], preferred: list[str]) -> list[str]:
    if not existing:
        return list(preferred)
    headers = list(preferred)
    for header in existing:
        if header and header not in headers:
            headers.append(header)
    return headers


def _cell_id(value: object) -> str:
    text = str(value or "").strip()
    if text.startswith(FORMULA_PREFIX):
        text = text[1:].strip()
    return text


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
