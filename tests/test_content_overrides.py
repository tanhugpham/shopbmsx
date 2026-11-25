from __future__ import annotations

import json

import pytest

from shop_integration.catalog.models import SALES_CONTACT, ContentOverride
from shop_integration.combined_quote import TAB_AUTO, TAB_MENU, CombinedQuoteWriter, shop_quote_rows
from shop_integration.content_overrides import (
    OverrideStore,
    OverrideStoreError,
    load_override_store,
    replace_sales_handle,
    save_override_store,
)
from shop_integration.errors import CatalogValidationError
from shop_integration.menu_sync import run_menu_sync
from shop_integration.models import Product, Variant
from shop_integration.text_normalize import CONTACT_REPLACEMENT
from tests.conftest import make_settings
from tests.test_combined_quote import _menu_book


def _chatgpt(*, description: str = "API mô tả mới", quantity: int = 9, price: int = 168000) -> Product:
    return Product(
        id="local_cg",
        title="ChatGPT",
        variants=[
            Variant(
                id="v1",
                price_vnd=price,
                in_stock=True,
                in_stock_presence="value",
                title="Plus",
                description=description,
                available_quantity=quantity,
                quantity_presence="value",
            )
        ],
    )


def _store(*items: dict) -> OverrideStore:
    payload = {"version": 1, "items": list(items)}
    path_text = json.dumps(payload)
    store = OverrideStore()
    raw = json.loads(path_text)
    for item in raw["items"]:
        store.items[(item["product_id"], item["variant_id"])] = ContentOverride(
            locked_fields=frozenset(item.get("locked_fields") or []),
            display_name=item.get("display_name"),
            display_description=item.get("display_description"),
            duration=item.get("duration"),
            sales_content=item.get("sales_content"),
            contact=item.get("contact"),
        )
    return store


def test_sales_contact_is_the_public_replacement() -> None:
    assert CONTACT_REPLACEMENT == SALES_CONTACT == "@tanhung929"


def test_missing_file_does_not_invent_locks(tmp_path) -> None:
    store = load_override_store(tmp_path / "missing.json")
    row = {"ProductID": "local_cg", "VariantID": "v1", "PRODUCT": "ChatGPT — Plus", "PRICE": 1}
    assert store.apply([row]) == [row]


def test_corrupt_file_stops_before_a_write(tmp_path) -> None:
    target = tmp_path / "content_overrides.json"
    target.write_text("{", encoding="utf-8")
    with pytest.raises(OverrideStoreError):
        load_override_store(target)


