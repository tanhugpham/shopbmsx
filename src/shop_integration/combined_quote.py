"""Báo giá tổng hợp: MENU là bảng khách chính thức.

NASTELEID và BAO_GIA dùng cùng bảng khóa nguồn.
Dòng 1: STT | Nguồn | Tên sản phẩm | Tên biến thể | Mô tả | Giá bán | Tình trạng.
STT là số thứ tự, không lấy local id API.
BAO_GIA do tool ghi từ Shop Xoài Non.
NASTELEID do tool ghi khi có catalog Nastele; nếu catalog rỗng thì giữ dòng tay.
MENU nhận cả hai nguồn, giữ branding và cột bảo hành bên phải.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from shop_integration.errors import ConfigError
from shop_integration.models import Product
from shop_integration.pricing import PricingPolicy, quote_cost, round_pretty_vnd
from shop_integration.product_service import MISSING_TEXT, variant_stock_label
from shop_integration.text_normalize import guard_formula, public_description, shorten_text

TAB_MANUAL = "NASTELEID"
TAB_AUTO = "BAO_GIA"
TAB_COMBINED = "BAO_GIA_TONG_HOP"
TAB_MENU = "MENU"
DATA_TABS = (TAB_AUTO, TAB_COMBINED)
LOCKED_TABS = (TAB_MANUAL, TAB_AUTO, TAB_COMBINED)
WRITE_TABS = (*LOCKED_TABS, TAB_MENU)

SOURCE_MANUAL = "NASTELEID"
SOURCE_SHOP = "Shop Xoài Non"
MANUAL_ID = "Nastele ID"

QUOTE_HEADERS = [
    "STT",
    "Nguồn",
    "Tên sản phẩm",
    "Tên biến thể",
    "Mô tả",
    "Giá bán",
    "Tình trạng",
]
KEY_HEADERS = ("Nguồn", "STT")
STOCK_VALUES = ("Còn hàng", "Hết hàng", "Chưa có dữ liệu")
FORBIDDEN_COST_HEADERS = (
    "Giá gốc",
    "Giá gốc thấp nhất",
    "Giá gốc cao nhất",
    "Giá thấp nhất (VND)",
    "Giá cao nhất (VND)",
    "Lợi nhuận (VND)",
    "Tỷ lệ lợi nhuận (%)",
    "Giá bán đề xuất (VND)",
    "Retail Cost (VND)",
    "Shop Xoài Non Cost",
    "Nastele Cost",
    "Selected Cost",
)
HEADER_BLUE = {"red": 0.05, "green": 0.16, "blue": 0.36}
_COLUMN_WIDTHS = {
    "STT": 60,
    "Nguồn": 140,
    "Tên sản phẩm": 280,
    "Tên biến thể": 160,
    "Mô tả": 360,
    "Giá bán": 120,
    "Tình trạng": 130,
}
MENU_HEADERS = [
    "ID",
    "PRODUCT",
    "DESCRIPTION",
    "DURATION",
    "WARRANTY",
    "PRICE",
    "STOCK",
    "STATUS",
]
MENU_COLUMNS = len(MENU_HEADERS)
MENU_SIDEBAR_COLUMN = 9
SOURCE_SECTIONS = (
    (SOURCE_SHOP, "🤖 SHOP XOÀI NON", "SXN"),
    (SOURCE_MANUAL, "💎 NASTELE", "NST"),
)
_DURATION_UNIT = {
    "năm": "Năm",
    "nam": "Năm",
    "year": "Năm",
    "years": "Năm",
    "tháng": "Tháng",
    "thang": "Tháng",
    "thg": "Tháng",
    "month": "Tháng",
    "months": "Tháng",
    "ngày": "Ngày",
    "ngay": "Ngày",
    "day": "Ngày",
    "days": "Ngày",
    "d": "Ngày",
}
_LIFETIME = re.compile(r"(?i)trọn\s*đời|vĩnh\s*viễn|never\s*expires")
_DURATION_RANGE = re.compile(
    r"(?i)(\d+)\s*[-–]\s*(\d+)\s*(ngày|ngay|day|days|d)\b"
)
_DURATION_ONE = re.compile(
    r"(?i)(\d+)\s*(năm|nam|year|years|tháng|thang|thg|month|months|ngày|ngay|days|day|d)\b"
)
_WARRANTY = re.compile(r"(?i)\b(BHF|KBH|BH\s*\d+\s*(?:ngày|ngay|[dnh]))\b")
_PRODUCT_ID_RE = re.compile(r"^(SXN|NST|AI|UP|WIN|OFF|SFT)-(\d+)$", re.IGNORECASE)
_CATEGORY_SPECS = (
    ("shop xoài non", "SXN", ("shop xoài non", "shop xoai non")),
    ("ai accounts", "AI", ("ai accounts", "tài khoản trí tuệ", "tri tue nhan tao")),
    ("ai premium", "UP", ("ai premium", "upgrade", "nâng cấp chính chủ", "nang cap")),
    ("windows", "WIN", ("windows license", "windows")),
    ("microsoft office", "OFF", ("microsoft office", "tin học văn phòng", "tin hoc van phong")),
    ("software", "SFT", ("software", "digital products", "phần mềm", "phan mem", "tiện ích số")),
    ("nastele", "NST", ("nastele",)),
)
_PRODUCT_CATEGORY_RULES = (
    ("windows", ("windows",)),
    ("microsoft office", ("microsoft office", "office 365", "office 202", "office 201")),
    ("ai premium", ("upgrade", "nâng cấp", "nang cap")),
    (
        "ai accounts",
        ("chatgpt", "gpt-4", "grok", "claude", "gemini", "midjourney", "perplexity", "muse"),
    ),
    (
        "software",
        (
            "canva",
            "capcut",
            "nord",
            "vpn",
            "figma",
            "duolingo",
            "memrise",
            "meitu",
            "wink",
            "spotify",
            "adobe",
            "youtube",
        ),
    ),
)


@dataclass
class MenuRegion:
    key: str
    title: str
    header_row: int
    prefix: str
    product_rows: list[int] = field(default_factory=list)
    empty_rows: list[int] = field(default_factory=list)


class QuoteGateway(Protocol):
    def list_sheet_titles(self, spreadsheet_id: str) -> list[str]: ...

    def add_sheet(self, spreadsheet_id: str, title: str) -> None: ...

    def get_values(self, spreadsheet_id: str, range_a1: str) -> list[list[Any]]: ...

    def batch_update_values(
        self,
        spreadsheet_id: str,
        items: list[tuple[str, list[list[Any]]]],
        *,
        value_input_option: str = "RAW",
    ) -> None: ...

    def list_sheet_meta(self, spreadsheet_id: str) -> list[dict[str, Any]]: ...

    def batch_update(self, spreadsheet_id: str, requests: list[dict[str, Any]]) -> None: ...


@dataclass
class TabPlan:
    tab: str
    create: bool = False
    full_grid: list[list[Any]] | None = None
    cells: list[tuple[str, Any]] = field(default_factory=list)
    warning: str = ""
    format_ui: bool = False
    origin: str = "A1"
    allow_manual_data: bool = False

    def ranges(self) -> list[str]:
        found: list[str] = []
        if self.full_grid is not None:
            found.append(f"'{self.tab}'!{self.origin}")
        found.extend(cell_range for cell_range, _value in self.cells)
        return found


@dataclass
class QuoteUpdate:
    shop_count: int
    manual_count: int
    warnings: list[str]
    ranges: list[str]
    applied: bool
    nastele_count: int = 0
    menu_count: int = 0

    def note(self, *, dry_run: bool) -> str:
        parts = list(self.warnings)
        if dry_run:
            parts.append(
                f"Dry-run file tổng hợp: {self.shop_count} dòng shop, "
                f"{self.nastele_count or self.manual_count} dòng Nastele, "
                f"{self.menu_count} dòng MENU. Không ghi ô."
            )
        elif self.ranges:
            parts.append(
                "Đã khóa UI STT|Nguồn|Tên sản phẩm|Tên biến thể|Mô tả|Giá bán|Tình trạng "
                "trên NASTELEID và BAO_GIA."
            )
            if self.nastele_count:
                parts.append("Đã ghi NASTELEID từ catalog Nastele.")
            else:
                parts.append("Không ghi dữ liệu NASTELEID.")
            if self.menu_count:
                parts.append("Đã cập nhật MENU (báo giá chính thức) từ hai nguồn.")
        else:
            parts.append("File tổng hợp không có ô cần đổi.")
        return " ".join(parts)


def shop_quote_rows(
    products: list[Product], policy: PricingPolicy | None = None
) -> list[dict[str, Any]]:
    """Mỗi biến thể có giá bán thành một dòng. STT 1..n. Không ghi local id API."""

    rules = policy or PricingPolicy()
    rows: list[dict[str, Any]] = []
    serial = 0
    for product in products:
        product_name = (product.title or "").strip() or "Chưa có tên"
        for variant in product.variants:
            quote = quote_cost(variant.price_vnd, rules)
            if quote.selling is None:
                continue
            serial += 1
            description = _public_description((variant.description or "").strip())
            quantity: Any = ""
            if variant.quantity_presence == "value":
                quantity = variant.available_quantity
            rows.append(
                {
                    "STT": serial,
                    "Nguồn": SOURCE_SHOP,
                    "Tên sản phẩm": product_name,
                    "Tên biến thể": (variant.title or "").strip(),
                    "Mô tả": description or MISSING_TEXT,
                    "Giá bán": round_pretty_vnd(quote.selling),
                    "Tình trạng": variant_stock_label(variant),
                    "Số lượng": quantity,
                }
            )
    return rows


def nastele_quote_rows(
    offers: list[Any], policy: PricingPolicy | None = None
) -> list[dict[str, Any]]:
    """Mỗi sản phẩm Nastele một dòng khách. Giá bán = giá gốc + lợi nhuận, làm tròn đẹp."""

    rules = policy or PricingPolicy()
    rows: list[dict[str, Any]] = []
    serial = 0
    for offer in offers:
        quote = quote_cost(getattr(offer, "cost_price_vnd", None), rules)
        if quote.selling is None:
            continue
        serial += 1
        name = str(getattr(offer, "source_product_name", "") or "").strip() or "Chưa có tên"
        description = _public_description(str(getattr(offer, "description", "") or "").strip())
        attrs = getattr(offer, "product_attributes", None) or {}
        variant = str(attrs.get("bien_the") or "").strip()
        if variant in {"", "API không có biến thể"}:
            variant = ""
        available = getattr(offer, "available", None)
        presence = str(getattr(offer, "quantity_presence", "") or "")
        quantity = getattr(offer, "available_quantity", None)
        if available is True:
            status = "Còn hàng"
        elif available is False:
            status = "Hết hàng"
        elif presence == "value" and quantity == 0:
            status = "Hết hàng"
        else:
            status = "Chưa có dữ liệu"
        rows.append(
            {
                "STT": serial,
                "Nguồn": SOURCE_MANUAL,
                "Tên sản phẩm": name,
                "Tên biến thể": variant,
                "Mô tả": description or MISSING_TEXT,
                "Giá bán": round_pretty_vnd(quote.selling),
                "Tình trạng": status,
                "Số lượng": quantity if presence == "value" else "",
            }
        )
    return rows


def quote_duration(*parts: object) -> str:
    text = " ".join(str(part or "") for part in parts)
    if _LIFETIME.search(text):
        return "Vĩnh viễn"
    ranged = _DURATION_RANGE.search(text)
    if ranged:
        unit = _DURATION_UNIT[ranged.group(3).casefold()]
        return f"{int(ranged.group(1))}-{int(ranged.group(2))} {unit}"
    one = _DURATION_ONE.search(text)
    if one:
        unit = _DURATION_UNIT[one.group(2).casefold()]
        return f"{int(one.group(1))} {unit}"
    return ""


def quote_warranty(*parts: object) -> str:
    text = " ".join(str(part or "") for part in parts)
    match = _WARRANTY.search(text)
    if not match:
        return "Bảo hành 1:1"
    compact = re.sub(r"\s+", "", match.group(1)).upper()
    compact = compact.replace("NGÀY", "N").replace("NGAY", "N")
    return compact


def menu_quote_grid(*groups: tuple[str, str, list[dict[str, Any]]]) -> list[list[Any]]:
    """Lưới A-H của MENU. Không đụng cột bảo hành từ J trở đi."""

    grid: list[list[Any]] = []
    for title, prefix, rows in groups:
        if not rows:
            continue
        grid.append([title, "", "", "", "", "", "", ""])
        for index, row in enumerate(rows, start=1):
            grid.append(_menu_product_row(f"{prefix}-{index:02d}", row))
    return grid


def _menu_product_row(item_id: str, row: dict[str, Any]) -> list[Any]:
    name = str(row.get("Tên sản phẩm") or "").strip()
    variant = str(row.get("Tên biến thể") or "").strip()
    product = name if not variant or variant == name else f"{name} — {variant}"
    description = _customer_description(row.get("Mô tả"), product)
    blob = " ".join((name, variant, str(row.get("Mô tả") or "")))
    quantity = row.get("Số lượng")
    status_label = str(row.get("Tình trạng") or "")
    return [
        item_id,
        product,
        description,
        quote_duration(blob),
        quote_warranty(blob),
        row.get("Giá bán", ""),
        _menu_stock(status_label, quantity),
        _menu_status(status_label, quantity),
    ]


def _customer_description(value: object, fallback: str) -> str:
    text = shorten_text(
        _public_description(str(value or "").replace("[SERVICE_MANUAL]", " ").strip()),
        140,
    )
    if not text or text == MISSING_TEXT:
        return _public_description(fallback) or fallback
    return _public_description(text)


def _public_description(value: object) -> str:
    return public_description(value)


def _menu_stock(status_label: str, quantity: object) -> Any:
    if isinstance(quantity, int) and not isinstance(quantity, bool):
        return quantity
    if "Hết" in status_label:
        return 0
    return ""


def _menu_status(status_label: str, quantity: object) -> str:
    qty = quantity if isinstance(quantity, int) and not isinstance(quantity, bool) else None
    if "Hết" in status_label or qty == 0:
        return "SOLD OUT"
    if qty is not None and 0 < qty <= 5:
        return "LOW STOCK"
    if "Còn" in status_label:
        return "IN STOCK"
    return "CONTACT"


def is_category_header_row(row: list[Any] | None) -> bool:
    """Hàng tiêu đề danh mục: có nhãn ở A, không phải mã sản phẩm, B–H trống."""

    raw = row or []
    label = str(_at(raw, 0) or "").strip()
    if not label or _header(label).upper() == "ID":
        return False
    if _PRODUCT_ID_RE.match(label):
        return False
    rest = [_at(raw, column) for column in range(1, MENU_COLUMNS)]
    rest_blank = all(_blank(cell) for cell in rest)
    folded = _fold_category(label)
    known = any(token in folded for _key, _prefix, tokens in _CATEGORY_SPECS for token in tokens)
    if rest_blank:
        return known or _looks_like_section_title(label)
    if known and all(_blank(cell) or _fold_category(cell) == folded for cell in rest):
        return True
    return False


def is_menu_product_row(row: list[Any] | None) -> bool:
    raw = row or []
    if is_category_header_row(raw):
        return False
    label = str(_at(raw, 0) or "").strip()
    if _PRODUCT_ID_RE.match(label):
        return True
    product = _at(raw, 1)
    price = _at(raw, 5)
    status = _at(raw, 7)
    return not _blank(product) and (not _blank(price) or not _blank(status))


def list_menu_products(grid: list[list[Any]] | None) -> list[dict[str, Any]]:
    """Đọc sản phẩm MENU. Bỏ Category Header, banner, hàng trang trí."""

    rows: list[dict[str, Any]] = []
    header_index = _menu_header_index(grid)
    if header_index is None:
        return rows
    for raw in (grid or [])[header_index + 1 :]:
        if not is_menu_product_row(raw):
            continue
        rows.append(
            {
                "ID": _at(raw, 0),
                "PRODUCT": _at(raw, 1),
                "DESCRIPTION": _at(raw, 2),
                "DURATION": _at(raw, 3),
                "WARRANTY": _at(raw, 4),
                "PRICE": _at(raw, 5),
                "STOCK": _at(raw, 6),
                "STATUS": _at(raw, 7),
            }
        )
    return rows


def parse_menu_regions(grid: list[list[Any]] | None, header_index: int) -> list[MenuRegion]:
    """Map mỗi Category Header tới vùng sản phẩm ngay bên dưới, đến header kế tiếp."""

    sheet = grid or []
    regions: list[MenuRegion] = []
    current: MenuRegion | None = None
    last_row = max(len(sheet), header_index + 1)
    for row_number in range(header_index + 2, last_row + 1):
        raw = sheet[row_number - 1] if row_number - 1 < len(sheet) else []
        if is_category_header_row(raw):
            current = _region_from_title(_at(raw, 0), row_number)
            regions.append(current)
            continue
        if current is None:
            continue
        if is_menu_product_row(raw):
            current.product_rows.append(row_number)
        elif _menu_data_empty(raw) and not _menu_ui_occupied(raw):
            current.empty_rows.append(row_number)
    if regions:
        return regions
    implicit = MenuRegion(key="shop xoài non", title="", header_row=0, prefix="SXN")
    for row_number in range(header_index + 2, last_row + 1):
        raw = sheet[row_number - 1] if row_number - 1 < len(sheet) else []
        if is_menu_product_row(raw):
            implicit.product_rows.append(row_number)
        elif _menu_data_empty(raw) and not _menu_ui_occupied(raw):
            implicit.empty_rows.append(row_number)
    if implicit.product_rows or implicit.empty_rows:
        regions.append(implicit)
    return regions


def assign_menu_category(row: dict[str, Any], regions: list[MenuRegion]) -> MenuRegion | None:
    """Gán sản phẩm vào vùng danh mục có sẵn. Không tự tạo Category Header."""

    by_key = {region.key: region for region in regions}
    source = str(row.get("Nguồn") or "")
    if source == SOURCE_MANUAL:
        return by_key.get("nastele")
    specialized = [
        region for region in regions if region.key not in {"shop xoài non", "nastele", ""}
    ]
    blob = _fold_category(
        " ".join(str(row.get(name) or "") for name in ("Tên sản phẩm", "Tên biến thể", "Mô tả"))
    )
    if specialized:
        for key, tokens in _PRODUCT_CATEGORY_RULES:
            if key in by_key and any(token in blob for token in tokens):
                return by_key[key]
    if "shop xoài non" in by_key:
        return by_key["shop xoài non"]
    shop_like = [region for region in regions if region.key != "nastele"]
    return shop_like[0] if shop_like else None


def plan_menu_cells(
    grid: list[list[Any]],
    shop_rows: list[dict[str, Any]],
    nastele_rows: list[dict[str, Any]],
    header_index: int,
) -> tuple[list[tuple[str, Any]], int, list[str]]:
    """Cập nhật từng ô A–H của sản phẩm. Không ghi Category Header hay cột J+."""

    regions = parse_menu_regions(grid, header_index)
    locked = _locked_menu_rows(grid, header_index, regions)
    allocated = _allocated_menu_ids(grid)
    by_region_name, by_name = _menu_name_indexes(grid, regions, header_index)
    used_rows: set[int] = set()
    cells: list[tuple[str, Any]] = []
    notes: list[str] = []
    placed = 0
    incoming = list(shop_rows) + list(nastele_rows)
    for item in incoming:
        region = assign_menu_category(item, regions)
        keys = _incoming_name_keys(item)
        row_number = _match_menu_row(keys, region, by_region_name, by_name, used_rows, locked)
        item_id = ""
        if row_number is not None:
            item_id = str(_at(grid[row_number - 1] if row_number - 1 < len(grid) else [], 0) or "").strip()
        elif region is None:
            name = str(item.get("Tên sản phẩm") or "").strip() or "?"
            notes.append(f"Bỏ qua '{name}' vì chưa có vùng danh mục phù hợp.")
            continue
        else:
            row_number = _next_region_slot(region, used_rows, locked)
            if row_number is None:
                name = str(item.get("Tên sản phẩm") or "").strip() or "?"
                notes.append(
                    f"Bỏ qua '{name}' vì vùng '{region.title or region.key}' không còn hàng trống."
                )
                continue
            item_id = _next_menu_id(region.prefix, allocated)
        if not item_id:
            item_id = _next_menu_id(region.prefix if region else "SXN", allocated)
        used_rows.add(row_number)
        for key in keys:
            by_name[key] = row_number
            if region is not None:
                by_region_name[(region.key, key)] = row_number
        wanted = _menu_product_row(item_id, item)
        _append_menu_row_changes(cells, grid, row_number, wanted, locked)
        placed += 1
    _sanitize_existing_menu_descriptions(grid, header_index, cells, locked)
    if not notes:
        notes.append(
            "Đã cập nhật MENU theo từng danh mục. Category Header và cột bảo hành bên phải giữ nguyên."
        )
    return cells, placed, notes


def _fold_category(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _looks_like_section_title(label: str) -> bool:
    if _PRODUCT_ID_RE.match(label.strip()):
        return False
    letters = [char for char in label if char.isalpha()]
    if letters and sum(char.isupper() for char in letters) / len(letters) >= 0.6:
        return True
    return any(ord(char) > 127 and not char.isalpha() and not char.isspace() for char in label)


def _region_from_title(title: object, row_number: int) -> MenuRegion:
    text = str(title or "").strip()
    folded = _fold_category(text)
    for key, prefix, tokens in _CATEGORY_SPECS:
        if any(token in folded for token in tokens):
            return MenuRegion(key=key, title=text, header_row=row_number, prefix=prefix)
    return MenuRegion(key=folded or "other", title=text, header_row=row_number, prefix="OTH")


def _menu_data_empty(row: list[Any] | None) -> bool:
    raw = row or []
    return all(_blank(_at(raw, column)) for column in range(MENU_COLUMNS))


def _menu_ui_occupied(row: list[Any] | None) -> bool:
    raw = row or []
    return any(not _blank(_at(raw, column)) for column in range(MENU_COLUMNS, len(raw)))


def _locked_menu_rows(
    grid: list[list[Any]], header_index: int, regions: list[MenuRegion]
) -> set[int]:
    locked = set(range(1, header_index + 2))
    for region in regions:
        if region.header_row:
            locked.add(region.header_row)
    for row_number, raw in enumerate(grid, start=1):
        if row_number <= header_index + 1:
            continue
        if is_category_header_row(raw):
            locked.add(row_number)
        elif _menu_data_empty(raw) and _menu_ui_occupied(raw):
            locked.add(row_number)
        elif not is_menu_product_row(raw) and not _menu_data_empty(raw):
            locked.add(row_number)
    return locked


def _allocated_menu_ids(grid: list[list[Any]]) -> set[str]:
    found: set[str] = set()
    for raw in grid:
        label = str(_at(raw, 0) or "").strip()
        match = _PRODUCT_ID_RE.match(label)
        if not match:
            continue
        found.add(label.upper())
        found.add(f"{match.group(1).upper()}-{int(match.group(2)):02d}")
    return found


def _next_menu_id(prefix: str, allocated: set[str]) -> str:
    serial = 1
    while True:
        candidate = f"{prefix}-{serial:02d}"
        if candidate.upper() not in allocated:
            allocated.add(candidate.upper())
            return candidate
        serial += 1


def _existing_name_keys(row: list[Any]) -> set[str]:
    product = str(_at(row, 1) or "").strip()
    keys = {_fold_category(product)} if product else set()
    for separator in (" — ", " - ", " – "):
        if separator in product:
            keys.add(_fold_category(product.split(separator, 1)[0]))
    return {key for key in keys if key}


def _incoming_name_keys(row: dict[str, Any]) -> set[str]:
    name = str(row.get("Tên sản phẩm") or "").strip()
    variant = str(row.get("Tên biến thể") or "").strip()
    keys = {_fold_category(name)} if name else set()
    if variant and variant != name:
        keys.add(_fold_category(f"{name} — {variant}"))
        keys.add(_fold_category(f"{name} {variant}"))
    return {key for key in keys if key}


def _menu_name_indexes(
    grid: list[list[Any]],
    regions: list[MenuRegion],
    header_index: int,
) -> tuple[dict[tuple[str, str], int], dict[str, int]]:
    by_region_name: dict[tuple[str, str], int] = {}
    by_name: dict[str, int] = {}
    region_of_row = {
        row_number: region.key
        for region in regions
        for row_number in region.product_rows
    }
    for row_number, raw in enumerate(grid[header_index + 1 :], start=header_index + 2):
        if not is_menu_product_row(raw):
            continue
        for key in _existing_name_keys(raw):
            by_name.setdefault(key, row_number)
            region_key = region_of_row.get(row_number)
            if region_key:
                by_region_name.setdefault((region_key, key), row_number)
    return by_region_name, by_name


def _match_menu_row(
    keys: set[str],
    region: MenuRegion | None,
    by_region_name: dict[tuple[str, str], int],
    by_name: dict[str, int],
    used_rows: set[int],
    locked: set[int],
) -> int | None:
    if region is not None:
        for key in keys:
            row_number = by_region_name.get((region.key, key))
            if row_number and row_number not in used_rows and row_number not in locked:
                return row_number
    for key in keys:
        row_number = by_name.get(key)
        if row_number and row_number not in used_rows and row_number not in locked:
            return row_number
    return None


def _next_region_slot(region: MenuRegion, used_rows: set[int], locked: set[int]) -> int | None:
    for row_number in region.empty_rows:
        if row_number not in used_rows and row_number not in locked:
            return row_number
    return None


def _append_menu_row_changes(
    cells: list[tuple[str, Any]],
    grid: list[list[Any]],
    row_number: int,
    wanted: list[Any],
    locked: set[int],
) -> None:
    if row_number in locked:
        return
    raw = grid[row_number - 1] if row_number - 1 < len(grid) else []
    for column, value in enumerate(wanted[:MENU_COLUMNS]):
        outgoing = _outgoing(value)
        if _same(_at(raw, column), outgoing):
            continue
        cells.append((_cell_range(TAB_MENU, column, row_number), outgoing))


def _sanitize_existing_menu_descriptions(
    grid: list[list[Any]],
    header_index: int,
    cells: list[tuple[str, Any]],
    locked: set[int],
) -> None:
    del locked
    pending = {cell_range for cell_range, _value in cells}
    for row_number, raw in enumerate(grid[header_index + 1 :], start=header_index + 2):
        current = _at(raw, 2)
        if _blank(current):
            continue
        cleaned = _public_description(current)
        if _same(current, cleaned):
            continue
        target = _cell_range(TAB_MENU, 2, row_number)
        if target in pending:
            continue
        cells.append((target, _outgoing(cleaned)))


def manual_quote_rows(grid: list[list[Any]] | None) -> tuple[list[dict[str, Any]], str]:
    """Đọc NASTELEID cùng cột khóa với BAO_GIA. Giá giữ nguyên như đã gõ."""

    rows, notes = _records_from_grid(grid, default_source=SOURCE_MANUAL, only_source=SOURCE_MANUAL)
    return rows, " ".join(notes)


def plan_tab(
    tab: str,
    grid: list[list[Any]] | None,
    desired: list[dict[str, Any]],
    *,
    exists: bool,
    sources_to_refresh: set[str],
) -> TabPlan:
    """Ghi các cột khóa. Dòng 1 phải đúng mẫu. Không xóa cột phụ bên phải."""

    if tab not in DATA_TABS:
        raise ConfigError(f"Từ chối ghi dữ liệu tab '{tab}'.")
    wanted, order = _desired_index(desired)
    if not exists or not grid:
        return TabPlan(
            tab=tab,
            create=not exists,
            full_grid=_full_grid(order, wanted),
            format_ui=True,
        )

    headers = [_header(cell) for cell in grid[0]]
    if not _headers_locked(headers):
        existing_sources = _sources_in_grid(grid, headers)
        if existing_sources - sources_to_refresh:
            return TabPlan(
                tab=tab,
                warning=(
                    f"Tab {tab} chưa đúng dòng 1 khóa UI và còn nguồn chưa làm mới. "
                    "Không ghi để giữ dòng cũ."
                ),
            )
        return TabPlan(
            tab=tab,
            full_grid=_padded_full_grid(order, wanted, len(grid)),
            warning=f"Tab {tab} đã khóa lại dòng 1 theo mẫu UI thống nhất.",
            format_ui=True,
        )

    index = _header_index(headers)
    existing = _existing_keys(grid, index)
    cells: list[tuple[str, Any]] = []
    for key, row_number in existing.items():
        source, _stt = key
        if source not in sources_to_refresh:
            continue
        if key not in wanted:
            cells.extend(_clears(tab, grid, row_number, index))
            continue
        cells.extend(_changes(tab, grid, row_number, index, wanted[key]))
    next_row = len(grid) + 1
    for key in order:
        if key in existing:
            continue
        for name in QUOTE_HEADERS:
            column = index.get(name)
            if column is None:
                continue
            cells.append((_cell_range(tab, column, next_row), _outgoing(wanted[key].get(name, ""))))
        next_row += 1
    return TabPlan(tab=tab, cells=cells, format_ui=True)


def build_quote_format_requests(
    sheet_id: int,
    headers: list[str],
    row_count: int,
    *,
    banding_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Header xanh, đóng băng dòng 1, dải màu, khổ cột, số Giá bán. Không chứa bí mật."""

    requests: list[dict[str, Any]] = []
    for band_id in banding_ids or []:
        requests.append({"deleteBanding": {"bandedRangeId": band_id}})
    columns = max(len(headers), 1)
    end_row = max(row_count, 2)
    requests.append(
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": columns,
                },
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
    if "Giá bán" in headers:
        column = headers.index("Giá bán")
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
                    "properties": {"pixelSize": _COLUMN_WIDTHS.get(header, 140)},
                    "fields": "pixelSize",
                }
            }
        )
    return requests


