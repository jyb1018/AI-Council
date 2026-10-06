import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_council import config
from ai_council.cli import main
from ai_council.store import Store


def test_demo_and_status_export(tmp_path, capsys):
    db = tmp_path / "demo.db"
    assert main(["demo", "Check architecture", "--database", str(db)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "completed" and result["simulated"]
    sid = result["session_id"]
    config = tmp_path / "council.toml"
    config.write_text(f'database="{db.as_posix()}"\n')
    prefix = ["--config", str(config)]
    assert main(prefix + ["status", sid]) == 0
    assert json.loads(capsys.readouterr().out)["attempts"] == 10
    output = tmp_path / "report.md"
    assert main(prefix + ["export", sid, "--output", str(output), "--format", "markdown"]) == 0
    capsys.readouterr()
    assert "[SIMULATED]" in output.read_text()
    assert main(prefix + ["export", sid, "--output", str(output)]) == 2
    assert "error" in json.loads(capsys.readouterr().out)


def test_module_entrypoint(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    result = subprocess.run([sys.executable, "-m", "ai_council", "demo", "--database", str(tmp_path / "module.db")], env=env, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["attempts"] == 10


def test_bad_config(capsys):
    assert main(["--config", "/missing/settings.toml", "doctor"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "config"


def test_macos_demo_without_database_uses_writable_user_directory(tmp_path, monkeypatch, capsys):
    legacy_share = tmp_path / ".local" / "share"
    legacy_share.mkdir(parents=True)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(config, "DEFAULT_DB", legacy_share / "ai-council" / "council.sqlite3")
    legacy_share.chmod(0o555)
    try:
        assert main(["demo", "Personal State OS에 event sourcing이 필요한가요?"]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["status"] == "completed" and result["simulated"]
        assert result["attempts"] == 10
        assert (tmp_path / "Library" / "Application Support" / "ai-council" / "demo.sqlite3").exists()
        assert not (legacy_share / "ai-council").exists()
    finally:
        legacy_share.chmod(0o755)


def test_database_permission_error_is_specific(tmp_path, capsys):
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    blocked.chmod(0o555)
    try:
        assert main(["demo", "Check architecture", "--database", str(blocked / "demo.sqlite3")]) == 1
        error = json.loads(capsys.readouterr().out)["error"]
        assert error["code"] == "database_permissions"
        assert "writable" in error["message"]
        assert not (blocked / "demo.sqlite3").exists()
    finally:
        blocked.chmod(0o755)


@pytest.mark.parametrize("platform", ["darwin", "linux"])
@pytest.mark.parametrize("filename", ["council.sqlite3", "demo.sqlite3"])
def test_default_database_location(tmp_path, monkeypatch, platform, filename):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    directory = "Library/Application Support" if platform == "darwin" else ".local/share"
    expected = tmp_path / directory / "ai-council" / filename
    assert config._default_database(filename) == expected
    assert not expected.parent.exists()


def test_macos_demo_preserves_legacy_sessions(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    legacy = tmp_path / ".local" / "share" / "ai-council"
    db = legacy / "demo.sqlite3"
    assert main(["demo", "First session", "--database", str(db)]) == 0
    first = json.loads(capsys.readouterr().out)["session_id"]
    assert main(["demo", "Second session"]) == 0
    second = json.loads(capsys.readouterr().out)["session_id"]
    assert config._default_database("demo.sqlite3") == db
    assert not (tmp_path / "Library").exists()
    store = Store(db, read_only=True)
    try:
        assert {item["session_id"] for item in store.list_sessions()} == {first, second}
    finally:
        store.close()
    real_db = legacy / "council.sqlite3"
    real_db.touch()
    assert config._default_database("council.sqlite3") == real_db
