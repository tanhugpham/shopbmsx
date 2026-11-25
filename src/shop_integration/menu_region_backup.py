"""Snapshot vùng sắp ghi, lưu trên chính spreadsheet chính thức.

Tab ``_MENU_SYNC_BACKUP`` nằm cùng file Google đã chia sẻ cho service account.
Runner GitHub Actions bị xóa vẫn khôi phục được vì snapshot không nằm trên đĩa runner.
Không tạo file Drive mới và không đổi quyền chia sẻ. Không ghi cột I:M của MENU.
"""

from __future__ import annotations

import json
import os
from typing import Any

from shop_integration.backup_service import checksum, redact_payload
from shop_integration.errors import BackupError
from shop_integration.time_utils import format_timestamp, now_vietnam

OFFICIAL_SPREADSHEET_ID = "196goR1lDZN6zJfoC3a7vOVyo5u9o-pvxXpc3mnGrGSY"
BACKUP_TAB = "_MENU_SYNC_BACKUP"
CHUNK_CHARS = 30000
KEEP_SNAPSHOTS = 8
HEADER = ["SNAPSHOT_ID", "PART", "CHECKSUM", "BODY"]
REGIONS: tuple[tuple[str, str], ...] = (
    ("MENU", "A1:H250"),
    ("BAO_GIA", "A1:H400"),
    ("BAO_GIA_TONG_HOP", "A1:H400"),
)
RESTORE_PLAN = (
    "Khôi phục chỉ các vùng đã chụp: MENU!A1:H250, BAO_GIA!A1:H400, "
    "BAO_GIA_TONG_HOP!A1:H400. Ô bắt đầu bằng '=' là công thức, ghi bằng "
    "valueInputOption USER_ENTERED. Các ô còn lại ghi RAW. Xóa phần cuối vùng đã chụp "
    "trước khi ghi vì snapshot bỏ ô trống ở cuối dòng. Không ghi cột I:M của MENU. "
    "Không đổi merge, công thức J39/J40, dropdown K38 hay vùng chính sách bảo hành J:M. "
    "Không ghi NASTELEID, Trang tính1, hoặc tab khác. Không xóa sản phẩm nhập tay. "
    "Snapshot này là điểm trước lượt ghi."
)


