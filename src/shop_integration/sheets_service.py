"""Ghi danh mục vào một spreadsheet có sẵn. DRY_RUN không tạo tab và không ghi ô."""

from __future__ import annotations

from typing import Any, Protocol

from shop_integration.errors import ConfigError, GoogleWorkspaceError, PartialSyncError
from shop_integration.google_auth import call_google
from shop_integration.logging_utils import redact
from shop_integration.product_service import CUSTOMER_PRICE_HEADERS
from shop_integration.sheet_layout import (
    ALL_TABS,
    PRICE_HISTORY_HEADERS,
    PRODUCT_HEADER_MAP,
    SYNC_LOG_HEADERS,
    VARIANT_HEADER_MAP,
    SheetTable,
    merge_catalog_tab,
    merge_config,
    records_from_grid,
    rewrite_table,
)
from shop_integration.text_normalize import guard_formula

_SECRET_MIN_LENGTH = 8


class SheetsGateway(Protocol):
    def list_sheet_titles(self, spreadsheet_id: str) -> list[str]: ...

    def add_sheet(self, spreadsheet_id: str, title: str) -> None: ...

    def get_values(self, spreadsheet_id: str, range_a1: str) -> list[list[Any]]: ...

    def update_values(
        self, spreadsheet_id: str, range_a1: str, values: list[list[Any]]
    ) -> None: ...

    def append_values(
        self, spreadsheet_id: str, range_a1: str, values: list[list[Any]]
    ) -> None: ...


