from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

import pytest

from shop_integration.config import (
    DEFAULT_GOOGLE_CREDENTIALS_FILE,
    PROJECT_ROOT,
    Settings,
    resolve_credentials_path,
)
from shop_integration.errors import ConfigError
from shop_integration.google_auth import WORKSPACE_SCOPES, load_service_account_credentials

_SECRET_BODY = '{"type": "service_account", "private_key": "BEGIN PRIVATE KEY material-hidden"}'
_ENV_NAMES = (
    "GOOGLE_CREDENTIALS_FILE",
    "SHOP_XOAI_NON_BASE_URL",
    "SHOP_XOAI_NON_API_KEY",
    "GOOGLE_SPREADSHEET_ID",
    "GOOGLE_DOCUMENT_ID",
)


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_project_root_is_derived_from_config_file() -> None:
    config_file = Path(PROJECT_ROOT / "src" / "shop_integration" / "config.py").resolve()
    assert config_file.is_file()
    assert PROJECT_ROOT == config_file.parents[2]
    assert PROJECT_ROOT == Path(__import__("shop_integration.config", fromlist=["PROJECT_ROOT"]).__file__).resolve().parents[2]


def test_relative_path_from_project_root_and_from_another_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    other = tmp_path / "elsewhere"
    project.mkdir()
    other.mkdir()
    target = project / "secrets" / "sa.json"
    target.parent.mkdir()
    target.write_text('{"type": "authorized_user"}', encoding="utf-8")
    (other / "secrets" / "sa.json").parent.mkdir(parents=True)
    (other / "secrets" / "sa.json").write_text(_SECRET_BODY, encoding="utf-8")
    monkeypatch.setattr("shop_integration.config.PROJECT_ROOT", project)

    for folder in (project, other):
        monkeypatch.chdir(folder)
        resolved = resolve_credentials_path("secrets/sa.json")
        assert resolved == target.resolve()
        with pytest.raises(ConfigError) as caught:
            load_service_account_credentials("secrets/sa.json", WORKSPACE_SCOPES)
        message = str(caught.value)
        assert "Không tìm thấy" not in message
        assert "service account" in message
        assert "material-hidden" not in message
        assert "BEGIN PRIVATE KEY" not in message


def test_absolute_path_ignores_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    other = tmp_path / "elsewhere"
    project.mkdir()
    other.mkdir()
    target = tmp_path / "absolute-sa.json"
    target.write_text('{"type": "authorized_user"}', encoding="utf-8")
    monkeypatch.setattr("shop_integration.config.PROJECT_ROOT", project)
    (other / "absolute-sa.json").write_text(_SECRET_BODY, encoding="utf-8")

    for folder in (project, other):
        monkeypatch.chdir(folder)
        assert resolve_credentials_path(str(target)) == target.resolve()
        with pytest.raises(ConfigError) as caught:
            load_service_account_credentials(str(target), WORKSPACE_SCOPES)
        message = str(caught.value)
        assert "Không tìm thấy" not in message
        assert "material-hidden" not in message


def test_missing_credentials_file_reports_vietnamese_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    project = tmp_path / "project"
    other = tmp_path / "elsewhere"
    project.mkdir()
    other.mkdir()
    (other / "GOOGLE_CREDENTIALS_FILE.json").write_text(_SECRET_BODY, encoding="utf-8")
    monkeypatch.setattr("shop_integration.config.PROJECT_ROOT", project)
    monkeypatch.chdir(other)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(ConfigError) as caught:
        load_service_account_credentials("./GOOGLE_CREDENTIALS_FILE.json", WORKSPACE_SCOPES)

    message = str(caught.value)
    expected = (project / "GOOGLE_CREDENTIALS_FILE.json").resolve()
    assert "Không tìm thấy tệp thông tin xác thực Google" in message
    assert str(expected) in message
    assert "material-hidden" not in message
    assert "BEGIN PRIVATE KEY" not in message
    assert "material-hidden" not in caplog.text
    assert "BEGIN PRIVATE KEY" not in caplog.text
    assert not (project / "GOOGLE_CREDENTIALS_FILE.json").exists()


def test_dotenv_is_read_from_project_root_not_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    other = tmp_path / "elsewhere"
    project.mkdir()
    other.mkdir()
    (project / ".env").write_text(
        "GOOGLE_CREDENTIALS_FILE=./nested/sa.json\n"
        "SHOP_XOAI_NON_BASE_URL=https://example.test/api/v1\n",
        encoding="utf-8",
    )
    (other / ".env").write_text(
        "GOOGLE_CREDENTIALS_FILE=./ignored.json\n",
        encoding="utf-8",
    )
    _clear_env(monkeypatch)
    monkeypatch.setattr("shop_integration.config.PROJECT_ROOT", project)
    monkeypatch.chdir(other)

    settings = Settings()

    assert settings.google_credentials_file == "./nested/sa.json"
    assert settings.resolved_credentials_path == (project / "nested" / "sa.json").resolve()


def test_blank_credentials_setting_uses_default_relative_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    other = tmp_path / "elsewhere"
    project.mkdir()
    other.mkdir()
    (project / ".env").write_text("GOOGLE_CREDENTIALS_FILE=\n", encoding="utf-8")
    _clear_env(monkeypatch)
    monkeypatch.setattr("shop_integration.config.PROJECT_ROOT", project)
    monkeypatch.chdir(other)

    settings = Settings()

    assert settings.google_credentials_file == DEFAULT_GOOGLE_CREDENTIALS_FILE
    assert settings.resolved_credentials_path == (project / "GOOGLE_CREDENTIALS_FILE.json").resolve()


def test_gitignore_ignores_service_account_json_and_keeps_data_json(tmp_path: Path) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("Git không có trên PATH")
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copyfile(PROJECT_ROOT / ".gitignore", repo / ".gitignore")
    subprocess.run([git, "init"], cwd=repo, check=True, capture_output=True)
    expected = {
        "GOOGLE_CREDENTIALS_FILE.json": True,
        "nested/GOOGLE_CREDENTIALS_FILE.json": True,
        "prod-service-account.json": True,
        "my_service_account.json": True,
        "credentials-live.json": True,
        "tests/fixtures/products_empty.json": False,
        "data/catalog.json": False,
        "backups/snapshot/manifest.json": True,
    }
    for relative, ignored in expected.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
        result = subprocess.run(
            [git, "check-ignore", "--", relative],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        assert result.returncode == (0 if ignored else 1), relative