def durable_backup_required() -> bool:
    """Live sync trên GitHub Actions luôn phải có backup bền trước khi ghi.

    Pytest trên runner CI cũng có ``GITHUB_ACTIONS=true``. Lúc đó chỉ bắt backup
    khi test chủ động bật ``MENU_REQUIRE_DURABLE_BACKUP``, để các bài test gateway
    giả không bị chặn. Workflow thật không chạy dưới pytest nên vẫn bị bắt buộc.
    """

    explicit = os.environ.get("MENU_REQUIRE_DURABLE_BACKUP", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return explicit
    if os.environ.get("GITHUB_ACTIONS", "").strip().lower() == "true":
        return True
    return explicit


def assert_official_spreadsheet(spreadsheet_id: str) -> None:
    if spreadsheet_id.strip() != OFFICIAL_SPREADSHEET_ID:
        raise BackupError(
            "GOOGLE_SPREADSHEET_ID không khớp spreadsheet chính thức. Không ghi MENU."
        )


def capture_regions(gateway: Any, spreadsheet_id: str) -> dict[str, Any]:
    titles = {str(title) for title in gateway.list_sheet_titles(spreadsheet_id)}
    regions: dict[str, Any] = {}
    for tab, a1 in REGIONS:
        key = f"{tab}!{a1}"
        if tab not in titles:
            regions[key] = {"present": False, "values": []}
            continue
        quoted = f"'{tab}'!{a1}"
        values = _read_range(gateway, spreadsheet_id, quoted, "UNFORMATTED_VALUE")
        formulas = _read_range(gateway, spreadsheet_id, quoted, "FORMULA")
        regions[key] = {"present": True, "values": _stable_rows(values, formulas)}
    return regions


def build_snapshot(regions: dict[str, Any], secrets: list[str]) -> tuple[dict[str, Any], str]:
    clean = redact_payload(regions, secrets)
    if not isinstance(clean, dict):
        raise BackupError("Snapshot vùng ghi không hợp lệ. Không ghi MENU.")
    text = _dump(clean)
    if "BEGIN PRIVATE KEY" in text or "END PRIVATE KEY" in text:
        raise BackupError("Snapshot còn khóa riêng tư. Không lưu và không ghi MENU.")
    for secret in secrets:
        if secret and secret in text:
            raise BackupError("Snapshot còn bí mật. Không lưu và không ghi MENU.")
    return clean, checksum(clean)


def store_region_backup(
    gateway: Any,
    spreadsheet_id: str,
    *,
    secrets: list[str] | None = None,
) -> str:
    """Ghi snapshot, đọc lại, đối chiếu checksum. Lệch thì không được ghi MENU."""

    assert_official_spreadsheet(spreadsheet_id)
    secret_values = [item for item in (secrets or []) if len(item) >= 8]
    regions = capture_regions(gateway, spreadsheet_id)
    snapshot, digest = build_snapshot(regions, secret_values)
    created = format_timestamp(now_vietnam())
    snapshot_id = created.replace(":", "").replace("-", "") + "-" + digest[:12]
    manifest = {
        "schema": 1,
        "snapshot_id": snapshot_id,
        "created_at": created,
        "spreadsheet_id": spreadsheet_id.strip(),
        "checksum": digest,
        "regions": list(snapshot),
        "storage": BACKUP_TAB,
        "status": "verified",
    }
    rows = _snapshot_rows(snapshot_id, digest, manifest, snapshot)
    existing = _read_ledger(gateway, spreadsheet_id)
    kept = _keep_latest(existing, KEEP_SNAPSHOTS - 1)
    grid = [HEADER, *kept, *rows]
    previous = _row_count(existing)
    while len(grid) < previous:
        grid.append(["", "", "", ""])
    _write_ledger(gateway, spreadsheet_id, grid)
    verified = _verify_ledger(gateway, spreadsheet_id, snapshot_id, digest, snapshot)
    _hide_backup_tab(gateway, spreadsheet_id)
    again, again_digest = build_snapshot(capture_regions(gateway, spreadsheet_id), secret_values)
    del again
    if again_digest != digest:
        raise BackupError(
            "Vùng sắp ghi đổi trong lúc sao lưu. Có nguồn ghi khác. Không ghi MENU."
        )
    return (
        f"Backup bền READY tab={BACKUP_TAB} snapshots={verified} checksum={digest[:12]}."
    )


def _snapshot_rows(
    snapshot_id: str,
    digest: str,
    manifest: dict[str, Any],
    snapshot: dict[str, Any],
) -> list[list[str]]:
    payload = _dump(snapshot)
    chunks = [payload[index : index + CHUNK_CHARS] for index in range(0, len(payload), CHUNK_CHARS)] or [""]
    rows = [
        [snapshot_id, "manifest", digest, _dump(manifest)],
        [snapshot_id, "restore_plan", digest, RESTORE_PLAN],
    ]
    for index, chunk in enumerate(chunks):
        rows.append([snapshot_id, f"payload-{index:03d}", digest, chunk])
    return rows


def _read_ledger(gateway: Any, spreadsheet_id: str) -> list[list[Any]]:
    titles = {str(title) for title in gateway.list_sheet_titles(spreadsheet_id)}
    if BACKUP_TAB not in titles:
        gateway.add_sheet(spreadsheet_id, BACKUP_TAB)
        return []
    values = gateway.get_values(spreadsheet_id, f"'{BACKUP_TAB}'!A1:D")
    if not isinstance(values, list) or not values:
        return []
    header = [str(cell or "").strip() for cell in values[0][:4]]
    if header != HEADER:
        raise BackupError(
            "Tab backup bền không đúng tiêu đề. Không ghi đè và không ghi MENU."
        )
    return values


def _keep_latest(existing: list[list[Any]], limit: int) -> list[list[str]]:
    grouped: list[tuple[str, list[list[str]]]] = []
    current_id = ""
    bucket: list[list[str]] = []
    for raw in existing[1:]:
        if not raw or not str(raw[0] or "").strip():
            continue
        snapshot_id = str(raw[0]).strip()
        row = [str(raw[index] if index < len(raw) else "") for index in range(4)]
        if snapshot_id != current_id:
            if bucket:
                grouped.append((current_id, bucket))
            current_id = snapshot_id
            bucket = [row]
        else:
            bucket.append(row)
    if bucket:
        grouped.append((current_id, bucket))
    kept = grouped[-limit:] if limit > 0 else []
    return [row for _snapshot_id, rows in kept for row in rows]


def _write_ledger(gateway: Any, spreadsheet_id: str, grid: list[list[str]]) -> None:
    gateway.batch_update_values(
        spreadsheet_id,
        [(f"'{BACKUP_TAB}'!A1", grid)],
        value_input_option="RAW",
    )


def _verify_ledger(
    gateway: Any,
    spreadsheet_id: str,
    snapshot_id: str,
    digest: str,
    snapshot: dict[str, Any],
) -> int:
    values = gateway.get_values(spreadsheet_id, f"'{BACKUP_TAB}'!A1:D")
    if not isinstance(values, list) or not values:
        raise BackupError("Không đọc lại được backup bền. Không ghi MENU.")
    header = [str(cell or "").strip() for cell in values[0][:4]]
    if header != HEADER:
        raise BackupError("Backup bền đọc lại sai tiêu đề. Không ghi MENU.")
    payload_parts: list[tuple[str, str]] = []
    manifest = None
    restore = ""
    ids: list[str] = []
    for raw in values[1:]:
        if not raw or not str(raw[0] or "").strip():
            continue
        row_id = str(raw[0]).strip()
        if row_id not in ids:
            ids.append(row_id)
        if row_id != snapshot_id:
            continue
        part = str(raw[1] if len(raw) > 1 else "")
        row_checksum = str(raw[2] if len(raw) > 2 else "")
        body = str(raw[3] if len(raw) > 3 else "")
        if row_checksum != digest:
            raise BackupError("Checksum backup bền không khớp. Không ghi MENU.")
        if part == "manifest":
            try:
                manifest = json.loads(body)
            except json.JSONDecodeError as exc:
                raise BackupError("Manifest backup bền không phải JSON. Không ghi MENU.") from exc
        elif part == "restore_plan":
            restore = body
        elif part.startswith("payload-"):
            payload_parts.append((part, body))
    if not isinstance(manifest, dict) or manifest.get("checksum") != digest:
        raise BackupError("Manifest backup bền không khớp checksum. Không ghi MENU.")
    if manifest.get("spreadsheet_id") != spreadsheet_id.strip():
        raise BackupError("Backup bền không thuộc spreadsheet chính thức. Không ghi MENU.")
    if manifest.get("status") != "verified":
        raise BackupError("Backup bền chưa ở trạng thái verified. Không ghi MENU.")
    if restore != RESTORE_PLAN:
        raise BackupError("Thiếu restore plan của backup bền. Không ghi MENU.")
    if not payload_parts:
        raise BackupError("Backup bền không có snapshot. Không ghi MENU.")
    joined = "".join(body for _part, body in sorted(payload_parts))
    try:
        loaded = json.loads(joined)
    except json.JSONDecodeError as exc:
        raise BackupError("Snapshot backup bền hỏng khi đọc lại. Không ghi MENU.") from exc
    if checksum(loaded) != digest or loaded != snapshot:
        raise BackupError("Đọc lại snapshot không khớp checksum. Không ghi MENU.")
    return len(ids)


def _hide_backup_tab(gateway: Any, spreadsheet_id: str) -> None:
    """Ẩn tab backup nếu được. Thất bại không làm mất snapshot đã kiểm tra."""

    try:
        meta = gateway.list_sheet_meta(spreadsheet_id)
        sheet_id = None
        for item in meta:
            if isinstance(item, dict) and item.get("title") == BACKUP_TAB:
                sheet_id = item.get("sheetId")
        if not isinstance(sheet_id, int):
            return
        gateway.batch_update(
            spreadsheet_id,
            [
                {
                    "updateSheetProperties": {
                        "properties": {"sheetId": sheet_id, "hidden": True},
                        "fields": "hidden",
                    }
                }
            ],
        )
    except Exception:
        return


def _row_count(existing: list[list[Any]]) -> int:
    return len(existing)


def _read_range(gateway: Any, spreadsheet_id: str, range_a1: str, render: str) -> list[Any]:
    try:
        values = gateway.get_values(
            spreadsheet_id,
            range_a1,
            value_render_option=render,
        )
    except TypeError:
        values = gateway.get_values(spreadsheet_id, range_a1)
    if not isinstance(values, list):
        raise BackupError(f"Không đọc được vùng {range_a1}. Không ghi MENU.")
    return values


def _stable_rows(values: list[Any], formulas: list[Any]) -> list[list[Any]]:
    """Lưu công thức thay cho giá trị tự tính, và bỏ ô trống cuối dòng."""

    height = max(len(values), len(formulas))
    rows: list[list[Any]] = []
    for row_index in range(height):
        value_row = values[row_index] if row_index < len(values) and isinstance(values[row_index], list) else []
        formula_row = (
            formulas[row_index]
            if row_index < len(formulas) and isinstance(formulas[row_index], list)
            else []
        )
        width = max(len(value_row), len(formula_row))
        line: list[Any] = []
        for column in range(width):
            formula = formula_row[column] if column < len(formula_row) else None
            value = value_row[column] if column < len(value_row) else None
            if isinstance(formula, str) and formula.startswith("="):
                line.append(formula)
            else:
                line.append(_normalize_cell(value))
        while line and line[-1] == "":
            line.pop()
        rows.append(line)
    while rows and rows[-1] == []:
        rows.pop()
    return rows


def _normalize_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _dump(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