class CombinedQuoteWriter:
    def __init__(self, gateway: QuoteGateway, spreadsheet_id: str) -> None:
        cleaned = spreadsheet_id.strip()
        if not cleaned:
            raise ConfigError("Thiếu LAST_SPREADSHEET_ID.")
        self.gateway = gateway
        self.spreadsheet_id = cleaned

    def update(
        self,
        shop_rows: list[dict[str, Any]],
        *,
        dry_run: bool,
        nastele_rows: list[dict[str, Any]] | None = None,
    ) -> QuoteUpdate:
        titles = set(self.gateway.list_sheet_titles(self.spreadsheet_id))
        warnings: list[str] = []
        plans: list[TabPlan] = []
        manual_rows: list[dict[str, Any]] = []
        refresh_manual = False
        auto_nastele = bool(nastele_rows)

        if auto_nastele:
            wanted, order = _desired_index(nastele_rows or [])
            manual_rows = [wanted[key] for key in order]
            refresh_manual = True
            plans.append(
                TabPlan(
                    tab=TAB_MANUAL,
                    create=TAB_MANUAL not in titles,
                    full_grid=_padded_full_grid(
                        order,
                        wanted,
                        len(self._read(TAB_MANUAL)) if TAB_MANUAL in titles else 1,
                    ),
                    format_ui=True,
                    allow_manual_data=True,
                    warning="Đã ghi NASTELEID từ catalog Nastele.",
                )
            )
        elif TAB_MANUAL not in titles:
            plans.append(
                TabPlan(
                    tab=TAB_MANUAL,
                    create=True,
                    full_grid=[list(QUOTE_HEADERS)],
                    format_ui=True,
                    warning="Đã tạo tab NASTELEID với dòng tiêu đề khóa. Nhập tay từ dòng 2.",
                )
            )
        else:
            manual_grid = self._read(TAB_MANUAL)
            if _blank_manual(manual_grid):
                plans.append(
                    TabPlan(
                        tab=TAB_MANUAL,
                        full_grid=[list(QUOTE_HEADERS)],
                        format_ui=True,
                        warning="Tab NASTELEID trống. Đã khóa dòng tiêu đề. Nhập tay từ dòng 2.",
                    )
                )
            else:
                manual_rows, warning = manual_quote_rows(manual_grid)
                if warning:
                    warnings.append(warning)
                refresh_manual = True
                plans.append(TabPlan(tab=TAB_MANUAL, format_ui=True))

        refresh_shop = bool(shop_rows)
        if not refresh_shop:
            warnings.append(
                "API shop không có dòng giá. Giữ nguyên phần shop trên BAO_GIA và bảng tổng hợp."
            )

        if refresh_shop:
            plans.append(self._plan(TAB_AUTO, titles, shop_rows, sources={SOURCE_SHOP}))

        combined: list[dict[str, Any]] = []
        sources: set[str] = set()
        if refresh_manual:
            combined.extend(manual_rows)
            sources.add(SOURCE_MANUAL)
        if refresh_shop:
            combined.extend(shop_rows)
            sources.add(SOURCE_SHOP)
        elif TAB_COMBINED in titles:
            kept = [
                row
                for row in _records_from_grid(self._read(TAB_COMBINED))[0]
                if row.get("Nguồn") == SOURCE_SHOP
            ]
            if kept:
                combined.extend(kept)
                sources.add(SOURCE_SHOP)
        if sources:
            plans.append(self._plan(TAB_COMBINED, titles, combined, sources=sources))

        menu_count = 0
        if TAB_MENU in titles:
            menu_plan, menu_count = self._plan_menu(shop_rows, manual_rows)
            if menu_plan is not None:
                plans.append(menu_plan)
        else:
            warnings.append("Bỏ qua MENU vì tab chưa có. Không tạo mới.")

        for plan in plans:
            if plan.warning:
                warnings.append(plan.warning)
        ranges = [item for plan in plans for item in plan.ranges()]
        for plan in plans:
            for item in plan.ranges():
                if (
                    item.startswith(f"'{TAB_MANUAL}'!")
                    and not item.endswith("!A1")
                    and not plan.allow_manual_data
                ):
                    raise ConfigError("Từ chối ghi dữ liệu tab NASTELEID.")
        applied = False
        if not dry_run and plans:
            self._apply(plans)
            applied = True
        return QuoteUpdate(
            shop_count=len(shop_rows),
            manual_count=len(manual_rows),
            nastele_count=len(nastele_rows or []),
            menu_count=menu_count,
            warnings=warnings,
            ranges=ranges,
            applied=applied,
        )

    def _plan(
        self,
        tab: str,
        titles: set[str],
        desired: list[dict[str, Any]],
        *,
        sources: set[str],
    ) -> TabPlan:
        exists = tab in titles
        grid = self._read(tab) if exists else []
        return plan_tab(tab, grid, desired, exists=exists, sources_to_refresh=sources)

    def _read(self, tab: str) -> list[list[Any]]:
        grid = self.gateway.get_values(self.spreadsheet_id, f"'{tab}'!A1:ZZ")
        if not isinstance(grid, list):
            raise ConfigError(f"Tab {tab} trả về dữ liệu không hợp lệ.")
        return grid

    def _plan_menu(
        self,
        shop_rows: list[dict[str, Any]],
        nastele_rows: list[dict[str, Any]],
    ) -> tuple[TabPlan | None, int]:
        grid = self._read(TAB_MENU)
        header_index = _menu_header_index(grid)
        if header_index is None:
            return (
                TabPlan(
                    tab=TAB_MENU,
                    warning="MENU chưa có dòng ID | PRODUCT. Không ghi để giữ layout chính thức.",
                ),
                0,
            )
        cells, count, notes = plan_menu_cells(grid, shop_rows, nastele_rows, header_index)
        return (
            TabPlan(
                tab=TAB_MENU,
                cells=cells,
                warning=" ".join(note for note in notes if note),
            ),
            count,
        )

    def _apply(self, plans: list[TabPlan]) -> None:
        for plan in plans:
            if plan.tab == TAB_MANUAL and plan.cells and not plan.allow_manual_data:
                raise ConfigError("Từ chối ghi dữ liệu tab NASTELEID.")
            if (
                plan.tab == TAB_MANUAL
                and plan.full_grid is not None
                and len(plan.full_grid) > 1
                and not plan.allow_manual_data
            ):
                raise ConfigError("Từ chối ghi dữ liệu tab NASTELEID.")
            if plan.tab not in WRITE_TABS:
                raise ConfigError(f"Từ chối ghi tab '{plan.tab}'.")
            if plan.tab == TAB_MENU and plan.full_grid is not None:
                raise ConfigError("Từ chối ghi đè toàn bộ MENU. Chỉ cập nhật ô sản phẩm.")
            if plan.create:
                self.gateway.add_sheet(self.spreadsheet_id, plan.tab)
            unmerge = self._unmerge_requests(plan)
            if unmerge:
                self.gateway.batch_update(self.spreadsheet_id, unmerge)
            payload: list[tuple[str, list[list[Any]]]] = []
            if plan.full_grid is not None:
                payload.append((f"'{plan.tab}'!{plan.origin}", plan.full_grid))
            payload.extend((cell_range, [[value]]) for cell_range, value in plan.cells)
            if payload:
                self.gateway.batch_update_values(
                    self.spreadsheet_id,
                    payload,
                    value_input_option="RAW",
                )
        self._lock_ui(plans)

    def _lock_ui(self, plans: list[TabPlan]) -> None:
        needed = {plan.tab for plan in plans if plan.format_ui}
        if not needed:
            return
        meta = {
            str(item.get("title")): item
            for item in self.gateway.list_sheet_meta(self.spreadsheet_id)
            if isinstance(item, dict)
        }
        requests: list[dict[str, Any]] = []
        for tab in LOCKED_TABS:
            if tab not in needed:
                continue
            info = meta.get(tab)
            if not info or not isinstance(info.get("sheetId"), int):
                continue
            grid = self._read(tab)
            row_count = max(len(grid), 2)
            requests.extend(
                build_quote_format_requests(
                    int(info["sheetId"]),
                    list(QUOTE_HEADERS),
                    row_count,
                    banding_ids=list(info.get("bandingIds") or []),
                )
            )
        if requests:
            self.gateway.batch_update(self.spreadsheet_id, requests)

    def _unmerge_requests(self, plan: TabPlan) -> list[dict[str, Any]]:
        del plan
        return []


