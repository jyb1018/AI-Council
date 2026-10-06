import json
import os
import subprocess
import sys
from pathlib import Path

from ai_council.cli import main


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