class GoogleSheetsGateway:
    def __init__(self, credentials: Any) -> None:
        from googleapiclient.discovery import build

        self._service = build("sheets", "v4", credentials=credentials, cache_discovery=False)

    def list_sheet_titles(self, spreadsheet_id: str) -> list[str]:
        meta = call_google(
            lambda: self._service.spreadsheets()
            .get(spreadsheetId=spreadsheet_id, fields="sheets.properties.title")
            .execute(),
            retry=True,
        )
        titles: list[str] = []
        for sheet in meta.get("sheets", []):
            title = sheet.get("properties", {}).get("title")
            if isinstance(title, str) and title:
                titles.append(title)
        return titles

    def add_sheet(self, spreadsheet_id: str, title: str) -> None:
        body = {"requests": [{"addSheet": {"properties": {"title": title}}}]}
        call_google(
            lambda: self._service.spreadsheets()
            .batchUpdate(spreadsheetId=spreadsheet_id, body=body)
            .execute(),
            retry=False,
        )

    def list_sheet_meta(self, spreadsheet_id: str) -> list[dict[str, Any]]:
        """Id tab, banding và số rule định dạng. Dùng khi tô màu tab mới."""

        meta = call_google(
            lambda: self._service.spreadsheets()
            .get(
                spreadsheetId=spreadsheet_id,
                fields=(
                    "sheets.properties(sheetId,title),"
                    "sheets.bandedRanges(bandedRangeId),"
                    "sheets.conditionalFormats,"
                    "sheets.merges"
                ),
            )
            .execute(),
            retry=True,
        )
        found: list[dict[str, Any]] = []
        for sheet in meta.get("sheets", []) if isinstance(meta, dict) else []:
            if not isinstance(sheet, dict):
                continue
            props = sheet.get("properties", {})
            title = props.get("title") if isinstance(props, dict) else ""
            sheet_id = props.get("sheetId") if isinstance(props, dict) else None
            if not isinstance(title, str) or not isinstance(sheet_id, int):
                continue
            banding_ids: list[int] = []
            for band in sheet.get("bandedRanges", []):
                if isinstance(band, dict) and isinstance(band.get("bandedRangeId"), int):
                    banding_ids.append(band["bandedRangeId"])
            rules = sheet.get("conditionalFormats", [])
            merges = [
                merge
                for merge in (sheet.get("merges") or [])
                if isinstance(merge, dict)
            ]
            found.append(
                {
                    "title": title,
                    "sheetId": sheet_id,
                    "bandingIds": banding_ids,
                    "conditionalFormatCount": len(rules) if isinstance(rules, list) else 0,
                    "merges": merges,
                }
            )
        return found

    def batch_update(self, spreadsheet_id: str, requests: list[dict[str, Any]]) -> None:
        if not requests:
            return
        call_google(
            lambda: self._service.spreadsheets()
            .batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests})
            .execute(),
            retry=False,
        )

    def delete_sheet(self, spreadsheet_id: str, sheet_id: int) -> None:
        body = {"requests": [{"deleteSheet": {"sheetId": sheet_id}}]}
        call_google(
            lambda: self._service.spreadsheets()
            .batchUpdate(spreadsheetId=spreadsheet_id, body=body)
            .execute(),
            retry=False,
        )

    def get_values(
        self,
        spreadsheet_id: str,
        range_a1: str,
        *,
        value_render_option: str = "UNFORMATTED_VALUE",
    ) -> list[list[Any]]:
        result = call_google(
            lambda: self._service.spreadsheets()
            .values()
            .get(
                spreadsheetId=spreadsheet_id,
                range=range_a1,
                valueRenderOption=value_render_option,
            )
            .execute(),
            retry=True,
        )
        values = result.get("values", [])
        if not isinstance(values, list):
            raise GoogleWorkspaceError("Google Sheets trả về vùng dữ liệu không hợp lệ.")
        return values

    def update_values(
        self,
        spreadsheet_id: str,
        range_a1: str,
        values: list[list[Any]],
        *,
        value_input_option: str = "RAW",
    ) -> None:
        call_google(
            lambda: self._service.spreadsheets()
            .values()
            .update(
                spreadsheetId=spreadsheet_id,
                range=range_a1,
                valueInputOption=value_input_option,
                body={"values": values},
            )
            .execute(),
            retry=False,
        )

    def batch_update_values(
        self,
        spreadsheet_id: str,
        items: list[tuple[str, list[list[Any]]]],
        *,
        value_input_option: str = "RAW",
    ) -> None:
        data = [{"range": range_a1, "values": values} for range_a1, values in items]

        def send(chunk: list[dict[str, Any]]) -> None:
            self._service.spreadsheets().values().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"valueInputOption": value_input_option, "data": chunk},
            ).execute()

        for offset in range(0, len(data), 5):
            chunk = data[offset : offset + 5]
            call_google(lambda chunk=chunk: send(chunk), retry=False)

    def append_values(
        self, spreadsheet_id: str, range_a1: str, values: list[list[Any]]
    ) -> None:
        call_google(
            lambda: self._service.spreadsheets()
            .values()
            .append(
                spreadsheetId=spreadsheet_id,
                range=range_a1,
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": values},
            )
            .execute(),
            retry=False,
        )

    def backup_snapshot(self, spreadsheet_id: str) -> dict[str, Any]:
        meta = call_google(
            lambda: self._service.spreadsheets()
            .get(
                spreadsheetId=spreadsheet_id,
                fields=(
                    "spreadsheetId,properties(title,locale,timeZone),"
                    "sheets.properties(sheetId,title,index,hidden,gridProperties)"
                ),
            )
            .execute(),
            retry=True,
        )
        properties = meta.get("properties", {}) if isinstance(meta, dict) else {}
        sheets: list[dict[str, Any]] = []
        for sheet in meta.get("sheets", []) if isinstance(meta, dict) else []:
            props = sheet.get("properties", {}) if isinstance(sheet, dict) else {}
            title = props.get("title")
            if not isinstance(title, str) or not title:
                continue
            sheets.append(
                {
                    "sheet_id": props.get("sheetId"),
                    "title": title,
                    "index": props.get("index"),
                    "hidden": bool(props.get("hidden", False)),
                    "grid_properties": props.get("gridProperties", {}),
                    "formulas": self.get_values(
                        spreadsheet_id,
                        f"'{title}'!A:ZZ",
                        value_render_option="FORMULA",
                    ),
                    "values": self.get_values(
                        spreadsheet_id,
                        f"'{title}'!A:ZZ",
                        value_render_option="UNFORMATTED_VALUE",
                    ),
                }
            )
        return {
            "spreadsheet_id": spreadsheet_id,
            "title": properties.get("title", ""),
            "locale": properties.get("locale", ""),
            "time_zone": properties.get("timeZone", ""),
            "sheets": sheets,
        }