def quote_layout_tables() -> list[tuple[str, list[str], list[list[str]]]]:
    columns = " | ".join(QUOTE_HEADERS)
    stock = " / ".join(STOCK_VALUES)
    return [
        (
            "Hai file Google",
            ["File", "Biến môi trường", "Tab liên quan", "Ai ghi"],
            [
                [
                    "Catalog Shop",
                    "GOOGLE_SPREADSHEET_ID",
                    "SAN_PHAM, BIEN_THE, BANG_GIA_BAN",
                    "Tool ghi catalog. Không copy sang file tổng hợp.",
                ],
                [
                    "Báo giá chính thức",
                    "LAST_SPREADSHEET_ID",
                    "MENU, NASTELEID, BAO_GIA, BAO_GIA_TONG_HOP",
                    "MENU là bảng khách. Tool ghi MENU, BAO_GIA, BAO_GIA_TONG_HOP. Nastele vào NASTELEID khi có catalog.",
                ],
            ],
        ),
        (
            "Cột khóa — dòng 1 của NASTELEID và BAO_GIA",
            ["Cột", "BAO_GIA (API shop)", "NASTELEID (nhập tay)"],
            [
                ["STT", "1, 2, 3… theo thứ tự catalog. Không lấy local id.", "1, 2, 3… do bạn đánh."],
                ["Nguồn", "Shop Xoài Non", "NASTELEID hoặc để trống."],
                ["Tên sản phẩm", "title sản phẩm", "Tên khách thấy."],
                ["Tên biến thể", "title biến thể", "Gói / thời hạn."],
                ["Mô tả", "description biến thể", "Mô tả tay."],
                ["Giá bán", "Giá shop đã + lợi nhuận, làm tròn đẹp.", "Giá đã gõ, không làm tròn lại."],
                ["Tình trạng", stock, stock],
            ],
        ),
        (
            "Không copy từ file catalog",
            ["Cột catalog", "Vì sao sai", "Cột đúng"],
            [
                ["Product ID / Variant ID / local_…", "Không đưa mã nội bộ lên bảng khách.", "STT"],
                ["Giá gốc / Retail Cost", "Giá vốn.", "Giá bán"],
                ["Lợi nhuận / %", "Nội bộ.", "(không có)"],
            ],
        ),
        (
            "Cách điền NASTELEID",
            ["Dòng 1", "Dòng dữ liệu", "Tool làm gì"],
            [
                [
                    columns,
                    "1 | NASTELEID | Canva Pro | Gói năm | Học sinh | 102700 | Còn hàng",
                    "Khóa định dạng. Không ghi dữ liệu. Không lấy local id API.",
                ]
            ],
        ),
    ]


