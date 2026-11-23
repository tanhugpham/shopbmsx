"""Báo giá tổng hợp trên một spreadsheet riêng.

Ba tab dùng cùng một hàng tiêu đề. NASTELEID là phần nhập tay, không bao giờ ghi.
BAO_GIA nhận giá shop đã làm tròn đẹp.
BAO_GIA_TONG_HOP ghép hai nguồn và chép nguyên giá cùng cấu hình của từng nguồn.

Mọi lần ghi chỉ dùng values.update với RAW. Không gửi batchUpdate định dạng,
không đổi thứ tự cột, không viết lại dòng tiêu đề và không xóa dòng.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from shop_integration.errors import ConfigError
from shop_integration.models import Product
from shop_integration.pricing import PricingPolicy, quote_cost, round_pretty_vnd
from shop_integration.product_service import variant_stock_label
from shop_integration.text_normalize import guard_formula

TAB_MANUAL = "NASTELEID"
TAB_AUTO = "BAO_GIA"
TAB_COMBINED = "BAO_GIA_TONG_HOP"
WRITABLE_TABS = (TAB_AUTO, TAB_COMBINED)

SOURCE_MANUAL = "NASTELEID"
SOURCE_SHOP = "Shop Xoài Non"
MANUAL_ID = "Nastele ID"

QUOTE_HEADERS = ["Nguồn", "Mã", "Tên sản phẩm", "Giá bán", "Tình trạng", "Cấu hình"]
KEY_HEADERS = ("Nguồn", "Mã")
MANUAL_CODE_HEADERS = ("Mã", MANUAL_ID)
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


@dataclass
class TabPlan:
    tab: str
    create: bool = False
    full_grid: list[list[Any]] | None = None
    cells: list[tuple[str, Any]] = field(default_factory=list)
    warning: str = ""

    def ranges(self) -> list[str]:
        found: list[str] = []
        if self.full_grid is not None:
            found.append(f"'{self.tab}'!A1")
        found.extend(cell_range for cell_range, _value in self.cells)
        return found


@dataclass
class QuoteUpdate:
    shop_count: int
    manual_count: int
    warnings: list[str]
    ranges: list[str]
    applied: bool

    def note(self, *, dry_run: bool) -> str:
        parts = list(self.warnings)
        if dry_run:
            parts.append(
                f"Dry-run file tổng hợp: {self.shop_count} dòng shop, "
                f"{self.manual_count} dòng NASTELEID. Không ghi ô."
            )
        elif self.ranges:
            parts.append(
                "Đã cập nhật giá trị trên BAO_GIA và BAO_GIA_TONG_HOP. "
                "Không đổi định dạng. Không ghi NASTELEID."
            )
        else:
            parts.append("File tổng hợp không có ô cần đổi. Không ghi NASTELEID.")
        return " ".join(parts)


def shop_quote_rows(
    products: list[Product], policy: PricingPolicy | None = None
) -> list[dict[str, Any]]:
    """Mỗi biến thể có giá bán thành một dòng. Giá là giá đẹp sau lợi nhuận."""

    rules = policy or PricingPolicy()
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for product in products:
        product_name = (product.title or "").strip() or "Chưa có tên"
        for variant in product.variants:
            quote = quote_cost(variant.price_vnd, rules)
            if quote.selling is None:
                continue
            code = f"{product.id}|{variant.id}"
            key = (SOURCE_SHOP, code)
            if key in seen:
                continue
            seen.add(key)
            variant_name = (variant.title or "").strip()
            display = (
                f"{product_name} — {variant_name}"
                if variant_name and variant_name != product_name
                else product_name
            )
            rows.append(
                {
                    "Nguồn": SOURCE_SHOP,
                    "Mã": code,
                    "Tên sản phẩm": display,
                    "Giá bán": round_pretty_vnd(quote.selling),
                    "Tình trạng": variant_stock_label(variant),
                    "Cấu hình": (variant.description or "").strip(),
                }
            )
    return rows


def manual_quote_rows(grid: list[list[Any]] | None) -> tuple[list[dict[str, Any]], str]:
    """Đọc NASTELEID. Cùng cột với BAO_GIA. Giá giữ nguyên như đã gõ, không làm tròn."""

    if not grid:
        return [], ""
    headers = [_header(cell) for cell in grid[0]]
    index = _header_index(headers)
    if not any(name in index for name in MANUAL_CODE_HEADERS):
        return [], "Tab NASTELEID thiếu cột Mã (hoặc Nastele ID). Không đọc phần nhập tay."
    notes: list[str] = []
    cost_hits = [name for name in FORBIDDEN_COST_HEADERS if name in index]
    if cost_hits:
        notes.append(
            "Tab NASTELEID có cột giá vốn "
            + ", ".join(cost_hits)
            + ". Không lấy các cột này. Chỉ dùng cột Giá bán."
        )
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    skipped_foreign = 0
    for raw in grid[1:]:
        if _foreign_manual_source(raw, index):
            skipped_foreign += 1
            continue
        code = _manual_code(raw, index)
        if not code or code in seen:
            continue
        seen.add(code)
        rows.append(
            {
                "Nguồn": SOURCE_MANUAL,
                "Mã": code,
                "Tên sản phẩm": _at(raw, index.get("Tên sản phẩm")),
                "Giá bán": _at(raw, index.get("Giá bán")),
                "Tình trạng": _at(raw, index.get("Tình trạng")),
                "Cấu hình": _at(raw, index.get("Cấu hình")),
            }
        )
    if skipped_foreign:
        notes.append(
            f"Tab NASTELEID bỏ {skipped_foreign} dòng nguồn khác. "
            "Chỉ lấy dòng Nguồn trống hoặc NASTELEID."
        )
    return rows, " ".join(notes)


def plan_tab(
    tab: str,
    grid: list[list[Any]] | None,
    desired: list[dict[str, Any]],
    *,
    exists: bool,
    sources_to_refresh: set[str],
) -> TabPlan:
    """Lập các ô cần ghi. Không đụng cột ngoài danh sách và không xóa dòng."""

    if tab not in WRITABLE_TABS:
        raise ConfigError(f"Từ chối ghi tab '{tab}'.")
    wanted, order = _desired_index(desired)
    if not exists or not grid:
        return TabPlan(tab=tab, create=not exists, full_grid=_full_grid(order, wanted))

    headers = [_header(cell) for cell in grid[0]]
    index = _header_index(headers)
    if any(name not in index for name in KEY_HEADERS):
        return TabPlan(
            tab=tab,
            warning=(
                f"Tab {tab} thiếu cột Nguồn hoặc Mã ở dòng 1. "
                "Không ghi để giữ giao diện."
            ),
        )
    missing = [name for name in QUOTE_HEADERS if name not in KEY_HEADERS and name not in index]
    warning = (
        f"Tab {tab} thiếu cột {', '.join(missing)}. Chỉ cập nhật cột có sẵn."
        if missing
        else ""
    )
    existing = _existing_keys(grid, index)
    cells: list[tuple[str, Any]] = []
    for key, row_number in existing.items():
        source, _code = key
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
    return TabPlan(tab=tab, cells=cells, warning=warning)


class CombinedQuoteWriter:
    def __init__(self, gateway: QuoteGateway, spreadsheet_id: str) -> None:
        cleaned = spreadsheet_id.strip()
        if not cleaned:
            raise ConfigError("Thiếu LAST_SPREADSHEET_ID.")
        self.gateway = gateway
        self.spreadsheet_id = cleaned

    def update(self, shop_rows: list[dict[str, Any]], *, dry_run: bool) -> QuoteUpdate:
        titles = set(self.gateway.list_sheet_titles(self.spreadsheet_id))
        warnings: list[str] = []
        manual_rows: list[dict[str, Any]] = []
        refresh_manual = False
        if TAB_MANUAL not in titles:
            warnings.append(
                "Chưa có tab NASTELEID. Không tạo tab này và không xóa dòng nhập tay trên bảng tổng hợp."
            )
        else:
            manual_grid = self._read(TAB_MANUAL)
            manual_rows, warning = manual_quote_rows(manual_grid)
            if warning:
                warnings.append(warning)
            refresh_manual = "thiếu cột Mã" not in warning

        refresh_shop = bool(shop_rows)
        if not refresh_shop:
            warnings.append(
                "API shop không có dòng giá. Giữ nguyên phần shop trên BAO_GIA và bảng tổng hợp."
            )

        plans: list[TabPlan] = []
        if refresh_shop:
            plans.append(
                self._plan(TAB_AUTO, titles, shop_rows, sources={SOURCE_SHOP})
            )
        combined: list[dict[str, Any]] = []
        sources: set[str] = set()
        if refresh_manual:
            combined.extend(manual_rows)
            sources.add(SOURCE_MANUAL)
        if refresh_shop:
            combined.extend(shop_rows)
            sources.add(SOURCE_SHOP)
        if sources:
            plans.append(self._plan(TAB_COMBINED, titles, combined, sources=sources))

        for plan in plans:
            if plan.warning:
                warnings.append(plan.warning)
        ranges = [item for plan in plans for item in plan.ranges()]
        if any(TAB_MANUAL in item for item in ranges):
            raise ConfigError("Từ chối ghi tab NASTELEID.")
        applied = False
        if not dry_run and plans:
            self._apply(plans)
            applied = True
        return QuoteUpdate(
            shop_count=len(shop_rows),
            manual_count=len(manual_rows),
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

    def _apply(self, plans: list[TabPlan]) -> None:
        for plan in plans:
            if plan.tab not in WRITABLE_TABS:
                raise ConfigError(f"Từ chối ghi tab '{plan.tab}'.")
            if plan.create:
                self.gateway.add_sheet(self.spreadsheet_id, plan.tab)
            payload: list[tuple[str, list[list[Any]]]] = []
            if plan.full_grid is not None:
                payload.append((f"'{plan.tab}'!A1", plan.full_grid))
            payload.extend((cell_range, [[value]]) for cell_range, value in plan.cells)
            if not payload:
                continue
            self.gateway.batch_update_values(
                self.spreadsheet_id,
                payload,
                value_input_option="RAW",
            )


def quote_layout_tables() -> list[tuple[str, list[str], list[list[str]]]]:
    """Bảng UI thống nhất cho hai file Google. Dùng để in CLI và đối chiếu test."""

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
                    "Báo giá tổng hợp",
                    "LAST_SPREADSHEET_ID",
                    "NASTELEID, BAO_GIA, BAO_GIA_TONG_HOP",
                    "Người dùng chỉ sửa NASTELEID. Tool ghi hai tab còn lại.",
                ],
            ],
        ),
        (
            "Cột thống nhất — dòng 1 của NASTELEID, BAO_GIA, BAO_GIA_TONG_HOP",
            ["Cột", "Bắt buộc", "NASTELEID (nhập tay)", "BAO_GIA (tool)", "BAO_GIA_TONG_HOP (tool)"],
            [
                [
                    "Nguồn",
                    "Có trên BAO_GIA và tổng hợp",
                    "Điền NASTELEID hoặc để trống. Không điền Shop Xoài Non.",
                    "Luôn là Shop Xoài Non.",
                    "Copy nguyên từ nguồn gốc.",
                ],
                [
                    "Mã",
                    "Có",
                    "Mã Nastele, ví dụ 43. Cột cũ Nastele ID vẫn đọc được.",
                    "product_id|variant_id, ví dụ local_81|local_81.",
                    "Copy nguyên. Không dùng Product ID của SAN_PHAM.",
                ],
                [
                    "Tên sản phẩm",
                    "Có",
                    "Tên hiển thị cho khách.",
                    "Tên shop, ghép biến thể khi khác tên sản phẩm.",
                    "Copy nguyên từng nguồn.",
                ],
                [
                    "Giá bán",
                    "Có",
                    "Giá đã bán, giữ nguyên số đã gõ. Không lấy giá gốc.",
                    "Giá shop đã cộng lợi nhuận rồi làm tròn đẹp.",
                    "Copy nguyên. Không tính lại, không chọn nguồn rẻ.",
                ],
                [
                    "Tình trạng",
                    "Có",
                    stock,
                    "Còn hàng / Hết hàng / nhãn API shop.",
                    "Copy nguyên từng nguồn.",
                ],
                [
                    "Cấu hình",
                    "Không",
                    "Gói, thời hạn, ghi chú gói. Không dán mô tả HTML thô.",
                    "Mô tả biến thể đã chuẩn hóa.",
                    "Copy nguyên từng nguồn.",
                ],
            ],
        ),
        (
            "Không copy từ file catalog sang file tổng hợp",
            ["Cột catalog", "Vì sao sai", "Cột đúng trên file tổng hợp"],
            [
                ["Giá gốc / Giá thấp nhất (VND) / Retail Cost", "Đó là giá vốn.", "Giá bán"],
                ["Lợi nhuận (VND) / Tỷ lệ lợi nhuận (%)", "Nội bộ, không hiện cho khách.", "(không có)"],
                ["Product ID / Variant ID tách cột", "Khóa tổng hợp là một ô Mã.", "Mã"],
                ["Tên biến thể tách cột", "File tổng hợp ghép vào tên.", "Tên sản phẩm"],
                ["Trạng thái hàng / Availability", "Tên cột khác.", "Tình trạng"],
            ],
        ),
        (
            "Cách điền NASTELEID",
            ["Dòng 1", "Dòng dữ liệu", "Tool làm gì"],
            [
                [
                    columns,
                    "NASTELEID | 43 | Canva Pro 1 năm | 102700 | Còn hàng | Gói năm",
                    "Chỉ đọc. Không tạo tab. Không xóa dòng. Không đổi định dạng.",
                ]
            ],
        ),
    ]


def render_quote_layout() -> str:
    blocks = [title + "\n" + _markdown_table(headers, rows) for title, headers, rows in quote_layout_tables()]
    return "\n\n".join(blocks) + "\n"


def _manual_code(raw: list[Any], index: dict[str, int]) -> str:
    for name in MANUAL_CODE_HEADERS:
        code = _key_part(_at(raw, index.get(name)))
        if code:
            return code
    return ""


def _foreign_manual_source(raw: list[Any], index: dict[str, int]) -> bool:
    if "Nguồn" not in index:
        return False
    source = _key_part(_at(raw, index["Nguồn"]))
    return bool(source) and source != SOURCE_MANUAL


def _markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for position, cell in enumerate(row):
            widths[position] = max(widths[position], len(cell))

    def line(cells: list[str]) -> str:
        return "| " + " | ".join(cell.ljust(widths[index]) for index, cell in enumerate(cells)) + " |"

    rule = "| " + " | ".join("-" * width for width in widths) + " |"
    return "\n".join([line(headers), rule, *(line(row) for row in rows)])


def _desired_index(
    desired: list[dict[str, Any]],
) -> tuple[dict[tuple[str, str], dict[str, Any]], list[tuple[str, str]]]:
    found: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    for row in desired:
        key = (_key_part(row.get("Nguồn")), _key_part(row.get("Mã")))
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
    code_col = index["Mã"]
    for offset, raw in enumerate(grid[1:], start=2):
        key = (_key_part(_at(raw, source_col)), _key_part(_at(raw, code_col)))
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
