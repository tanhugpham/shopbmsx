"""Phase 4.3J: gom sản phẩm cùng loại trên MENU thành cụm liền nhau.

Chỉ được phép đổi *thứ tự dòng* trong vùng sản phẩm A:H của từng vùng danh mục
đang có. Nguyên tắc:

* không thêm/bớt dòng, không thêm cột, không thêm Category Header;
* không gộp nhiều sản phẩm vào một dòng, không cộng dồn tồn kho;
* di chuyển nguyên bộ A:H nên ID, tên, mô tả, thời hạn, bảo hành, giá, tồn kho
  và trạng thái luôn đi cùng nhau;
* không ghi J:M, không sửa merge/công thức/dropdown/định dạng điều kiện;
* thứ tự cụm lấy theo lần xuất hiện đầu tiên nên ổn định qua các lượt đồng bộ.

Sản phẩm mới cùng loại chỉ cần nằm trong vùng sản phẩm là tự động rơi cạnh cụm
của nó, vì cụm được dựng lại từ tên sản phẩm ở mỗi lượt chạy.
"""

from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass
from typing import Any, Sequence

from shop_integration.combined_quote import (
    TAB_MENU,
    is_menu_product_row,
    menu_header_row,
    parse_menu_regions,
)
from shop_integration.errors import MenuLayoutError
from shop_integration.menu_layout import (
    MENU_WRITABLE_COLUMNS,
    MenuCapacity,
    cell_range_for,
)

GROUP_SIMILAR_ENV = "MENU_GROUP_SIMILAR"
ORDER_CELLS = len(MENU_WRITABLE_COLUMNS)  # A..H
_ID_COLUMN = 0
_NAME_COLUMN = 1

# Bảng nhận diện loại sản phẩm theo tên thật trên MENU.
# Thứ tự trong bảng là ưu tiên khi hai loại cùng khớp ở một vị trí trong tên.
_FAMILY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("chatgpt", ("chatgpt", "chat gpt", "gpt 4", "gpt 5", "gpt4", "gpt5", "openai")),
    ("claude", ("claude", "anthropic")),
    ("gemini", ("gemini", "google ai", "bard")),
    ("grok", ("grok", "xai")),
    ("copilot", ("copilot",)),
    ("perplexity", ("perplexity",)),
    ("cursor", ("cursor",)),
    ("midjourney", ("midjourney",)),
    ("elevenlabs", ("elevenlabs", "eleven labs")),
    ("muse", ("muse",)),
    ("canva", ("canva",)),
    ("adobe", ("adobe", "photoshop", "illustrator", "lightroom", "acrobat", "premiere")),
    ("capcut", ("capcut", "cap cut")),
    ("figma", ("figma",)),
    ("meitu", ("meitu",)),
    ("wink", ("wink",)),
    ("duolingo", ("duolingo",)),
    ("memrise", ("memrise",)),
    ("spotify", ("spotify",)),
    ("youtube", ("youtube", "youtube premium")),
    ("netflix", ("netflix",)),
    (
        "office",
        ("microsoft office", "office 365", "office365", "microsoft 365", "ms office"),
    ),
    ("windows", ("windows", "win 10", "win 11")),
    ("google drive", ("google drive", "google one", "gdrive", "gg drive")),
    ("dropbox", ("dropbox",)),
    ("onedrive", ("onedrive",)),
    ("icloud", ("icloud",)),
    ("nordvpn", ("nordvpn", "nord vpn")),
    ("vpn", ("vpn",)),
    ("grammarly", ("grammarly",)),
    ("linkedin", ("linkedin",)),
    ("zoom", ("zoom",)),
    ("telegram", ("telegram",)),
    ("discord", ("discord",)),
)


def may_group_similar() -> bool:
    """Bật/tắt gom cụm. Mặc định bật; ``MENU_GROUP_SIMILAR=false`` để tắt."""

    raw = os.environ.get(GROUP_SIMILAR_ENV, "true").strip().lower()
    return raw not in {"false", "0", "no", "off", ""}


