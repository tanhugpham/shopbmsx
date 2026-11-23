from __future__ import annotations

from typing import Any

from shop_integration.product_service import PRODUCT_HEADERS, VARIANT_HEADERS
from shop_integration.sheet_layout import (
    PRICE_HISTORY_HEADERS,
    PRODUCT_HEADER_MAP,
    SheetTable,
    diff_variant_prices,
    merge_catalog_tab,
    records_from_grid,
)
from shop_integration.text_normalize import FORMULA_PREFIX
from shop_integration.sheets_service import SheetsService


class MemorySheets:
    def __init__(self, titles: list[str] | None = None, values: dict | None = None) -> None:
        self.titles = list(titles or [])
        self.values = {key: [list(row) for row in rows] for key, rows in (values or {}).items()}
        self.writes: list[str] = []
        self.appends: list[tuple[str, list]] = []
        self.added: list[str] = []

    def list_sheet_titles(self, spreadsheet_id: str) -> list[str]:
        return list(self.titles)

    def add_sheet(self, spreadsheet_id: str, title: str) -> None:
        self.added.append(title)
        self.titles.append(title)

    def get_values(self, spreadsheet_id: str, range_a1: str) -> list[list[Any]]:
        return [list(row) for row in self.values.get(_tab(range_a1), [])]

    def update_values(self, spreadsheet_id: str, range_a1: str, values: list[list[Any]]) -> None:
        tab = _tab(range_a1)
        self.values[tab] = [list(row) for row in values]
        self.writes.append(tab)

    def append_values(self, spreadsheet_id: str, range_a1: str, values: list[list[Any]]) -> None:
        tab = _tab(range_a1)
        self.appends.append((tab, values))
        self.values.setdefault(tab, []).extend(values)


def _tab(range_a1: str) -> str:
    return range_a1.split("!", 1)[0].strip("'")


OLD_PRODUCT_HEADERS = [
    "Product ID",
    "Tên sản phẩm",
    "Trạng thái còn hàng",
    "Giá thấp nhất (VND)",
    "Giá cao nhất (VND)",
    "Số biến thể",
    "Ngày cập nhật",
    "Trạng thái đồng bộ",
]
OLD_VARIANT_HEADERS = [
    "Product ID",
    "Variant ID",
    "Giá (VND)",
    "Trạng thái còn hàng",
    "Số lượng khả dụng",
    "Ngày cập nhật",
    "Trạng thái đồng bộ",
]


def _sample_rows() -> tuple[list[dict], list[dict]]:
    products = [
        {
            "STT": "",
            "Product ID": "local_81",
            "Tên sản phẩm đã decode": "Tên mới",
            "Mô tả ngắn": "Chưa có dữ liệu",
            "Số biến thể": 1,
            "Giá gốc thấp nhất": 9500,
            "Giá gốc cao nhất": 9500,
            "Giá bán thấp nhất": 12350,
            "Giá bán cao nhất": 12350,
            "Trạng thái hàng": "Còn hàng",
            "Ngày cập nhật": "2026-11-23T10:00:00+07:00",
            "Trạng thái đồng bộ": "Thành công",
            "Tên gốc": "Tên mới",
        }
    ]
    variants = [
        {
            "STT": "",
            "Product ID": "local_81",
            "Variant ID": "local_81",
            "Tên sản phẩm": "Tên mới",
            "Tên biến thể": "Chưa có dữ liệu",
            "Mô tả": "Chưa có dữ liệu",
            "Giá gốc": 9500,
            "Tỷ lệ lợi nhuận (%)": "30.00",
            "Lợi nhuận (VND)": 2850,
            "Giá bán đề xuất (VND)": 12350,
            "Trạng thái còn hàng": "Còn hàng",
            "Số lượng tồn kho": 2,
            "Manual upgrade": "Chưa có dữ liệu",
            "Ngày cập nhật": "2026-11-23T10:00:00+07:00",
            "Trạng thái đồng bộ": "Thành công",
            "Tên gốc": "",
            "Mô tả gốc": "",
        }
    ]
    return products, variants


