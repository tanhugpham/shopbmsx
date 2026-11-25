from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from shop_integration.backup_service import BackupService, apply_restore, plan_restore
from shop_integration.config import PROJECT_ROOT
from shop_integration.docs_service import DocsService
from shop_integration.errors import BackupError, ConfigError, GoogleWorkspaceError, PartialSyncError
from shop_integration.google_auth import call_google, translate_google_error
from shop_integration.product_service import ProductService
from shop_integration.sheet_layout import ALL_TABS
from shop_integration.sheets_service import GoogleSheetsGateway, SheetsService
from shop_integration.sync_service import SyncService
from shop_integration.time_utils import VIETNAM_TZ
from tests.conftest import client_for, make_settings
from tests.test_docs_service import MemoryDocs
from tests.test_sheets_service import MemorySheets, _tab
from tests.test_sync_service import AcceptingBackup


SECRET = "sxn_live_test_key_value"
PRIVATE = "-----BEGIN PRIVATE KEY-----\nmaterial-sa-secret\n-----END PRIVATE KEY-----\n"


def _column_values(grid: list, header: str) -> list[str]:
    index = grid[0].index(header)
    found: list[str] = []
    for row in grid[1:]:
        if len(row) <= index:
            continue
        text = str(row[index]).strip().removeprefix("\u200b")
        if text:
            found.append(text)
    return found


def _clock() -> datetime:
    return datetime(2026, 11, 23, 10, 30, tzinfo=VIETNAM_TZ)


def _product(index: int, price: int) -> dict:
    return {
        "id": f"local_{index}",
        "title": f"Sản phẩm {index}",
        "in_stock": index % 2 == 0,
        "variants": [
            {
                "id": f"var_{index}",
                "prices": {"vnd": price},
                "in_stock": True,
                "available_quantity": 1,
            }
        ],
    }


def _catalog(first_price: int) -> dict:
    return {"data": [_product(index, first_price if index == 1 else 1000 + index) for index in range(1, 25)]}


class BatchSheets(MemorySheets):
    def __init__(self) -> None:
        super().__init__(
            titles=["Trang tính1"],
            values={"Trang tính1": [["Giữ nguyên"], ["số liệu người dùng"]]},
        )
        self.batches: list[list] = []

    def batch_update_values(self, spreadsheet_id: str, items: list, **kwargs) -> None:
        del kwargs
        self.batches.append(items)
        for range_a1, values in items:
            self.update_values(spreadsheet_id, range_a1, values)


class FailVariantWrite(MemorySheets):
    def update_values(self, spreadsheet_id: str, range_a1: str, values: list, **kwargs) -> None:
        del kwargs
        if _tab(range_a1) == "BIEN_THE":
            raise GoogleWorkspaceError("Google tạm thời không phản hồi (HTTP 500).")
        super().update_values(spreadsheet_id, range_a1, values)


class ShortRead(MemorySheets):
    def get_values(self, spreadsheet_id: str, range_a1: str, **kwargs) -> list:
        del kwargs
        rows = super().get_values(spreadsheet_id, range_a1)
        if "SAN_PHAM" in range_a1 and rows:
            return rows[:1]
        return rows


class Recorder:
    def __init__(self) -> None:
        self.writes: list[tuple] = []
        self.deleted: list[int] = []
        self.docs: list[list] = []

    def update_values(self, spreadsheet_id: str, range_a1: str, values: list, **kwargs) -> None:
        self.writes.append((range_a1, values, kwargs))

    def delete_sheet(self, spreadsheet_id: str, sheet_id: int) -> None:
        self.deleted.append(sheet_id)

    def batch_update(self, document_id: str, requests: list) -> None:
        self.docs.append(requests)


def _run(settings, handler, sheets, docs, backup, page_size: int = 200):
    with client_for(settings, handler) as api:
        return SyncService(
            settings,
            api,
            ProductService(),
            sheets_service=SheetsService(sheets, "sheet-id", dry_run=False, secret=SECRET),
            docs_service=DocsService(docs, "doc-id", dry_run=False, secret=SECRET),
            clock=_clock,
            page_size=page_size,
            backup_service=backup,
        ).run(write_sheets=True, write_doc=True)