class SheetsService:
    def __init__(
        self,
        gateway: SheetsGateway,
        spreadsheet_id: str,
        *,
        dry_run: bool,
        secret: str = "",
    ) -> None:
        if not spreadsheet_id.strip():
            raise ConfigError("Thiếu GOOGLE_SPREADSHEET_ID.")
        self.gateway = gateway
        self.spreadsheet_id = spreadsheet_id.strip()
        self.dry_run = dry_run
        self._secret = secret if len(secret) >= _SECRET_MIN_LENGTH else ""
        self.tabs_to_create: list[str] = []

    def read_records(self, tab: str) -> SheetTable:
        titles = self.gateway.list_sheet_titles(self.spreadsheet_id)
        if tab not in titles:
            return SheetTable()
        grid = self.gateway.get_values(self.spreadsheet_id, f"'{tab}'!A:ZZ")
        return records_from_grid(grid)

    def apply_catalog(
        self,
        *,
        existing_products: SheetTable,
        existing_variants: SheetTable,
        existing_history: SheetTable,
        existing_log: SheetTable,
        existing_config: SheetTable,
        product_headers: list[str],
        variant_headers: list[str],
        product_records: list[dict[str, Any]],
        variant_records: list[dict[str, Any]],
        price_changes: list[dict[str, Any]],
        config_pairs: list[tuple[str, str]],
        log_record: dict[str, Any],
        updated_at: str,
        include_log: bool = True,
        existing_customer: SheetTable | None = None,
        customer_records: list[dict[str, Any]] | None = None,
    ) -> dict[str, int]:
        self._ensure_tabs()
        if self.dry_run:
            return {"products": 0, "variants": 0, "history": 0, "customer": 0}
        product_grid = merge_catalog_tab(
            product_headers,
            PRODUCT_HEADER_MAP,
            existing_products,
            product_records,
            ["Product ID"],
            stamp_header="Ngày cập nhật",
            stamp_value=updated_at,
        )
        variant_grid = merge_catalog_tab(
            variant_headers,
            VARIANT_HEADER_MAP,
            existing_variants,
            variant_records,
            ["Product ID", "Variant ID"],
            stamp_header="Ngày cập nhật",
            stamp_value=updated_at,
        )
        customer_grid = rewrite_table(
            CUSTOMER_PRICE_HEADERS,
            existing_customer or SheetTable(),
            customer_records or [],
        )
        config_grid = merge_config(existing_config, config_pairs)
        self._write_many(
            [
                ("SAN_PHAM", product_grid),
                ("BIEN_THE", variant_grid),
                ("BANG_GIA_BAN", customer_grid),
                ("CAU_HINH", config_grid),
            ]
        )
        self._append_dicts("LICH_SU_GIA", PRICE_HISTORY_HEADERS, price_changes, existing_history)
        if include_log:
            self._append_dicts("NHAT_KY_DONG_BO", SYNC_LOG_HEADERS, [log_record], existing_log)
        return {
            "products": len(records_from_grid(product_grid).records),
            "variants": len(records_from_grid(variant_grid).records),
            "history": len(existing_history.records) + len(price_changes),
            "customer": len(records_from_grid(customer_grid).records),
        }

    def append_sync_log(self, record: dict[str, Any]) -> None:
        existing = self.read_records("NHAT_KY_DONG_BO")
        self._append_dicts("NHAT_KY_DONG_BO", SYNC_LOG_HEADERS, [record], existing)

    def assert_tab_count(self, tab: str, expected: int) -> None:
        self._assert_managed_tab(tab)
        found = len(self.read_records(tab).records)
        if found != expected:
            raise PartialSyncError(
                f"Đọc lại tab {tab} thấy {found} dòng dữ liệu, dự kiến {expected}. "
                "Google Docs không được ghi. Không tự khôi phục."
            )

    def _ensure_tabs(self) -> None:
        titles = set(self.gateway.list_sheet_titles(self.spreadsheet_id))
        for title in ALL_TABS:
            self._assert_managed_tab(title)
            if title in titles:
                continue
            if self.dry_run:
                if title not in self.tabs_to_create:
                    self.tabs_to_create.append(title)
                continue
            self.gateway.add_sheet(self.spreadsheet_id, title)
            titles.add(title)

    def _assert_managed_tab(self, tab: str) -> None:
        if tab == "Trang tính1" or tab not in ALL_TABS:
            raise ConfigError(
                f"Từ chối ghi tab '{tab}'. Không ghi đè Trang tính1 hoặc dữ liệu ngoài vùng quản lý."
            )

    def _write_many(self, items: list[tuple[str, list[list[Any]]]]) -> None:
        if self.dry_run:
            return
        payload: list[tuple[str, list[list[Any]]]] = []
        for tab, grid in items:
            self._assert_managed_tab(tab)
            payload.append((f"'{tab}'!A1", self._redact_grid(grid, includes_header_row=True)))
        batch = getattr(self.gateway, "batch_update_values", None)
        if batch is not None and payload:
            batch(self.spreadsheet_id, payload)
            return
        for range_a1, grid in payload:
            self.gateway.update_values(self.spreadsheet_id, range_a1, grid)

    def _write_grid(self, tab: str, grid: list[list[Any]]) -> None:
        if self.dry_run:
            return
        self._assert_managed_tab(tab)
        self.gateway.update_values(
            self.spreadsheet_id,
            f"'{tab}'!A1",
            self._redact_grid(grid, includes_header_row=True),
        )

    def _append_dicts(
        self,
        tab: str,
        preferred_headers: list[str],
        records: list[dict[str, Any]],
        existing: SheetTable,
    ) -> None:
        if not records or self.dry_run:
            return
        self._assert_managed_tab(tab)
        if not existing.headers:
            from shop_integration.sheet_layout import grid_from_records

            self._write_grid(tab, grid_from_records(preferred_headers, records))
            return
        headers = list(existing.headers)
        missing = [header for header in preferred_headers if header not in headers]
        if missing:
            from shop_integration.sheet_layout import grid_from_records

            headers.extend(missing)
            self._write_grid(tab, grid_from_records(headers, existing.records + records))
            return
        rows: list[list[Any]] = []
        for record in records:
            rows.append([_cell_or_blank(record.get(header, "")) for header in headers])
        self.gateway.append_values(
            self.spreadsheet_id,
            f"'{tab}'!A1",
            self._redact_grid(rows, headers=headers),
        )

    def _redact_grid(
        self,
        grid: list[list[Any]],
        *,
        includes_header_row: bool = False,
        headers: list[str] | None = None,
    ) -> list[list[Any]]:
        """Che bí mật rồi chặn ô chữ bắt đầu như công thức. Không thêm ký tự vào Product ID hay Variant ID."""

        protected = {"Product ID", "Variant ID"}
        column_names = list(headers or [])
        if includes_header_row and grid and not column_names:
            column_names = [str(cell).strip() if cell is not None else "" for cell in grid[0]]
        redacted: list[list[Any]] = []
        for row_index, row in enumerate(grid):
            cleaned = []
            header_row = includes_header_row and row_index == 0
            for col_index, cell in enumerate(row):
                if not isinstance(cell, str):
                    cleaned.append(cell)
                    continue
                text = redact(cell, [self._secret])
                column = column_names[col_index] if col_index < len(column_names) else ""
                if not header_row and column not in protected:
                    text = guard_formula(text)
                cleaned.append(text)
            redacted.append(cleaned)
        return redacted


def _cell_or_blank(value: Any) -> Any:
    return "" if value is None else value


def config_value_contains_secret(pairs: list[tuple[str, str]], secret: str) -> bool:
    if len(secret) < _SECRET_MIN_LENGTH:
        return False
    return any(secret in key or secret in value for key, value in pairs)
