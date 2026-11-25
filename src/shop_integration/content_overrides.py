"""Kho ContentOverride trên đĩa. Mỗi lượt sync đọc lại file, không giữ bản trong RAM.

Chỉ trường nằm trong ``locked_fields`` được áp lên dòng báo giá. Giá, tồn kho và
bảo hành không có trong file. Khác biệt giữa sheet và API không tự thành khóa.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from shop_integration.config import PROJECT_ROOT
from shop_integration.errors import CatalogValidationError, IntegrationError
from shop_integration.text_normalize import public_description

OVERRIDE_VERSION = 1
DEFAULT_RELATIVE_PATH = Path("data") / "content_overrides.json"
ENV_PATH = "CONTENT_OVERRIDES_FILE"
_ITEM_FIELDS = frozenset(
    {
        "product_id",
        "variant_id",
        "locked_fields",
        "display_name",
        "display_description",
        "duration",
        "sales_content",
        "contact",
    }
)
_VALUE_FIELDS = (
    "display_name",
    "display_description",
    "duration",
    "sales_content",
    "contact",
)
_SALES_HANDLE = re.compile(r"(?i)(?<!\w)@(?:tanhung929|tiensky123)(?![\w])")


class OverrideStoreError(IntegrationError):
    """File khóa nội dung hỏng hoặc chứa trường không được phép."""


def _catalog():
    """Import muộn để không kéo catalog vào lúc combined_quote đang nạp."""

    from shop_integration.catalog.models import LOCKABLE_CONTENT_FIELDS, SALES_CONTACT, ContentOverride

    return ContentOverride, SALES_CONTACT, LOCKABLE_CONTENT_FIELDS


@dataclass
class OverrideStore:
    items: dict[tuple[str, str], ContentOverride] = field(default_factory=dict)
    path: str = ""

    def find(self, product_id: object, variant_id: object) -> ContentOverride | None:
        return self.items.get((_text(product_id), _text(variant_id)))

    def apply(self, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Áp khóa lên bản sao của dòng. Giá, tồn và bảo hành giữ nguyên."""

        applied: list[dict[str, Any]] = []
        for row in rows:
            applied.append(self._apply_one(dict(row)))
        return applied

    def upsert(
        self,
        product_id: str,
        variant_id: str,
        *,
        lock: Sequence[str] = (),
        unlock: Sequence[str] = (),
        values: Mapping[str, str | None] | None = None,
        clear: bool = False,
    ) -> None:
        key = (_text(product_id), _text(variant_id))
        if not key[0] or not key[1]:
            raise OverrideStoreError("Cần product_id và variant_id để khóa nội dung.")
        if clear:
            self.items.pop(key, None)
            return
        content_override, _sales_contact, _lockable = _catalog()
        current = self.items.get(key, content_override())
        provided = {
            name: value
            for name, value in (values or {}).items()
            if name in _VALUE_FIELDS and value is not None
        }
        locked = set(current.locked_fields)
        locked.update(_text(item) for item in lock if _text(item))
        locked.update(provided)
        locked.difference_update(_text(item) for item in unlock if _text(item))
        if not locked:
            self.items.pop(key, None)
            return
        payload = {name: getattr(current, name) for name in _VALUE_FIELDS}
        payload.update(provided)
        self.items[key] = content_override(locked_fields=frozenset(locked), **payload)

    def as_dict(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for (product_id, variant_id), override in sorted(self.items.items()):
            item: dict[str, Any] = {
                "product_id": product_id,
                "variant_id": variant_id,
                "locked_fields": sorted(override.locked_fields),
            }
            for name in _VALUE_FIELDS:
                item[name] = getattr(override, name) or ""
            items.append(item)
        return {
            "version": OVERRIDE_VERSION,
            "notes": (
                "Chỉ trường trong locked_fields được giữ khi sync. "
                "Giá, tồn kho và bảo hành không khóa được. "
                "Không suy khóa từ khác biệt trên sheet."
            ),
            "items": items,
        }

    def _apply_one(self, row: dict[str, Any]) -> dict[str, Any]:
        override = self.find(row.get("ProductID"), row.get("VariantID"))
        if override is None or not override.locked_fields:
            return row
        if "display_name" in override.locked_fields:
            current = _text(row.get("PRODUCT"))
            locked_name = override.resolve("display_name", current, current)
            if locked_name and locked_name != current:
                if not _text(row.get("source_product")):
                    row["source_product"] = current
                row["PRODUCT"] = locked_name
        if "display_description" in override.locked_fields:
            current = _text(row.get("DESCRIPTION"))
            locked_text = override.resolve("display_description", current, current)
            row["DESCRIPTION"] = public_description(locked_text)
        if "duration" in override.locked_fields:
            current = _text(row.get("DURATION"))
            row["DURATION"] = override.resolve("duration", current, current)
        if "sales_content" in override.locked_fields:
            current = _text(row.get("sales_content"))
            row["sales_content"] = override.resolve("sales_content", current, current)
        if "contact" in override.locked_fields:
            _content_override, sales_contact, _lockable = _catalog()
            contact = override.resolve("contact", "", row.get("contact") or sales_contact)
            row["DESCRIPTION"] = replace_sales_handle(row.get("DESCRIPTION"), contact or sales_contact)
        return row


def replace_sales_handle(value: object, contact: str) -> str:
    """Đổi handle bán hàng đã có trong mô tả. Không thêm câu mới khi mô tả không có handle."""

    _content_override, sales_contact, _lockable = _catalog()
    text = value if isinstance(value, str) else str(value or "")
    target = _text(contact) or sales_contact
    if not text or _SALES_HANDLE.search(text) is None:
        return text
    return _SALES_HANDLE.sub(target, text)


def default_override_path() -> Path:
    raw = os.environ.get(ENV_PATH, "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            return (PROJECT_ROOT / candidate).resolve()
        return candidate.resolve()
    return (PROJECT_ROOT / DEFAULT_RELATIVE_PATH).resolve()


def load_override_store(path: Path | None = None) -> OverrideStore:
    """Đọc file mỗi lần gọi. File chưa có thì kho rỗng, không tạo khóa."""

    target = (path or default_override_path()).resolve()
    if not target.exists():
        return OverrideStore(path=str(target))
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OverrideStoreError(f"Không đọc được content override '{target.name}': {exc}") from exc
    if not isinstance(payload, dict):
        raise OverrideStoreError("Content override phải là một object JSON.")
    version = payload.get("version")
    if version != OVERRIDE_VERSION:
        raise OverrideStoreError(f"Content override version '{version}' không được hỗ trợ.")
    raw_items = payload.get("items", [])
    if not isinstance(raw_items, list):
        raise OverrideStoreError("Content override items phải là danh sách.")
    content_override, _sales_contact, lockable = _catalog()
    items: dict[tuple[str, str], content_override] = {}
    for index, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, dict):
            raise OverrideStoreError(f"Mục override thứ {index} không phải object.")
        unknown = set(raw) - _ITEM_FIELDS
        if unknown:
            raise OverrideStoreError(
                "Content override chứa trường không được phép: " + ", ".join(sorted(unknown))
            )
        product_id = _text(raw.get("product_id"))
        variant_id = _text(raw.get("variant_id"))
        if not product_id or not variant_id:
            raise OverrideStoreError(f"Mục override thứ {index} thiếu product_id hoặc variant_id.")
        key = (product_id, variant_id)
        if key in items:
            raise OverrideStoreError(f"Content override trùng {product_id}/{variant_id}.")
        locked = raw.get("locked_fields", [])
        if not isinstance(locked, list):
            raise OverrideStoreError(f"locked_fields của {product_id}/{variant_id} phải là danh sách.")
        try:
            items[key] = content_override(
                locked_fields=frozenset(_text(item) for item in locked if _text(item)),
                display_name=_optional_text(raw.get("display_name")),
                display_description=_optional_text(raw.get("display_description")),
                duration=_optional_text(raw.get("duration")),
                sales_content=_optional_text(raw.get("sales_content")),
                contact=_optional_text(raw.get("contact")),
            )
        except CatalogValidationError as exc:
            raise OverrideStoreError(str(exc)) from exc
        unused = set(items[key].locked_fields) - lockable
        if unused:
            raise OverrideStoreError("Trường khóa không hợp lệ: " + ", ".join(sorted(unused)))
    return OverrideStore(items=items, path=str(target))


def save_override_store(store: OverrideStore, path: Path | None = None) -> Path:
    target = (path or Path(store.path) or default_override_path()).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(
        json.dumps(store.as_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    store.path = str(target)
    return target


def _text(value: object) -> str:
    return str(value or "").strip()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