def render_quote_layout() -> str:
    blocks = [title + "\n" + _markdown_table(headers, rows) for title, headers, rows in quote_layout_tables()]
    return "\n\n".join(blocks) + "\n"


def _records_from_grid(
    grid: list[list[Any]] | None,
    *,
    default_source: str = "",
    only_source: str = "",
) -> tuple[list[dict[str, Any]], list[str]]:
    if not grid:
        return [], []
    headers = [_header(cell) for cell in grid[0]]
    index = _header_index(headers)
    notes: list[str] = []
    cost_hits = [name for name in FORBIDDEN_COST_HEADERS if name in index]
    if cost_hits:
        notes.append(
            "Có cột giá vốn "
            + ", ".join(cost_hits)
            + ". Không lấy các cột này. Chỉ dùng cột Giá bán."
        )
    if "Tên sản phẩm" not in index:
        return [], ["Thiếu cột Tên sản phẩm. Không đọc dòng dữ liệu."]
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    skipped_foreign = 0
    for offset, raw in enumerate(grid[1:], start=1):
        source = _key_part(_at(raw, index.get("Nguồn"))) or default_source
        if only_source and source and source != only_source:
            skipped_foreign += 1
            continue
        if only_source:
            source = only_source
        name = _at(raw, index.get("Tên sản phẩm"))
        price = _at(raw, index.get("Giá bán"))
        if _blank(name) and _blank(price):
            continue
        serial = _key_part(_at(raw, index.get("STT"))) or str(offset)
        key = f"{source}|{serial}"
        if key in seen:
            continue
        seen.add(key)
        variant = _at(raw, index.get("Tên biến thể"))
        if _blank(variant):
            variant = _at(raw, index.get("Cấu hình"))
        description = _public_description(_at(raw, index.get("Mô tả")))
        rows.append(
            {
                "STT": int(serial) if serial.isdigit() else offset,
                "Nguồn": source or default_source,
                "Tên sản phẩm": name,
                "Tên biến thể": variant,
                "Mô tả": description,
                "Giá bán": price,
                "Tình trạng": _at(raw, index.get("Tình trạng")),
            }
        )
    if skipped_foreign:
        notes.append(
            f"Bỏ {skipped_foreign} dòng nguồn khác. Chỉ lấy dòng Nguồn trống hoặc {only_source}."
        )
    return rows, notes