def test_live_shape_sync_is_idempotent_and_detects_price_change() -> None:
    state = {"price": 1000}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(200, json=_catalog(state["price"]))

    settings = make_settings(dry_run=False)
    sheets = BatchSheets()
    docs = MemoryDocs()
    backup = AcceptingBackup()
    first = _run(settings, handler, sheets, docs, backup)
    assert first.summary.product_count == 24
    assert first.summary.variant_count == 24
    assert first.sheets_written is True
    assert first.doc_written is True
    # Phase 4.3B: catalog, nguồn, NASTELEID và báo giá cùng một spreadsheet.
    assert len(sheets.batches) == 4
    assert [item[0] for item in sheets.batches[0]] == [
        "'SAN_PHAM'!A1",
        "'BIEN_THE'!A1",
        "'BANG_GIA_BAN'!A1",
        "'CAU_HINH'!A1",
    ]
    assert [item[0] for item in sheets.batches[1]] == ["'NASTELEID'!A1"]
    assert [item[0] for item in sheets.batches[2]] == ["'BAO_GIA'!A1"]
    assert [item[0] for item in sheets.batches[3]] == ["'BAO_GIA_TONG_HOP'!A1"]
    assert sheets.values["Trang tính1"] == [["Giữ nguyên"], ["số liệu người dùng"]]
    assert "Trang tính1" not in sheets.writes

    second = _run(settings, handler, sheets, docs, backup)
    product_ids = _column_values(sheets.values["SAN_PHAM"], "Product ID")
    variant_ids = list(
        zip(
            _column_values(sheets.values["BIEN_THE"], "Product ID"),
            _column_values(sheets.values["BIEN_THE"], "Variant ID"),
            strict=True,
        )
    )
    assert second.sheets_written is True
    assert second.doc_written is False
    assert second.added_count == 0
    assert second.unchanged_count == 24
    assert "không nối thêm" in second.doc_note
    assert len(product_ids) == 24
    assert len(set(product_ids)) == 24
    assert len(variant_ids) == 24
    assert len(set(variant_ids)) == 24
    assert len(docs.requests) == 2
    assert len(sheets.values["NHAT_KY_DONG_BO"]) == 3

    state["price"] = 1500
    docs.plain = docs.plain.replace(
        "PHAN_SUA_TAY_BEGIN\n\nPHAN_SUA_TAY_END",
        "PHAN_SUA_TAY_BEGIN\nGIU_NOI_DUNG_TAY\nPHAN_SUA_TAY_END",
    )
    third = _run(settings, handler, sheets, docs, backup)
    assert len(third.price_changes) == 1
    assert third.price_changes[0].old_price == 1000
    assert third.price_changes[0].new_price == 1500
    assert third.doc_written is True
    assert docs.plain.count("SHOP_XOAI_NON_BAO_CAO_BEGIN") == 1
    assert docs.plain.count("SHOP_XOAI_NON_BAO_CAO_END") == 1
    assert "BẢNG BÁO GIÁ" in docs.plain
    assert "GIU_NOI_DUNG_TAY" in docs.plain
    assert docs.plain.count("PHAN_SUA_TAY_BEGIN") == 1
    assert docs.plain.count("PHAN_SUA_TAY_END") == 1
    assert "Sản phẩm 1" not in docs.plain
    assert "Shop Xoài Non" not in docs.plain
    assert "Mẫu báo giá: 4" in docs.plain
    assert "local_" not in docs.plain
    assert "giá gốc" not in docs.plain.lower()
    assert "lợi nhuận" not in docs.plain.lower()
    assert len(_column_values(sheets.values["SAN_PHAM"], "Product ID")) == 24
    assert "Giá gốc" not in sheets.values["BANG_GIA_BAN"][0]
    assert "Lợi nhuận" not in "".join(str(cell) for cell in sheets.values["BANG_GIA_BAN"][0])
    assert SECRET not in third.text
    assert "BEGIN PRIVATE KEY" not in third.text


def test_backup_failure_and_invalid_snapshot_block_writes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_catalog(1000))

    settings = make_settings(dry_run=False)
    sheets = MemorySheets(titles=["Trang tính1"], values={"Trang tính1": [["giữ"]]})
    docs = MemoryDocs()

    class BrokenBackup(AcceptingBackup):
        def create_and_verify(self) -> dict:
            raise BackupError("không đọc được Google")

    with pytest.raises(BackupError):
        _run(settings, handler, sheets, docs, BrokenBackup())
    assert sheets.writes == []
    assert docs.requests == []

    class InvalidBackup(AcceptingBackup):
        def create_and_verify(self) -> dict:
            return {"status": "writing", "complete_fidelity": False}

    with pytest.raises(BackupError, match="chưa hoàn tất"):
        _run(settings, handler, sheets, docs, InvalidBackup())
    assert sheets.writes == []