def test_merge_preserves_extra_column_marks_missing_and_clears_leftover() -> None:
    grid = [
        OLD_PRODUCT_HEADERS + ["Ghi chú nội bộ"],
        ["local_81", "Tên cũ", "Còn hàng", 9000, 9000, 1, "cũ", "Thành công", "giữ lại"],
        ["", "", "", "", "", "", "", "", ""],
        ["local_old", "Cũ", "Hết hàng", 1000, 1000, 1, "cũ", "Thành công", "không xóa"],
    ]
    existing = records_from_grid(grid)
    products, _variants = _sample_rows()
    merged = merge_catalog_tab(
        PRODUCT_HEADERS,
        PRODUCT_HEADER_MAP,
        existing,
        products,
        ["Product ID"],
        stamp_header="Ngày cập nhật",
        stamp_value="2026-11-23T10:00:00+07:00",
    )

    assert merged[0][-1] == "Ghi chú nội bộ"
    assert merged[0][: len(PRODUCT_HEADERS)] == PRODUCT_HEADERS
    assert merged[1][PRODUCT_HEADERS.index("STT")] == 1
    assert merged[1][PRODUCT_HEADERS.index("Product ID")] == "local_81"
    assert merged[1][PRODUCT_HEADERS.index("Tên sản phẩm đã decode")] == "Tên mới"
    assert merged[1][PRODUCT_HEADERS.index("Giá gốc thấp nhất")] == 9500
    assert merged[1][-1] == "giữ lại"
    assert merged[2][PRODUCT_HEADERS.index("Product ID")] == "local_old"
    assert merged[2][PRODUCT_HEADERS.index("Giá gốc thấp nhất")] == 1000
    assert merged[2][PRODUCT_HEADERS.index("Trạng thái đồng bộ")] == "Không còn trong API"
    assert merged[2][-1] == "không xóa"
    assert merged[3] == [""] * len(merged[0])


def test_price_diff_ignores_new_variants() -> None:
    changes = diff_variant_prices(
        [{"Product ID": "local_81", "Variant ID": "local_81", "Giá (VND)": "9000"}],
        [
            {"Product ID": "local_81", "Variant ID": "local_81", "Giá (VND)": 9500},
            {"Product ID": "local_82", "Variant ID": "local_82", "Giá (VND)": 1000},
        ],
        "2026-11-23T10:00:00+07:00",
    )

    assert changes == [
        {
            "Thời điểm": "2026-11-23T10:00:00+07:00",
            "Product ID": "local_81",
            "Variant ID": "local_81",
            "Giá cũ (VND)": 9000,
            "Giá mới (VND)": 9500,
        }
    ]


def test_renamed_cost_column_does_not_record_a_fake_price_change() -> None:
    same = diff_variant_prices(
        [{"Product ID": "local_81", "Variant ID": "local_81", "Giá (VND)": 9500}],
        [{"Product ID": "local_81", "Variant ID": "local_81", "Giá gốc": 9500, "Giá bán đề xuất (VND)": 12350}],
        "2026-11-23T10:00:00+07:00",
    )
    changed = diff_variant_prices(
        [{"Product ID": "local_81", "Variant ID": "local_81", "Giá gốc": 9500}],
        [{"Product ID": "local_81", "Variant ID": "local_81", "Giá gốc": 10000, "Giá bán đề xuất (VND)": 13000}],
        "2026-11-23T10:00:00+07:00",
    )

    assert same == []
    assert changed[0]["Giá cũ (VND)"] == 9500
    assert changed[0]["Giá mới (VND)"] == 10000