def _menu_header_index(grid: list[list[Any]] | None) -> int | None:
    for index, row in enumerate(grid or []):
        cells = [_header(cell).upper() for cell in row[:MENU_COLUMNS]]
        if cells[:2] == ["ID", "PRODUCT"]:
            return index
    return None


def _origin_row(origin: str) -> int:
    digits = "".join(char for char in origin if char.isdigit())
    return int(digits) if digits else 1


def _merge_overlaps(
    merge: dict[str, Any], start_row: int, end_row: int, start_col: int, end_col: int
) -> bool:
    merge_start_row = int(merge.get("startRowIndex") or 0)
    merge_end_row = int(merge.get("endRowIndex") or 0)
    merge_start_col = int(merge.get("startColumnIndex") or 0)
    merge_end_col = int(merge.get("endColumnIndex") or 0)
    return (
        merge_start_row < end_row
        and merge_end_row > start_row
        and merge_start_col < end_col
        and merge_end_col > start_col
    )


def _menu_body_rows(grid: list[list[Any]], header_index: int) -> int:
    last = 0
    for offset, row in enumerate(grid[header_index + 1 :], start=1):
        if any(not _blank(cell) for cell in row[:MENU_COLUMNS]):
            last = offset
    return last


def _blank_manual(grid: list[list[Any]]) -> bool:
    if not grid:
        return True
    if len(grid) == 1 and [_header(cell) for cell in grid[0]] == list(QUOTE_HEADERS):
        return False
    return not any(any(not _blank(cell) for cell in row) for row in grid)


