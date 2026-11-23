from __future__ import annotations

from shop_integration.product_service import (
    CUSTOMER_PRICE_HEADERS,
    MISSING_TEXT,
    PRODUCT_HEADERS,
    ProductService,
    price_bounds,
    product_sync_status,
)
from tests.conftest import load_fixture


def test_variants_prices_and_stock() -> None:
    service = ProductService()
    products, warnings = service.parse_many(load_fixture("products_page1.json")["data"])

    assert [product.id for product in products] == ["local_81", "local_82"]
    assert warnings == []
    first = products[0]
    assert first.title == "Microsoft Office 365 Family Admin 1 Tháng"
    assert first.variants[0].id == "local_81"
    assert first.variants[0].price_vnd == 9500
    assert first.variants[0].available_quantity == 2
    assert price_bounds(first) == (9500, 9500)

    second = products[1]
    assert [variant.id for variant in second.variants] == ["local_82_a", "local_82_b"]
    assert second.variants[0].available_quantity == 0
    assert price_bounds(second) == (1000, 3000)
    assert product_sync_status(second) == "Thành công"


def test_missing_fields_do_not_crash_and_skip_invalid_rows() -> None:
    service = ProductService()
    products, warnings = service.parse_many(load_fixture("products_missing_fields.json")["data"])
    ids = [product.id for product in products]

    assert ids == ["local_90", "local_91", "local_92"]
    assert products[0].title is None
    assert products[0].variants[0].price_vnd is None
    assert product_sync_status(products[0]) == "Thiếu tên, Thiếu giá"
    assert products[1].variants == []
    assert product_sync_status(products[1]) == "Thiếu biến thể"
    assert products[2].variants[0].price_vnd is None
    assert any("9500" in warning.message or "thập phân" in warning.message for warning in warnings)
    assert any("thiếu id" in warning.message for warning in warnings)


def test_sheet_rows_use_only_confirmed_fields() -> None:
    service = ProductService()
    products, _warnings = service.parse_many(load_fixture("products_page1.json")["data"])
    records = service.build_product_records(products, "2026-11-23T10:00:00+07:00")
    variant_rows = service.build_variant_records(products, "2026-11-23T10:00:00+07:00")

    assert list(records[0]) == PRODUCT_HEADERS
    assert "description" not in records[0]
    assert all("Không được ghi sang bảng" not in str(value) for record in records for value in record.values())
    assert all("Không được ghi sang bảng" not in str(value) for row in variant_rows for value in row.values())
    assert records[0]["Giá gốc thấp nhất"] == 9500
    assert isinstance(records[0]["Giá gốc thấp nhất"], int)
    assert records[0]["Giá bán thấp nhất"] == 12350
    assert records[0]["Giá bán cao nhất"] == 12350
    assert records[0]["Mô tả ngắn"] == MISSING_TEXT
    assert records[1]["Giá gốc thấp nhất"] == 1000
    assert records[1]["Giá gốc cao nhất"] == 3000
    assert records[1]["Giá bán thấp nhất"] == 1300
    assert records[1]["Giá bán cao nhất"] == 3900
    assert records[1]["Số biến thể"] == 2
    assert records[1]["Trạng thái hàng"] == "Hết hàng"
    zero_qty = next(row for row in variant_rows if row["Variant ID"] == "local_82_a")
    assert zero_qty["Số lượng tồn kho"] == 0
    assert isinstance(zero_qty["Số lượng tồn kho"], int)
    assert zero_qty["Giá gốc"] == 1000
    assert zero_qty["Lợi nhuận (VND)"] == 300
    assert zero_qty["Giá bán đề xuất (VND)"] == 1300
    assert zero_qty["Tỷ lệ lợi nhuận (%)"] == "30.00"
    customer = service.build_customer_records(products, "2026-11-23T10:00:00+07:00")
    assert list(customer[0]) == CUSTOMER_PRICE_HEADERS
    assert "Giá gốc" not in customer[0]
    assert "Lợi nhuận (VND)" not in customer[0]
    assert "Product ID" not in customer[0]
    assert [row["Giá bán"] for row in customer] == [12350, 1300, 3900]


def test_string_variant_price_and_preserved_id() -> None:
    service = ProductService()
    products, warnings = service.parse_many(load_fixture("products_page2.json")["data"])

    assert warnings == []
    assert products[0].id == "local_083"
    assert products[0].variants[0].id == "var_083"
    assert products[0].variants[0].price_vnd == 500


def test_summary_counts() -> None:
    service = ProductService()
    page = load_fixture("products_page1.json")["data"] + load_fixture("products_missing_fields.json")["data"]
    products, _warnings = service.parse_many(page)
    summary = service.summarize(products)

    assert summary.product_count == 5
    assert summary.variant_count == 5
    assert summary.in_stock == 3
    assert summary.out_of_stock == 2
    assert summary.unknown_stock == 0
    assert summary.missing_price == 3


def test_duplicate_product_keeps_last_copy() -> None:
    service = ProductService()
    raw = [
        {"id": "local_1", "title": "cũ", "in_stock": True, "variants": []},
        {"id": "local_1", "title": "mới", "in_stock": False, "variants": []},
    ]
    products, warnings = service.parse_many(raw)

    assert len(products) == 1
    assert products[0].title == "mới"
    assert any("hơn một lần" in warning.message for warning in warnings)