def test_apply_upserts_history_and_redacts_secret() -> None:
    secret = "sxn_live_test_key_value"
    gateway = MemorySheets(
        titles=["SAN_PHAM", "BIEN_THE", "LICH_SU_GIA"],
        values={
            "SAN_PHAM": [
                OLD_PRODUCT_HEADERS + ["Ghi chú nội bộ"],
                ["local_81", "Tên cũ", "Còn hàng", 9000, 9000, 1, "cũ", "Thành công", "giữ lại"],
                ["local_old", "Cũ", "Hết hàng", 1000, 1000, 1, "cũ", "Thành công", ""],
            ],
            "BIEN_THE": [
                OLD_VARIANT_HEADERS,
                ["local_81", "local_81", 9000, "Còn hàng", 2, "cũ", "Thành công"],
            ],
            "LICH_SU_GIA": [PRICE_HISTORY_HEADERS],
        },
    )
    service = SheetsService(gateway, "sheet-id", dry_run=False, secret=secret)
    products, variants = _sample_rows()
    products[0]["Tên sản phẩm đã decode"] = secret
    products[0]["Tên gốc"] = secret
    changes = diff_variant_prices(
        records_from_grid(gateway.values["BIEN_THE"]).records,
        variants,
        "2026-11-23T10:00:00+07:00",
    )
    service.apply_catalog(
        existing_products=service.read_records("SAN_PHAM"),
        existing_variants=service.read_records("BIEN_THE"),
        existing_history=service.read_records("LICH_SU_GIA"),
        existing_log=service.read_records("NHAT_KY_DONG_BO"),
        existing_config=service.read_records("CAU_HINH"),
        product_headers=PRODUCT_HEADERS,
        variant_headers=VARIANT_HEADERS,
        product_records=products,
        variant_records=variants,
        price_changes=changes,
        config_pairs=[("api_key_configured", "có"), ("enable_purchase", "false")],
        log_record={
            "Thời điểm": "2026-11-23T10:00:00+07:00",
            "Môi trường": "test",
            "Dry run": "Không",
            "Số sản phẩm": 1,
            "Số biến thể": 1,
            "Số thay đổi giá": 1,
            "Kết quả": "Thành công",
            "Ghi chú": "Không có cảnh báo.",
        },
        updated_at="2026-11-23T10:00:00+07:00",
    )

    assert "NHAT_KY_DONG_BO" in gateway.added
    assert "CAU_HINH" in gateway.added
    assert "BANG_GIA_BAN" in gateway.added
    product_rows = gateway.values["SAN_PHAM"]
    assert product_rows[1][PRODUCT_HEADERS.index("Tên sản phẩm đã decode")] == "***"
    assert product_rows[1][PRODUCT_HEADERS.index("Tên gốc")] == "***"
    assert product_rows[1][-1] == "giữ lại"
    assert product_rows[2][PRODUCT_HEADERS.index("Product ID")] == "local_old"
    assert product_rows[2][PRODUCT_HEADERS.index("Trạng thái đồng bộ")] == "Không còn trong API"
    variant_headers = gateway.values["BIEN_THE"][0]
    variant_row = gateway.values["BIEN_THE"][1]
    assert variant_row[variant_headers.index("Giá gốc")] == 9500
    assert variant_row[variant_headers.index("Giá bán đề xuất (VND)")] == 12350
    assert "Giá gốc" not in gateway.values["BANG_GIA_BAN"][0]
    assert "Lợi nhuận (VND)" not in gateway.values["BANG_GIA_BAN"][0]
    assert gateway.appends[0][0] == "LICH_SU_GIA"
    assert gateway.appends[0][1][0][3:] == [9000, 9500]
    config_text = "\n".join(str(cell) for row in gateway.values["CAU_HINH"] for cell in row)
    assert secret not in config_text
    assert "false" in config_text


def test_google_gateway_writes_raw_values(mocker) -> None:
    built = mocker.Mock()
    mocker.patch("googleapiclient.discovery.build", return_value=built)
    from shop_integration.sheets_service import GoogleSheetsGateway

    gateway = GoogleSheetsGateway(object())
    gateway.update_values("abc", "'SAN_PHAM'!A1", [["Product ID"], ["local_81"]])

    update = built.spreadsheets.return_value.values.return_value.update
    update.assert_called_once()
    assert update.call_args.kwargs["valueInputOption"] == "RAW"
    assert update.call_args.kwargs["spreadsheetId"] == "abc"
    built.spreadsheets.return_value.values.return_value.update.return_value.execute.assert_called_once()


def test_dry_run_does_not_write(settings_secret: str = "sxn_live_test_key_value") -> None:
    gateway = MemorySheets(titles=["SAN_PHAM"])
    service = SheetsService(gateway, "sheet-id", dry_run=True, secret=settings_secret)
    products, variants = _sample_rows()
    service.apply_catalog(
        existing_products=SheetTable(),
        existing_variants=SheetTable(),
        existing_history=SheetTable(),
        existing_log=SheetTable(),
        existing_config=SheetTable(),
        product_headers=PRODUCT_HEADERS,
        variant_headers=VARIANT_HEADERS,
        product_records=products,
        variant_records=variants,
        price_changes=[],
        config_pairs=[("enable_purchase", "false")],
        log_record={"Thời điểm": "2026-11-23T10:00:00+07:00"},
        updated_at="2026-11-23T10:00:00+07:00",
    )

    assert gateway.writes == []
    assert gateway.appends == []
    assert gateway.added == []
    assert "BIEN_THE" in service.tabs_to_create
    assert "BANG_GIA_BAN" in service.tabs_to_create


def test_formula_text_is_guarded_without_touching_ids() -> None:
    service = SheetsService(MemorySheets(), "sheet-id", dry_run=False, secret="sxn_live_test_key_value")
    grid = [
        ["Product ID", "Tên sản phẩm đã decode", "Mô tả"],
        ["=CMD", "Cà phê", "=-2+2"],
    ]
    cleaned = service._redact_grid(grid, includes_header_row=True)
    assert cleaned[1][0] == "=CMD"
    assert cleaned[1][1] == "Cà phê"
    assert cleaned[1][2] == FORMULA_PREFIX + "=-2+2"
    again = service._redact_grid(
        [grid[0], [cleaned[1][0], cleaned[1][1], cleaned[1][2]]],
        includes_header_row=True,
    )
    assert again[1][2] == FORMULA_PREFIX + "=-2+2"
    assert again[1][2].count(FORMULA_PREFIX) == 1