def _headers_locked(headers: list[str]) -> bool:
    locked = [_header(name) for name in headers[: len(QUOTE_HEADERS)]]
    return locked == list(QUOTE_HEADERS)


def _sources_in_grid(grid: list[list[Any]], headers: list[str]) -> set[str]:
    index = _header_index(headers)
    column = index.get("Nguồn")
    if column is None:
        return set()
    found: set[str] = set()
    for raw in grid[1:]:
        source = _key_part(_at(raw, column))
        if source:
            found.add(source)
    return found


def _desired_index(
    desired: list[dict[str, Any]],
) -> tuple[dict[tuple[str, str], dict[str, Any]], list[tuple[str, str]]]:
    found: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    for row in desired:
        key = (_key_part(row.get("Nguồn")), _key_part(row.get("STT")))
        if not key[0] or not key[1] or key in found:
            continue
        found[key] = row
        order.append(key)
    return found, order


def _existing_keys(
    grid: list[list[Any]], index: dict[str, int]
) -> dict[tuple[str, str], int]:
    found: dict[tuple[str, str], int] = {}
    source_col = index["Nguồn"]
    stt_col = index["STT"]
    for offset, raw in enumerate(grid[1:], start=2):
        key = (_key_part(_at(raw, source_col)), _key_part(_at(raw, stt_col)))
        if not key[0] or not key[1] or key in found:
            continue
        found[key] = offset
    return found