def test_price_stock_and_warranty_cannot_be_stored(tmp_path) -> None:
    target = tmp_path / "content_overrides.json"
    target.write_text(
        json.dumps(
            {
                "version": 1,
                "items": [
                    {
                        "product_id": "local_cg",
                        "variant_id": "v1",
                        "locked_fields": ["display_name"],
                        "price": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(OverrideStoreError):
        load_override_store(target)
    with pytest.raises(CatalogValidationError):
        ContentOverride(locked_fields=frozenset({"warranty"}))


def test_locked_fields_survive_and_price_stock_follow_api() -> None:
    overrides = _store(
        {
            "product_id": "local_cg",
            "variant_id": "v1",
            "locked_fields": ["display_name", "display_description", "duration", "sales_content"],
            "display_name": "Tên khách đã chốt",
            "display_description": "Mô tả khách @tiensky123 giữ nguyên",
            "duration": "Vĩnh viễn",
            "sales_content": "Inbox riêng",
        }
    )
    first = shop_quote_rows([_chatgpt()], overrides=overrides)[0]
    second = shop_quote_rows(
        [_chatgpt(description="API đã đổi", quantity=2, price=200000)],
        overrides=overrides,
    )[0]
    assert first["PRODUCT"] == "Tên khách đã chốt"
    assert first["source_product"] == "ChatGPT — Plus"
    assert "Mô tả khách" in first["DESCRIPTION"]
    assert "@tanhung929" in first["DESCRIPTION"]
    assert "@tiensky123" not in first["DESCRIPTION"]
    assert "API mô tả" not in first["DESCRIPTION"]
    assert first["DURATION"] == "Vĩnh viễn"
    assert first["sales_content"] == "Inbox riêng"
    assert first["PRICE"] == 203000
    assert first["STOCK"] == 9
    assert second["PRODUCT"] == "Tên khách đã chốt"
    assert second["PRICE"] != first["PRICE"]
    assert second["STOCK"] == 2
    assert second["WARRANTY"] == first["WARRANTY"]
    again = overrides.apply([dict(first)])[0]
    assert again["PRODUCT"] == first["PRODUCT"]
    assert again["source_product"] == first["source_product"]
    assert again["PRICE"] == first["PRICE"]


def test_unlocked_description_follows_api() -> None:
    overrides = _store(
        {
            "product_id": "local_cg",
            "variant_id": "v1",
            "locked_fields": ["display_name"],
            "display_name": "Tên khách đã chốt",
            "display_description": "Mô tả này chưa khóa",
        }
    )
    row = shop_quote_rows([_chatgpt(description="Mô tả API mới")], overrides=overrides)[0]
    assert row["PRODUCT"] == "Tên khách đã chốt"
    assert row["DESCRIPTION"] == "Mô tả API mới"


def test_locked_contact_replaces_existing_handle_only() -> None:
    assert replace_sales_handle("Inbox @tanhung929 nhé.", "@tanhung929") == "Inbox @tanhung929 nhé."
    assert replace_sales_handle("Không có handle.", "@khac") == "Không có handle."
    overrides = _store(
        {
            "product_id": "local_cg",
            "variant_id": "v1",
            "locked_fields": ["contact"],
            "contact": "@tanhung929",
        }
    )
    row = shop_quote_rows(
        [_chatgpt(description="Inbox @tiensky123 để nhận mã.")],
        overrides=overrides,
    )[0]
    assert row["DESCRIPTION"] == "Inbox @tanhung929 để nhận mã."
    assert "ProductID" not in json.dumps({key: row[key] for key in row if key in {
        "ID", "PRODUCT", "DESCRIPTION", "DURATION", "WARRANTY", "PRICE", "STOCK"
    }})


def test_menu_sync_keeps_locked_row_and_supplier_warranty(tmp_path) -> None:
    target = tmp_path / "content_overrides.json"
    save_override_store(
        _store(
            {
                "product_id": "local_cg",
                "variant_id": "v1",
                "locked_fields": ["display_name", "display_description"],
                "display_name": "Tên khách đã chốt",
                "display_description": "Mô tả khách đã khóa",
            }
        ),
        target,
    )
    loaded = load_override_store(target)
    gateway = _menu_book()
    result = CombinedQuoteWriter(gateway, "last-id").update(
        shop_quote_rows([_chatgpt(description="API đổi tên và mô tả")], overrides=loaded),
        dry_run=False,
        expire_missing=False,
    )
    menu = gateway.values[TAB_MENU]
    assert result.applied is True
    assert menu[7][0] == "SXN-01"
    assert menu[7][1] == "Tên khách đã chốt"
    assert menu[7][2] == "Mô tả khách đã khóa"
    assert menu[7][4] == "BHF"
    assert menu[7][5] == 203000
    assert menu[7][6] == 9
    assert sum(1 for row in menu if row and row[0] == "SXN-01") == 1
    assert menu[10][1] == "Claude"
    assert "local_cg" not in json.dumps(gateway.values[TAB_AUTO], ensure_ascii=False)
    second = CombinedQuoteWriter(gateway, "last-id").update(
        shop_quote_rows([_chatgpt(quantity=4)], overrides=load_override_store(target)),
        dry_run=False,
        expire_missing=False,
    )
    assert second.applied is True
    assert gateway.values[TAB_MENU][7][1] == "Tên khách đã chốt"
    assert gateway.values[TAB_MENU][7][2] == "Mô tả khách đã khóa"
    assert gateway.values[TAB_MENU][7][6] == 4
    assert gateway.values[TAB_MENU][7][4] == "BHF"


def test_stated_warranty_still_updates_menu_cell() -> None:
    gateway = _menu_book()
    gateway.values[TAB_MENU][7][4] = "KBH"
    CombinedQuoteWriter(gateway, "last-id").update(
        shop_quote_rows([_chatgpt(description="Gói riêng BHF")], overrides=OverrideStore()),
        dry_run=False,
        expire_missing=False,
    )
    assert gateway.values[TAB_MENU][7][4] == "BHF"


def test_live_sync_path_reloads_override_file(tmp_path, monkeypatch) -> None:
    target = tmp_path / "content_overrides.json"
    save_override_store(
        _store(
            {
                "product_id": "local_cg",
                "variant_id": "v1",
                "locked_fields": ["display_description"],
                "display_description": "Nội dung khóa trên đĩa",
            }
        ),
        target,
    )
    monkeypatch.setenv("CONTENT_OVERRIDES_FILE", str(target))
    gateway = _menu_book()
    result = run_menu_sync(
        make_settings(google_spreadsheet_id="last-id"),
        live=True,
        gateway=gateway,
        shop_client=type(
            "Shop",
            (),
            {
                "list_all_products": lambda self: [
                    {
                        "id": "local_cg",
                        "title": "ChatGPT",
                        "in_stock": True,
                        "variants": [
                            {
                                "id": "v1",
                                "title": "Plus",
                                "prices": {"vnd": 168000},
                                "in_stock": True,
                                "description": "API mô tả",
                                "available_quantity": 9,
                            }
                        ],
                    }
                ]
            },
        )(),
    )
    assert result.status == "ok"
    assert gateway.values[TAB_MENU][7][2] == "Nội dung khóa trên đĩa"
    assert gateway.values[TAB_MENU][7][6] == 9


def test_clear_removes_lock_instead_of_treating_diff_as_manual(tmp_path) -> None:
    store = OverrideStore()
    store.upsert(
        "local_cg",
        "v1",
        values={"display_name": "Tên khách đã chốt"},
    )
    assert store.find("local_cg", "v1").locked_fields == frozenset({"display_name"})
    store.upsert("local_cg", "v1", unlock=["display_name"])
    assert store.find("local_cg", "v1") is None
    target = save_override_store(store, tmp_path / "content_overrides.json")
    assert load_override_store(target).items == {}