def fold_menu_text(text: object) -> str:
    """Chuẩn hóa tên sản phẩm: bỏ dấu, chữ thường, mọi ký tự lạ thành khoảng trắng."""

    raw = str(text or "")
    decomposed = unicodedata.normalize("NFD", raw)
    stripped = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    lowered = stripped.lower().replace("đ", "d")
    cleaned = "".join(char if char.isalnum() else " " for char in lowered)
    return " ".join(cleaned.split())


def _tokens(pattern: str) -> tuple[str, ...]:
    return tuple(fold_menu_text(pattern).split())


_FAMILY_TERMS: tuple[tuple[str, tuple[tuple[str, ...], ...]], ...] = tuple(
    (family, tuple(_tokens(term) for term in terms)) for family, terms in _FAMILY_RULES
)


def _first_position(tokens: Sequence[str], term: tuple[str, ...]) -> int | None:
    if not term:
        return None
    width = len(term)
    for index in range(len(tokens) - width + 1):
        if tuple(tokens[index : index + width]) == term:
            return index
    return None


def menu_family(name: object) -> str:
    """Khóa loại sản phẩm theo tên. Tên lạ dùng chính tên làm khóa để không trộn loại."""

    tokens = fold_menu_text(name).split()
    if not tokens:
        return "~"
    best: tuple[int, str] | None = None
    for family, terms in _FAMILY_TERMS:
        family_position: int | None = None
        for term in terms:
            position = _first_position(tokens, term)
            if position is None:
                continue
            if family_position is None or position < family_position:
                family_position = position
        if family_position is None:
            continue
        if best is None or family_position < best[0]:
            best = (family_position, family)
    if best is not None:
        return best[1]
    return "~" + " ".join(tokens[:2])


@dataclass(frozen=True)
class MenuCluster:
    """Một cụm sản phẩm cùng loại, giữ nguyên thứ tự xuất hiện ban đầu."""

    family: str
    title: str
    rows: tuple[int, ...] = ()

    @property
    def size(self) -> int:
        return len(self.rows)


@dataclass(frozen=True)
class MenuOrderPlan:
    """Kế hoạch đổi thứ tự: ``slots[i]`` nhận nguyên bộ A:H của ``order[i]``."""

    slots: tuple[int, ...] = ()
    order: tuple[int, ...] = ()
    clusters: tuple[MenuCluster, ...] = ()
    moved: int = 0
    notes: tuple[str, ...] = ()

    @property
    def identity(self) -> bool:
        return not self.slots or self.moved == 0

    @property
    def first_row(self) -> int:
        return min(self.slots) if self.slots else 0

    @property
    def last_row(self) -> int:
        return max(self.slots) if self.slots else 0

    @property
    def size(self) -> int:
        return len(self.slots)

    def summary(self) -> str:
        if not self.slots:
            return "MENU gom cụm: không có vùng sản phẩm nào để sắp xếp."
        if self.identity:
            return (
                f"MENU gom cụm: {self.size} dòng đã nằm đúng cụm, không đổi vị trí."
            )
        names = ", ".join(
            f"{cluster.title} ×{cluster.size}" for cluster in self.clusters[:6]
        )
        more = f" (+{len(self.clusters) - 6} cụm nữa)" if len(self.clusters) > 6 else ""
        return (
            f"MENU gom cụm: {self.size} sản phẩm, {len(self.clusters)} cụm cùng loại, "
            f"đổi vị trí {self.moved} dòng ({names}{more})."
        )