def _changes(
    tab: str,
    grid: list[list[Any]],
    row_number: int,
    index: dict[str, int],
    wanted: dict[str, Any],
) -> list[tuple[str, Any]]:
    cells: list[tuple[str, Any]] = []
    raw = grid[row_number - 1] if row_number - 1 < len(grid) else []
    for name in QUOTE_HEADERS:
        column = index.get(name)
        if column is None:
            continue
        outgoing = _outgoing(wanted.get(name, ""))
        if _same(_at(raw, column), outgoing):
            continue
        cells.append((_cell_range(tab, column, row_number), outgoing))
    return cells


def _clears(
    tab: str, grid: list[list[Any]], row_number: int, index: dict[str, int]
) -> list[tuple[str, Any]]:
    cells: list[tuple[str, Any]] = []
    raw = grid[row_number - 1] if row_number - 1 < len(grid) else []
    for name in QUOTE_HEADERS:
        column = index.get(name)
        if column is None or _blank(_at(raw, column)):
            continue
        cells.append((_cell_range(tab, column, row_number), ""))
    return cells


def _full_grid(
    order: list[tuple[str, str]], wanted: dict[tuple[str, str], dict[str, Any]]
) -> list[list[Any]]:
    grid: list[list[Any]] = [list(QUOTE_HEADERS)]
    for key in order:
        row = wanted[key]
        grid.append([_outgoing(row.get(header, "")) for header in QUOTE_HEADERS])
    return grid


