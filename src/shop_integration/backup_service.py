"""Sao lưu cục bộ trước khi ghi Google. Không phải bản sao đầy đủ của file."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from shop_integration.config import PROJECT_ROOT
from shop_integration.docs_service import (
    document_plain_text,
    managed_region_doc_bounds,
    text_without_managed_region,
)
from shop_integration.errors import BackupError
from shop_integration.logging_utils import redact
from shop_integration.sheet_layout import ALL_TABS
from shop_integration.time_utils import format_timestamp, now_vietnam

BACKUP_LIMITS = [
    "Snapshot lưu giá trị ô và công thức, cùng sheetId, tên tab và chỉ số Docs.",
    "Không lưu định dạng ô, ghi chú, data validation, bộ lọc, biểu đồ hoặc ảnh.",
    "Google Docs chỉ lưu văn bản thuần và kiểu đoạn. Không dựng lại bảng, ảnh, comment hay đề xuất chỉnh sửa.",
    "Khôi phục tự động không ghi đè Trang tính1 hoặc tab ngoài vùng quản lý.",
    "Tab do ứng dụng tạo sau snapshot chỉ bị xóa khi nội dung hiện tại khớp nhật ký đồng bộ.",
    "Ô hoặc đoạn chứa API key hay private key được thay bằng *** trước khi lưu. Khôi phục không dựng lại bí mật.",
    "Đây không phải backup hoàn chỉnh của Google Sheets hoặc Google Docs.",
]
REPORT_MARKER = "BÁO CÁO ĐỒNG BỘ SHOP XOÀI NON"
PROTECTED_TAB = "Trang tính1"


class SpreadsheetBackupSource(Protocol):
    def backup_snapshot(self, spreadsheet_id: str) -> dict[str, Any]: ...


class DocumentBackupSource(Protocol):
    def backup_snapshot(self, document_id: str) -> dict[str, Any]: ...


def checksum(payload: object) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def redact_payload(value: Any, secrets: list[str]) -> Any:
    if isinstance(value, str):
        if "PRIVATE KEY" in value:
            return "***"
        return redact(value, secrets)
    if isinstance(value, list):
        return [redact_payload(item, secrets) for item in value]
    if isinstance(value, dict):
        return {str(key): redact_payload(item, secrets) for key, item in value.items()}
    return value


def assess_live_sync_readiness() -> dict[str, Any]:
    """Backup hiện nằm trên đĩa local. Chưa đủ cho live sync theo lịch trên GitHub Actions."""

    return {
        "status": "FAIL",
        "ready": False,
        "storage": "local-snapshot",
        "github_actions_live_sync": "NOT_READY",
        "complete_fidelity": False,
        "unmanaged_tabs_preserved": True,
        "proposal": (
            "Giữ DRY_RUN=true. Snapshot trong thư mục backups chỉ tồn tại trên máy đang chạy và "
            "không được runner GitHub Actions giữ lại. Cần một kho bền đã có sẵn, do người vận hành cấp, "
            "rồi chạy restore-plan trên kho đó. Không để Actions tự tạo file Google Drive hoặc đổi quyền chia sẻ."
        ),
    }


def live_sync_block_reason() -> str | None:
    """Chặn ghi thật khi job Actions chỉ có snapshot local."""

    if os.environ.get("GITHUB_ACTIONS") != "true":
        return None
    readiness = assess_live_sync_readiness()
    if readiness["ready"]:
        return None
    return (
        "Scheduled live sync chưa đủ điều kiện. "
        + readiness["proposal"]
    )


def restore_guide() -> str:
    return (
        "Hướng dẫn khôi phục:\n"
        "1. Chạy backup-check để xác nhận checksum và trạng thái verified.\n"
        "2. Chạy restore-plan để đối chiếu file Google hiện tại. Lệnh này chỉ đọc.\n"
        "3. Nếu có xung đột, không khôi phục. Giữ dữ liệu hiện tại và backup cũ.\n"
        "4. Chỉ ghi khôi phục khi không còn xung đột chặn và bạn xác nhận bằng restore --confirm.\n"
        "Giới hạn: không phục hồi định dạng, biểu đồ, ảnh, comment. Không ghi đè Trang tính1. "
        "Không tự xóa tab mới nếu nội dung đã khác nhật ký. "
        "Khi Sheets đã ghi mà Docs lỗi, không tự khôi phục Sheets."
    )


class BackupService:
    def __init__(
        self,
        spreadsheet_source: SpreadsheetBackupSource,
        document_source: DocumentBackupSource,
        spreadsheet_id: str,
        document_id: str,
        *,
        directory: Path | None = None,
        secrets: list[str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not spreadsheet_id.strip() or not document_id.strip():
            raise BackupError("Thiếu spreadsheet ID hoặc document ID. Không tạo backup để ghi.")
        self._sheets = spreadsheet_source
        self._docs = document_source
        self._spreadsheet_id = spreadsheet_id.strip()
        self._document_id = document_id.strip()
        self._root = directory or (PROJECT_ROOT / "backups")
        self._secrets = [secret for secret in (secrets or []) if len(secret) >= 8]
        self._clock = clock or now_vietnam
        self.last_directory: Path | None = None

    def create_and_verify(self) -> dict[str, Any]:
        try:
            spreadsheet = redact_payload(
                self._sheets.backup_snapshot(self._spreadsheet_id),
                self._secrets,
            )
            document = redact_payload(
                self._docs.backup_snapshot(self._document_id),
                self._secrets,
            )
        except BackupError:
            raise
        except Exception as exc:
            raise BackupError(
                "Không đọc được Google để sao lưu. Dừng trước khi ghi. "
                f"Chi tiết: {exc}"
            ) from None
        if not isinstance(spreadsheet, dict) or not isinstance(document, dict):
            raise BackupError("Snapshot không hợp lệ. Dừng trước khi ghi.")
        folder = self._new_folder()
        folder.mkdir(parents=True, exist_ok=False)
        self._write_json(folder / "spreadsheet.json", spreadsheet)
        self._write_json(folder / "document.json", document)
        sheet_checksum = checksum(spreadsheet)
        document_checksum = checksum(document)
        names, ids = _sheet_index(spreadsheet)
        manifest = {
            "created_at": format_timestamp(self._clock()),
            "timezone": "Asia/Ho_Chi_Minh",
            "spreadsheet_id": self._spreadsheet_id,
            "document_id": self._document_id,
            "spreadsheet_checksum": sheet_checksum,
            "document_checksum": document_checksum,
            "status": "verified",
            "complete_fidelity": False,
            "sheet_names": names,
            "sheet_ids": ids,
            "limits": list(BACKUP_LIMITS),
        }
        self._write_json(folder / "manifest.json", manifest)
        verified = self.verify(folder)
        self.last_directory = folder
        return verified

    def verify(self, directory: Path) -> dict[str, Any]:
        manifest_path = directory / "manifest.json"
        sheet_path = directory / "spreadsheet.json"
        document_path = directory / "document.json"
        if not manifest_path.is_file() or not sheet_path.is_file() or not document_path.is_file():
            raise BackupError("Backup thiếu manifest hoặc snapshot. Không được ghi.")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            spreadsheet = json.loads(sheet_path.read_text(encoding="utf-8"))
            document = json.loads(document_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise BackupError("Backup không phải JSON hợp lệ. Không được ghi.") from None
        if not isinstance(manifest, dict):
            raise BackupError("Manifest không hợp lệ. Không được ghi.")
        if manifest.get("status") != "verified":
            raise BackupError("Backup chưa hoàn tất. Không được ghi.")
        if manifest.get("complete_fidelity") is not False:
            raise BackupError("Manifest tuyên bố backup hoàn chỉnh. Từ chối vì điều đó không đúng.")
        if checksum(spreadsheet) != manifest.get("spreadsheet_checksum"):
            raise BackupError("Checksum spreadsheet không khớp. Không được ghi.")
        if checksum(document) != manifest.get("document_checksum"):
            raise BackupError("Checksum document không khớp. Không được ghi.")
        blob = sheet_path.read_text(encoding="utf-8") + document_path.read_text(encoding="utf-8")
        self._reject_secrets(blob)
        checked = dict(manifest)
        checked["directory"] = str(directory)
        return checked

    def write_journal(self, payload: dict[str, Any]) -> Path:
        if self.last_directory is None:
            raise BackupError("Chưa có backup vừa được kiểm tra. Không ghi nhật ký khôi phục.")
        self.verify(self.last_directory)
        clean = redact_payload(payload, self._secrets)
        body = {"payload": clean, "checksum": checksum(clean)}
        text = json.dumps(body, ensure_ascii=False, indent=2)
        self._reject_secrets(text)
        path = self.last_directory / "journal.json"
        path.write_text(text, encoding="utf-8")
        return path

    def load(self, directory: Path) -> dict[str, Any]:
        manifest = self.verify(directory)
        spreadsheet = json.loads((directory / "spreadsheet.json").read_text(encoding="utf-8"))
        document = json.loads((directory / "document.json").read_text(encoding="utf-8"))
        journal = None
        journal_path = directory / "journal.json"
        if journal_path.is_file():
            try:
                raw = json.loads(journal_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                raise BackupError("Nhật ký khôi phục không hợp lệ.") from None
            payload = raw.get("payload") if isinstance(raw, dict) else None
            if not isinstance(raw, dict) or checksum(payload) != raw.get("checksum"):
                raise BackupError("Checksum nhật ký khôi phục không khớp.")
            journal = payload
        return {
            "manifest": manifest,
            "spreadsheet": spreadsheet,
            "document": document,
            "journal": journal,
        }

    def _new_folder(self) -> Path:
        stamp = format_timestamp(self._clock()).replace(":", "").replace("-", "")
        folder = self._root / stamp
        suffix = 2
        while folder.exists():
            folder = self._root / f"{stamp}-{suffix}"
            suffix += 1
        return folder

    def _write_json(self, path: Path, payload: object) -> None:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        self._reject_secrets(text)
        path.write_text(text, encoding="utf-8")

    def _reject_secrets(self, text: str) -> None:
        if "BEGIN PRIVATE KEY" in text or "END PRIVATE KEY" in text:
            raise BackupError("Từ chối lưu backup vì còn dấu khóa riêng tư.")
        for secret in self._secrets:
            if secret in text:
                raise BackupError("Từ chối lưu backup vì còn bí mật.")


def plan_restore(
    bundle: dict[str, Any],
    current_spreadsheet: dict[str, Any],
    current_document_text: str,
) -> dict[str, Any]:
    snapshot_sheets = {
        sheet.get("title"): sheet
        for sheet in bundle.get("spreadsheet", {}).get("sheets", [])
        if isinstance(sheet, dict) and sheet.get("title")
    }
    current_sheets = {
        sheet.get("title"): sheet
        for sheet in current_spreadsheet.get("sheets", [])
        if isinstance(sheet, dict) and sheet.get("title")
    }
    journal = bundle.get("journal") if isinstance(bundle.get("journal"), dict) else {}
    journal_tabs = journal.get("tabs", {}) if isinstance(journal.get("tabs"), dict) else {}
    blocking: list[str] = []
    actions: list[str] = []
    needs_confirm = False

    for title, current in current_sheets.items():
        if _is_user_tab(title):
            previous = snapshot_sheets.get(title)
            if previous is not None and _grid(previous) != _grid(current):
                actions.append(f"{title}: dữ liệu người dùng đã đổi so với snapshot. Không ghi tab này.")
            else:
                actions.append(f"{title}: giữ nguyên, không nằm trong vùng khôi phục.")
            continue
        if title not in ALL_TABS:
            actions.append(f"{title}: tab lạ, không ghi.")
            continue
        previous = snapshot_sheets.get(title)
        current_grid = _grid(current)
        if previous is None:
            journal_grid = journal_tabs.get(title)
            if journal_grid is not None and journal_grid == current_grid:
                actions.append(
                    f"{title}: snapshot không có tab và nội dung khớp nhật ký. Có thể xóa tab khi được xác nhận."
                )
                needs_confirm = True
            elif current_grid:
                blocking.append(f"{title}: tab mới không khớp nhật ký. Không xóa.")
            continue
        previous_grid = _grid(previous)
        if current_grid == previous_grid:
            actions.append(f"{title}: không đổi so với snapshot.")
            continue
        journal_grid = journal_tabs.get(title)
        if journal_grid is not None and journal_grid == current_grid:
            actions.append(f"{title}: có thể ghi lại giá trị snapshot khi được xác nhận.")
            needs_confirm = True
            continue
        blocking.append(f"{title}: dữ liệu hiện tại khác snapshot và khác nhật ký. Không ghi đè.")

    snapshot_text = str(bundle.get("document", {}).get("plain_text") or "")
    suffix = split_report_suffix(snapshot_text, current_document_text)
    journal_suffix = str(journal.get("doc_appended_text") or "")
    if current_document_text == snapshot_text:
        actions.append("Docs: không đổi so với snapshot.")
    elif journal_suffix and current_document_text == snapshot_text + journal_suffix:
        actions.append("Docs: có thể gỡ đúng phần báo cáo đã nối thêm khi được xác nhận.")
        needs_confirm = True
    elif suffix is not None and not journal_suffix:
        actions.append("Docs: phần thêm khớp một báo cáo đồng bộ. Có thể gỡ khi được xác nhận.")
        needs_confirm = True
    elif text_without_managed_region(current_document_text) == snapshot_text:
        actions.append("Docs: có thể gỡ đúng vùng báo cáo đã quản lý khi được xác nhận.")
        needs_confirm = True
    else:
        blocking.append("Docs: văn bản hiện tại không khớp snapshot. Không xóa nội dung.")

    return {
        "blocking": blocking,
        "actions": actions,
        "needs_confirm": needs_confirm,
        "limits": list(BACKUP_LIMITS),
        "complete_fidelity": False,
    }


def apply_restore(
    bundle: dict[str, Any],
    current_spreadsheet: dict[str, Any],
    current_document: dict[str, Any],
    *,
    confirm: bool,
    sheets_gateway: Any,
    docs_gateway: Any,
    spreadsheet_id: str,
    document_id: str,
) -> dict[str, Any]:
    current_text = document_plain_text(current_document)
    plan = plan_restore(bundle, current_spreadsheet, current_text)
    if plan["blocking"]:
        raise BackupError("Phát hiện xung đột. Không ghi khôi phục. " + " ".join(plan["blocking"]))
    if plan["needs_confirm"] and not confirm:
        raise BackupError("Chưa có xác nhận. Không ghi khôi phục.")
    if not plan["needs_confirm"]:
        return plan

    snapshot_sheets = {
        sheet.get("title"): sheet
        for sheet in bundle.get("spreadsheet", {}).get("sheets", [])
        if isinstance(sheet, dict) and sheet.get("title")
    }
    current_sheets = {
        sheet.get("title"): sheet
        for sheet in current_spreadsheet.get("sheets", [])
        if isinstance(sheet, dict) and sheet.get("title")
    }
    journal = bundle.get("journal") if isinstance(bundle.get("journal"), dict) else {}
    journal_tabs = journal.get("tabs", {}) if isinstance(journal.get("tabs"), dict) else {}
    for title, current in current_sheets.items():
        if _is_user_tab(title) or title not in ALL_TABS:
            continue
        previous = snapshot_sheets.get(title)
        current_grid = _grid(current)
        if previous is None:
            journal_grid = journal_tabs.get(title)
            sheet_id = current.get("sheet_id")
            if journal_grid == current_grid and isinstance(sheet_id, int) and title != PROTECTED_TAB:
                sheets_gateway.delete_sheet(spreadsheet_id, sheet_id)
            continue
        if current_grid == _grid(previous):
            continue
        journal_grid = journal_tabs.get(title)
        if journal_grid == current_grid:
            grid = previous.get("formulas") or previous.get("values") or []
            sheets_gateway.update_values(
                spreadsheet_id,
                f"'{title}'!A1",
                grid,
                value_input_option="USER_ENTERED",
            )

    snapshot_text = str(bundle.get("document", {}).get("plain_text") or "")
    journal_suffix = str(journal.get("doc_appended_text") or "")
    if current_text != snapshot_text and (
        (journal_suffix and current_text == snapshot_text + journal_suffix)
        or (not journal_suffix and split_report_suffix(snapshot_text, current_text) is not None)
    ):
        bounds = _suffix_bounds(current_document, snapshot_text)
        if bounds is None:
            raise BackupError("Không đủ chỉ số cấu trúc Docs để gỡ báo cáo an toàn. Không ghi.")
        start, end = bounds
        docs_gateway.batch_update(
            document_id,
            [{"deleteContentRange": {"range": {"startIndex": start, "endIndex": end}}}],
        )
    elif current_text != snapshot_text and text_without_managed_region(current_text) == snapshot_text:
        bounds = managed_region_doc_bounds(current_document)
        if bounds is None:
            raise BackupError("Không đủ chỉ số cấu trúc Docs để gỡ vùng báo cáo an toàn. Không ghi.")
        start, end = bounds
        docs_gateway.batch_update(
            document_id,
            [{"deleteContentRange": {"range": {"startIndex": start, "endIndex": end}}}],
        )
    return plan


def split_report_suffix(snapshot_text: str, current_text: str) -> str | None:
    if current_text == snapshot_text:
        return ""
    if snapshot_text and not current_text.startswith(snapshot_text):
        return None
    suffix = current_text[len(snapshot_text) :] if snapshot_text else current_text
    body = suffix.strip()
    if body.count(REPORT_MARKER) != 1 or not body.startswith(REPORT_MARKER):
        return None
    if "BEGIN PRIVATE KEY" in body:
        return None
    return suffix


def latest_backup(root: Path | None = None) -> Path:
    folder = root or (PROJECT_ROOT / "backups")
    if not folder.is_dir():
        raise BackupError("Chưa có thư mục backups.")
    candidates = [path for path in folder.iterdir() if path.is_dir() and (path / "manifest.json").is_file()]
    if not candidates:
        raise BackupError("Chưa có backup nào đã ghi manifest.")
    return max(candidates, key=lambda path: path.name)


def _sheet_index(snapshot: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    names: list[str] = []
    ids: dict[str, Any] = {}
    for sheet in snapshot.get("sheets", []):
        if not isinstance(sheet, dict):
            continue
        title = str(sheet.get("title") or "")
        if not title:
            continue
        names.append(title)
        ids[title] = sheet.get("sheet_id")
    return names, ids


def _is_user_tab(title: str) -> bool:
    return title == PROTECTED_TAB or title not in ALL_TABS


def _grid(sheet: dict[str, Any]) -> list[Any]:
    formulas = sheet.get("formulas")
    if isinstance(formulas, list) and formulas:
        return formulas
    values = sheet.get("values")
    return values if isinstance(values, list) else []


def _suffix_bounds(document: dict[str, Any], snapshot_text: str) -> tuple[int, int] | None:
    mapping: list[int] = []
    pieces: list[str] = []
    for start, _end, text in _indexed_runs(document):
        if not isinstance(start, int):
            return None
        pieces.append(text)
        mapping.extend(start + offset for offset in range(len(text)))
    full = "".join(pieces)
    suffix = split_report_suffix(snapshot_text, full)
    if not suffix:
        return None
    start_char = len(snapshot_text)
    if start_char >= len(mapping):
        return None
    return mapping[start_char], mapping[-1] + 1


def _indexed_runs(document: dict[str, Any]):
    from shop_integration.docs_service import _iter_text_runs

    return _iter_text_runs(document)