def cluster_menu_rows(
    rows: Sequence[int],
    grid: Sequence[Sequence[Any]],
) -> list[MenuCluster]:
    """Gom dòng sản phẩm thành cụm theo loại, giữ thứ tự cụm lần đầu xuất hiện."""

    buckets: dict[str, list[int]] = {}
    titles: dict[str, str] = {}
    for row_number in rows:
        name = menu_cell(grid, row_number, _NAME_COLUMN)
        family = menu_family(name)
        buckets.setdefault(family, []).append(row_number)
        titles.setdefault(family, str(name or "").strip())
    return [
        MenuCluster(family=family, title=titles[family], rows=tuple(items))
        for family, items in buckets.items()
    ]


def menu_cell(grid: Sequence[Sequence[Any]], row_number: int, column: int) -> Any:
    """Giá trị một ô A:H theo dòng 1-based. Ngoài vùng thì trả chuỗi rỗng."""

    index = row_number - 1
    if index < 0 or index >= len(grid):
        return ""
    raw = grid[index]
    if not isinstance(raw, (list, tuple)) or column >= len(raw):
        return ""
    return raw[column]


def menu_row_values(grid: Sequence[Sequence[Any]], row_number: int) -> list[Any]:
    """Nguyên bộ A:H của một dòng, đệm ô trống để luôn đủ 8 cột."""

    values = [menu_cell(grid, row_number, column) for column in range(ORDER_CELLS)]
    return ["" if value is None else value for value in values]


def _row_has_formula(values: Sequence[Any]) -> bool:
    return any(str(value).strip().startswith("=") for value in values)


def _reorderable_row(
    grid: Sequence[Sequence[Any]],
    row_number: int,
    capacity: MenuCapacity | None,
) -> bool:
    """Dòng sản phẩm được phép đổi chỗ: không công thức, không merge/protected."""

    values = menu_row_values(grid, row_number)
    if _row_has_formula(values):
        return False
    if capacity is None:
        return True
    if not capacity.row_allowed(row_number) or not capacity.selector_covers(row_number):
        return False
    return not any(
        capacity.cell_blocked(column, row_number) for column in MENU_WRITABLE_COLUMNS
    )


def plan_menu_order(
    grid: Sequence[Sequence[Any]] | None,
    *,
    capacity: MenuCapacity | None = None,
) -> MenuOrderPlan:
    """Tính thứ tự mới cho từng vùng sản phẩm. Chỉ đọc, không ghi."""

    sheet = [list(row) for row in (grid or [])]
    header_index = menu_header_row(sheet)
    if header_index is None:
        return MenuOrderPlan(notes=("MENU chưa có dòng tiêu đề nên bỏ qua gom cụm.",))
    regions = parse_menu_regions(sheet, header_index, capacity=capacity)
    slots: list[int] = []
    order: list[int] = []
    clusters: list[MenuCluster] = []
    notes: list[str] = []
    for region in regions:
        rows = [
            row_number
            for row_number in region.product_rows
            if is_menu_product_row(sheet[row_number - 1] if row_number <= len(sheet) else [])
            and _reorderable_row(sheet, row_number, capacity)
        ]
        skipped = len(region.product_rows) - len(rows)
        if skipped:
            notes.append(
                f"Bỏ {skipped} dòng có công thức/merge trong A:H khỏi vùng gom cụm "
                f"(danh mục '{region.title or region.key}')."
            )
        if not rows:
            continue
        region_clusters = cluster_menu_rows(rows, sheet)
        sequence = [row_number for cluster in region_clusters for row_number in cluster.rows]
        slots.extend(rows)
        order.extend(sequence)
        clusters.extend(cluster for cluster in region_clusters if cluster.size > 1)
    moved = sum(1 for slot, source in zip(slots, order) if slot != source)
    notes.append(
        f"Vùng sắp xếp A:H: {len(slots)} dòng, {len(clusters)} cụm nhiều sản phẩm."
    )
    return MenuOrderPlan(
        slots=tuple(slots),
        order=tuple(order),
        clusters=tuple(clusters),
        moved=moved,
        notes=tuple(notes),
    )