def test_missing_backup_service_blocks_write() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_catalog(1000))

    sheets = MemorySheets()
    with pytest.raises(ConfigError, match="sao lưu"):
        _run(make_settings(dry_run=False), handler, sheets, MemoryDocs(), None)
    assert sheets.writes == []


def test_incomplete_or_empty_source_does_not_mark_existing_products_missing() -> None:
    settings = make_settings(dry_run=False)
    sheets = MemorySheets(
        titles=["SAN_PHAM", "BIEN_THE", "LICH_SU_GIA", "NHAT_KY_DONG_BO", "CAU_HINH"],
        values={"SAN_PHAM": [["Product ID", "Trạng thái đồng bộ"], ["local_old", "Thành công"]]},
    )

    def repeated(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [_product(1, 1000)]})

    with pytest.raises(ConfigError, match="không đầy đủ"):
        _run(settings, repeated, sheets, MemoryDocs(), AcceptingBackup(), page_size=1)
    assert sheets.writes == []
    assert sheets.values["SAN_PHAM"][1] == ["local_old", "Thành công"]

    def empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    with pytest.raises(ConfigError, match="đang có dữ liệu"):
        _run(settings, empty, sheets, MemoryDocs(), AcceptingBackup())
    assert "Không có trong API" not in str(sheets.values)


def test_partial_sheet_write_does_not_touch_docs_or_user_tab() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_catalog(1000))

    sheets = FailVariantWrite(titles=["Trang tính1"], values={"Trang tính1": [["giữ"]]})
    docs = MemoryDocs()
    with pytest.raises(PartialSyncError, match="chưa trọn vẹn"):
        _run(make_settings(dry_run=False), handler, sheets, docs, AcceptingBackup())
    assert docs.requests == []
    assert "SAN_PHAM" in sheets.writes
    assert "BIEN_THE" not in sheets.writes
    assert sheets.values["Trang tính1"] == [["giữ"]]


def test_verified_sheet_with_failed_docs_can_retry_without_duplicates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_catalog(1000))

    settings = make_settings(dry_run=False)
    sheets = BatchSheets()
    docs = MemoryDocs()
    docs.fail_next = True
    backup = AcceptingBackup()
    with pytest.raises(PartialSyncError, match="Không xem là thành công toàn bộ"):
        _run(settings, handler, sheets, docs, backup)
    assert docs.requests == []
    first_ids = _column_values(sheets.values["SAN_PHAM"], "Product ID")
    assert len(first_ids) == 24

    report = _run(settings, handler, sheets, docs, backup)
    second_ids = _column_values(sheets.values["SAN_PHAM"], "Product ID")
    assert report.doc_written is True
    assert second_ids == first_ids
    assert len(set(second_ids)) == 24


def test_reread_mismatch_stops_before_docs() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [_product(1, 1000)]})

    docs = MemoryDocs()
    with pytest.raises(PartialSyncError, match="Đọc lại"):
        _run(make_settings(dry_run=False), handler, ShortRead(), docs, AcceptingBackup())
    assert docs.requests == []


def test_protected_tab_cannot_be_written() -> None:
    service = SheetsService(MemorySheets(), "sheet-id", dry_run=False)
    with pytest.raises(ConfigError, match="Trang tính1"):
        service._write_grid("Trang tính1", [["x"]])