def test_quantity_null_absent_zero_and_optional_variant_fields_stay_distinct() -> None:
    service = ProductService()
    products, warnings = service.parse_many(
        [
            {
                "id": "local_q",
                "title": "Phân biệt tồn kho",
                "in_stock": False,
                "variants": [
                    {
                        "id": "zero",
                        "prices": {"vnd": 1000},
                        "in_stock": False,
                        "available_quantity": 0,
                        "title": "Bản đủ",
                        "description": None,
                        "manual_upgrade": False,
                    },
                    {
                        "id": "missing",
                        "prices": {"vnd": 2000},
                        "in_stock": True,
                        "title": None,
                    },
                    {
                        "id": "null_qty",
                        "prices": {"vnd": 3000},
                        "in_stock": None,
                        "available_quantity": None,
                        "description": "Có mô tả",
                        "manual_upgrade": True,
                    },
                ],
            }
        ]
    )

    assert warnings == []
    rows = {
        row["Variant ID"]: row
        for row in service.build_variant_records(products, "2026-11-23T10:00:00+07:00")
    }
    assert rows["zero"]["Số lượng tồn kho"] == 0
    assert rows["zero"]["Trạng thái còn hàng"] == "Hết hàng"
    assert rows["zero"]["Tên biến thể"] == "Bản đủ"
    assert rows["zero"]["Mô tả"] == MISSING_TEXT
    assert rows["zero"]["Mô tả gốc"] == ""
    assert rows["zero"]["Manual upgrade"] == "Không"
    assert rows["missing"]["Số lượng tồn kho"] == "Không được cung cấp"
    assert rows["missing"]["Trạng thái còn hàng"] == "Còn hàng"
    assert rows["missing"]["Tên biến thể"] == MISSING_TEXT
    assert rows["missing"]["Tên gốc"] == ""
    assert rows["missing"]["Mô tả"] == MISSING_TEXT
    assert rows["null_qty"]["Số lượng tồn kho"] == "null"
    assert rows["null_qty"]["Trạng thái còn hàng"] == "null"
    assert rows["null_qty"]["Manual upgrade"] == "Có"
    assert rows["null_qty"]["Mô tả"] == "Có mô tả"
    product_row = service.build_product_records(products, "2026-11-23T10:00:00+07:00")[0]
    assert product_row["Trạng thái hàng"] == "Hết hàng"
    assert product_row["Mô tả ngắn"] == "Có mô tả"


def test_missing_variant_text_is_explicit_and_product_description_is_ignored() -> None:
    from shop_integration.product_service import VARIANT_HEADERS, resolve_variant_headers

    service = ProductService()
    products, _warnings = service.parse_many(load_fixture("products_page1.json")["data"])
    headers = resolve_variant_headers([], products)
    row = service.build_variant_records(products, "2026-11-23T10:00:00+07:00", headers)[0]

    assert headers == VARIANT_HEADERS
    assert row["Tên biến thể"] == MISSING_TEXT
    assert row["Mô tả"] == MISSING_TEXT
    assert row["Manual upgrade"] == MISSING_TEXT
    assert row["Tên gốc"] == ""
    assert "Không được ghi sang bảng" not in row.values()


def test_decode_keeps_ids_and_stores_raw_title() -> None:
    service = ProductService()
    products, warnings = service.parse_many(
        [
            {
                "id": "local_\\u00e1",
                "title": "C\\u00e0 ph\\u00ea &amp; <b>sữa</b>",
                "in_stock": True,
                "description": "Không được ghi sang bảng",
                "variants": [
                    {
                        "id": "var_\\u00e1",
                        "prices": {"vnd": 10000},
                        "in_stock": True,
                        "title": "Size &quot;M&quot;",
                        "description": "<p>Ngọt</p><br>vừa",
                    }
                ],
            }
        ]
    )

    assert warnings == []
    assert products[0].id == "local_\\u00e1"
    assert products[0].variants[0].id == "var_\\u00e1"
    assert products[0].title == "Cà phê & sữa"
    assert products[0].title_raw == "C\\u00e0 ph\\u00ea &amp; <b>sữa</b>"
    row = service.build_variant_records(products, "2026-11-23T10:00:00+07:00")[0]
    assert row["Variant ID"] == "var_\\u00e1"
    assert row["Tên biến thể"] == 'Size "M"'
    assert row["Mô tả"] == "Ngọt\n\nvừa"
    assert row["Mô tả gốc"] == "<p>Ngọt</p><br>vừa"
    assert "Không được ghi sang bảng" not in service.build_product_records(products, "t")[0].values()


def test_invalid_cost_is_not_zero_and_is_hidden_from_customer_prices() -> None:
    service = ProductService()
    products, _warnings = service.parse_many(load_fixture("products_missing_fields.json")["data"])
    rows = service.build_variant_records(products, "2026-11-23T10:00:00+07:00")
    missing = next(row for row in rows if row["Variant ID"] == "var_90")
    assert missing["Giá gốc"] == ""
    assert missing["Giá bán đề xuất (VND)"] == ""
    assert missing["Lợi nhuận (VND)"] == ""
    assert missing["Trạng thái đồng bộ"] == "Thiếu giá"
    assert 0 not in (missing["Giá gốc"], missing["Giá bán đề xuất (VND)"], missing["Lợi nhuận (VND)"])
    customer = service.build_customer_records(products, "2026-11-23T10:00:00+07:00")
    assert customer == []


def test_integral_float_price_is_kept() -> None:
    service = ProductService()
    products, warnings = service.parse_many(
        [
            {
                "id": "local_7",
                "title": "Float nguyên",
                "in_stock": True,
                "variants": [
                    {"id": "local_7", "prices": {"vnd": 9500.0}, "in_stock": True, "available_quantity": 2.0}
                ],
            }
        ]
    )

    assert warnings == []
    assert products[0].variants[0].price_vnd == 9500
    assert products[0].variants[0].available_quantity == 2