def _padded_full_grid(
    order: list[tuple[str, str]],
    wanted: dict[tuple[str, str], dict[str, Any]],
    previous_rows: int,
) -> list[list[Any]]:
    grid = _full_grid(order, wanted)
    blank = [""] * len(QUOTE_HEADERS)
    while len(grid) < previous_rows:
        grid.append(list(blank))
    return grid


def _outgoing(value: Any) -> Any:
    if isinstance(value, str):
        return guard_formula(value)
    return "" if value is None else value


def _same(current: Any, desired: Any) -> bool:
    if current == desired:
        return True
    if _blank(current) and _blank(desired):
        return True
    left = _number(current)
    right = _number(desired)
    if left is not None and right is not None:
        return left == right
    return False


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or isinstance(value, str):
        if isinstance(value, str) and value.strip().isdigit():
            return float(value.strip())
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _header(value: Any) -> str:
    return str(value or "").strip()


def _header_index(headers: list[str]) -> dict[str, int]:
    index: dict[str, int] = {}
    for position, name in enumerate(headers):
        if name and name not in index:
            index[name] = position
    return index


def _at(row: list[Any], column: int | None) -> Any:
    if column is None or column >= len(row):
        return ""
    value = row[column]
    return "" if value is None else value


def _key_part(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    return str(value).strip()


def _cell_range(tab: str, column: int, row: int) -> str:
    return f"'{tab}'!{_column_name(column)}{row}"


def _column_name(index: int) -> str:
    number = index + 1
    name = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for position, cell in enumerate(row):
            widths[position] = max(widths[position], len(cell))

    def line(cells: list[str]) -> str:
        return "| " + " | ".join(cell.ljust(widths[index]) for index, cell in enumerate(cells)) + " |"

    rule = "| " + " | ".join("-" * width for width in widths) + " |"
    return "\n".join([line(headers), rule, *(line(row) for row in rows)])