def test_backup_roundtrip_hides_secrets_and_keeps_old_copies(tmp_path: Path) -> None:
    secret_sheet = {
        "title": "Báo Giá",
        "sheets": [
            {
                "sheet_id": 0,
                "title": "Trang tính1",
                "index": 0,
                "formulas": [["=A1"]],
                "values": [["nhãn", SECRET, PRIVATE]],
                "grid_properties": {"rowCount": 1},
            }
        ],
    }
    document = {
        "document_id": "doc-id",
        "title": "Bảng giá",
        "plain_text": f"giá\n{SECRET}\n{PRIVATE}",
        "end_index": 2,
        "blocks": [{"type": "paragraph", "named_style": "NORMAL_TEXT", "text": "giá"}],
    }

    class Source:
        def __init__(self, payload) -> None:
            self.payload = payload

        def backup_snapshot(self, resource_id: str) -> dict:
            del resource_id
            return self.payload

    service = BackupService(
        Source(secret_sheet),
        Source(document),
        "sheet-id",
        "doc-id",
        directory=tmp_path,
        secrets=[SECRET],
        clock=_clock,
    )
    first = service.create_and_verify()
    second = service.create_and_verify()
    folders = [path for path in tmp_path.iterdir() if path.is_dir()]
    assert len(folders) == 2
    assert first["status"] == "verified"
    assert first["complete_fidelity"] is False
    assert second["directory"] != first["directory"]
    blob = Path(first["directory"]).read_text(encoding="utf-8") if False else "\n".join(
        path.read_text(encoding="utf-8") for path in Path(first["directory"]).iterdir()
    )
    assert SECRET not in blob
    assert "BEGIN PRIVATE KEY" not in blob
    assert "material-sa-secret" not in blob
    assert "Trang tính1" in blob
    sheet = json.loads((Path(first["directory"]) / "spreadsheet.json").read_text(encoding="utf-8"))
    assert sheet["sheets"][0]["sheet_id"] == 0
    assert sheet["sheets"][0]["formulas"] == [["=A1"]]

    tampered = Path(first["directory"]) / "spreadsheet.json"
    payload = json.loads(tampered.read_text(encoding="utf-8"))
    payload["title"] = "đã sửa"
    tampered.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BackupError, match="Checksum"):
        service.verify(Path(first["directory"]))


def test_backup_source_error_does_not_leave_verified_manifest(tmp_path: Path) -> None:
    class Source:
        def backup_snapshot(self, resource_id: str) -> dict:
            del resource_id
            raise TimeoutError("timed out")

    service = BackupService(
        Source(),
        Source(),
        "sheet-id",
        "doc-id",
        directory=tmp_path,
        secrets=[SECRET],
        clock=_clock,
    )
    with pytest.raises(BackupError, match="Không đọc được Google"):
        service.create_and_verify()
    assert list(tmp_path.iterdir()) == []


def test_restore_conflict_blocks_writes_and_confirm_restores_only_managed_data() -> None:
    bundle = {
        "spreadsheet": {
            "sheets": [
                {"title": "Trang tính1", "sheet_id": 1, "values": [["cũ"]]},
                {"title": "SAN_PHAM", "sheet_id": 2, "formulas": [["Product ID"], ["a"]], "values": [["Product ID"], ["a"]]},
            ]
        },
        "document": {"plain_text": "nội dung gốc"},
        "journal": {"tabs": {"SAN_PHAM": [["Product ID"], ["b"]]}, "doc_appended_text": ""},
    }
    current = {
        "sheets": [
            {"title": "Trang tính1", "sheet_id": 1, "values": [["người dùng sửa"]]},
            {"title": "SAN_PHAM", "sheet_id": 2, "values": [["Product ID"], ["lệch"]]},
        ]
    }
    plan = plan_restore(bundle, current, "nội dung đã bị sửa ngoài báo cáo")
    assert plan["blocking"]
    assert plan["complete_fidelity"] is False
    recorder = Recorder()
    with pytest.raises(BackupError, match="xung đột"):
        apply_restore(
            bundle,
            current,
            {"body": {"content": [{"endIndex": 2}]}},
            confirm=True,
            sheets_gateway=recorder,
            docs_gateway=recorder,
            spreadsheet_id="sheet-id",
            document_id="doc-id",
        )
    assert recorder.writes == []
    assert recorder.deleted == []
    assert recorder.docs == []

    matched = {
        "sheets": [
            {"title": "Trang tính1", "sheet_id": 1, "values": [["người dùng sửa"]]},
            {"title": "SAN_PHAM", "sheet_id": 2, "values": [["Product ID"], ["b"]]},
        ]
    }
    report = "BÁO CÁO ĐỒNG BỘ SHOP XOÀI NON\nSố sản phẩm: 24\n"
    document = {
        "body": {
            "content": [
                {
                    "startIndex": 1,
                    "endIndex": 1 + len(report),
                    "paragraph": {
                        "elements": [
                            {"startIndex": 1, "endIndex": 1 + len(report), "textRun": {"content": report}}
                        ]
                    },
                }
            ]
        }
    }
    doc_bundle = {
        "spreadsheet": {"sheets": [{"title": "Trang tính1", "sheet_id": 1, "values": [["cũ"]]}]},
        "document": {"plain_text": ""},
        "journal": {
            "tabs": {"SAN_PHAM": [["Product ID"], ["mới"]]},
            "doc_appended_text": report,
        },
    }
    current_new_tab = {
        "sheets": [
            {"title": "Trang tính1", "sheet_id": 1, "values": [["cũ"]]},
            {"title": "SAN_PHAM", "sheet_id": 9, "values": [["Product ID"], ["mới"]]},
        ]
    }
    with pytest.raises(BackupError, match="xác nhận"):
        apply_restore(
            doc_bundle,
            current_new_tab,
            document,
            confirm=False,
            sheets_gateway=recorder,
            docs_gateway=recorder,
            spreadsheet_id="sheet-id",
            document_id="doc-id",
        )
    assert recorder.deleted == []

    apply_restore(
        doc_bundle,
        current_new_tab,
        document,
        confirm=True,
        sheets_gateway=recorder,
        docs_gateway=recorder,
        spreadsheet_id="sheet-id",
        document_id="doc-id",
    )
    assert recorder.deleted == [9]
    assert all("Trang tính1" not in str(item) for item in recorder.writes)
    assert recorder.docs