def menu_order_cells(
    plan: MenuOrderPlan,
    grid: Sequence[Sequence[Any]],
) -> list[tuple[str, Any]]:
    """Danh sách ô cần ghi: nguyên bộ A:H từ dòng nguồn sang dòng đích."""

    cells: list[tuple[str, Any]] = []
    for slot, source in zip(plan.slots, plan.order):
        if slot == source:
            continue
        for column, value in enumerate(menu_row_values(grid, source)):
            cells.append((cell_range_for(TAB_MENU, column, slot), value))
    return cells


def expected_menu_rows(
    plan: MenuOrderPlan,
    grid: Sequence[Sequence[Any]],
) -> dict[int, list[Any]]:
    """Nội dung A:H mong đợi tại từng dòng đích sau khi sắp xếp."""

    return {slot: menu_row_values(grid, source) for slot, source in zip(plan.slots, plan.order)}


def menu_order_report(
    plan: MenuOrderPlan,
    before: Sequence[Sequence[Any]],
    after: Sequence[Sequence[Any]],
) -> list[str]:
    """Đối chiếu trước/sau: không mất, không trùng, giá và tồn kho vẫn khớp ID."""

    problems: list[str] = []
    before_rows = [menu_row_values(before, row) for row in plan.slots]
    after_rows = [menu_row_values(after, row) for row in plan.slots]
    if sorted(map(_hashable, before_rows)) != sorted(map(_hashable, after_rows)):
        problems.append("nội dung A:H trước và sau không khớp (mất hoặc trùng sản phẩm)")
    before_ids = _keyed_rows(before_rows)
    after_ids = _keyed_rows(after_rows)
    if set(before_ids) != set(after_ids):
        problems.append("danh sách ID sản phẩm thay đổi")
    for key, values in before_ids.items():
        if key and after_ids.get(key) != values:
            problems.append(f"ID {key} bị lệch giá/tồn kho sau khi sắp xếp")
            break
    return problems


def _hashable(values: Sequence[Any]) -> tuple[str, ...]:
    return tuple(str(value) for value in values)


def _keyed_rows(rows: Sequence[Sequence[Any]]) -> dict[str, tuple[Any, ...]]:
    """Bản đồ ID -> (PRODUCT, PRICE, STOCK, STATUS) để so khớp sau khi sắp xếp."""

    keyed: dict[str, tuple[Any, ...]] = {}
    for values in rows:
        key = str(values[_ID_COLUMN] or "").strip()
        if not key or key in keyed:
            continue
        keyed[key] = (
            values[_NAME_COLUMN],
            values[5],
            values[6],
            values[7],
        )
    return keyed


def apply_menu_order(
    gateway: Any,
    spreadsheet_id: str,
    plan: MenuOrderPlan,
    *,
    grid: Sequence[Sequence[Any]],
) -> int:
    """Ghi thứ tự mới rồi đọc lại kiểm tra. Trả số ô đã ghi."""

    if plan.identity:
        return 0
    cells = menu_order_cells(plan, grid)
    if not cells:
        return 0
    expected = expected_menu_rows(plan, grid)
    gateway.batch_update_values(
        spreadsheet_id,
        [(range_a1, [[value]]) for range_a1, value in cells],
        value_input_option="RAW",
    )
    after = gateway.get_values(spreadsheet_id, _zone_range(plan))
    problems = menu_order_report(plan, grid, after)
    for slot, values in expected.items():
        if menu_row_values(after, slot) != values:
            problems.append(f"dòng {slot} không khớp nội dung mong đợi")
            break
    if problems:
        raise MenuLayoutError(
            "Sắp xếp MENU xong nhưng kiểm tra lại không khớp: "
            + "; ".join(problems[:4])
            + ". Khôi phục từ bản backup gần nhất trong backups/."
        )
    return len(cells)


def _zone_range(plan: MenuOrderPlan) -> str:
    last = max(plan.last_row, 1)
    return f"'{TAB_MENU}'!A1:H{last}"