def test_google_status_and_retry_policy(mocker) -> None:
    mocker.patch("shop_integration.google_auth.time.sleep")

    class Http(Exception):
        def __init__(self, status_code: int) -> None:
            super().__init__(f"HTTP {status_code}")
            self.status_code = status_code

    assert "429" in str(translate_google_error(Http(429)))
    assert "503" in str(translate_google_error(Http(503)))
    assert "Hết thời gian" in str(translate_google_error(TimeoutError("timed out")))
    assert "quyền sửa" in str(translate_google_error(Http(403)))

    calls = {"n": 0}

    def read():
        calls["n"] += 1
        if calls["n"] < 3:
            raise Http(429)
        return "ok"

    assert call_google(read, retry=True, sleep=lambda _seconds: None) == "ok"
    assert calls["n"] == 3

    calls["n"] = 0

    def forbidden():
        calls["n"] += 1
        raise Http(403)

    with pytest.raises(GoogleWorkspaceError):
        call_google(forbidden, retry=True, sleep=lambda _seconds: None)
    assert calls["n"] == 1

    calls["n"] = 0

    def write():
        calls["n"] += 1
        raise TimeoutError("timed out")

    with pytest.raises(GoogleWorkspaceError, match="Hết thời gian"):
        call_google(write, retry=False, sleep=lambda _seconds: None)
    assert calls["n"] == 1

    built = mocker.Mock()
    mocker.patch("googleapiclient.discovery.build", return_value=built)
    gateway = GoogleSheetsGateway(object())
    values = built.spreadsheets.return_value.values.return_value
    values.get.return_value.execute.side_effect = [Http(503), {"values": [["A"]]}]
    assert gateway.get_values("abc", "A1") == [["A"]]
    assert values.get.return_value.execute.call_count == 2
    values.update.return_value.execute.side_effect = Http(500)
    with pytest.raises(GoogleWorkspaceError, match="500"):
        gateway.update_values("abc", "A1", [["A"]])
    assert values.update.return_value.execute.call_count == 1


def test_parser_exposes_backup_commands() -> None:
    from shop_integration.cli import build_parser

    parser = build_parser()
    for command in ("backup", "backup-check", "backup-readiness", "restore-plan", "restore"):
        assert parser.parse_args([command]).command == command


def test_gitignore_keeps_backups_off_github(tmp_path: Path) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("Git không có trên PATH")
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copyfile(PROJECT_ROOT / ".gitignore", repo / ".gitignore")
    subprocess.run([git, "init"], cwd=repo, check=True, capture_output=True)
    path = repo / "backups" / "snapshot" / "manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}", encoding="utf-8")
    result = subprocess.run(
        [git, "check-ignore", "--", "backups/snapshot/manifest.json"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    fixture = repo / "tests" / "fixtures" / "products_empty.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text("{}", encoding="utf-8")
    kept = subprocess.run(
        [git, "check-ignore", "--", "tests/fixtures/products_empty.json"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert kept.returncode == 1


def test_all_tabs_do_not_include_user_sheet() -> None:
    assert "Trang tính1" not in ALL_TABS
