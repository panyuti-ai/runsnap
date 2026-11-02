"""The Claude Code hook, driven the way Claude Code drives it: JSON on stdin, JSON on stdout."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_runsnap import SHOP_V1, TESTS

HOOK = Path(__file__).resolve().parents[1] / "plugins" / "runsnap" / "scripts" / "hook.py"


def hook(project, event_name, **fields):
    event = {"hook_event_name": event_name, "session_id": "s1", "cwd": str(project.path), **fields}
    proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event), capture_output=True,
                          text=True, cwd=project.path, timeout=300, check=True)
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def edit(path):
    return {"tool_name": "Edit", "tool_input": {"file_path": str(path)}}


@pytest.fixture
def project(pytester, monkeypatch):
    pytester.makepyfile(shop=SHOP_V1, test_shop=TESTS)
    (pytester.path / ".claude").mkdir()
    (pytester.path / ".claude" / "runsnap.json").write_text(
        json.dumps({"modules": ["shop"], "python": sys.executable}))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    return pytester


def test_changed_result_blocks_once_with_the_examples(project):
    assert hook(project, "UserPromptSubmit") is None
    assert hook(project, "PreToolUse", **edit(project.path / "shop.py")) is None
    # Claude's edit: the tests still pass, but the tax rule changed.
    project.makepyfile(shop=SHOP_V1.replace("round(amount * rate, 2)", "round(amount * rate + 0.5, 2)"))

    out = hook(project, "Stop", stop_hook_active=False)
    assert out["decision"] == "block"
    assert "shop.calc_tax(amount=150, rate=0.05)" in out["reason"]
    assert "before: returns 7.5" in out["reason"] and "now:    returns 8.0" in out["reason"]
    assert "shop.checkout" not in out["reason"]  # time and random differ on every run

    # Claude answers and stops again: no second block in the same turn.
    assert hook(project, "Stop", stop_hook_active=True) is None


def test_unchanged_behavior_only_reports_to_the_user(project):
    hook(project, "UserPromptSubmit")
    hook(project, "PreToolUse", **edit(project.path / "shop.py"))
    project.makepyfile(shop=SHOP_V1.replace("return round(amount * rate, 2)", "taxed = amount * rate\n    return round(taxed, 2)"))
    out = hook(project, "Stop", stop_hook_active=False)
    assert "decision" not in out
    assert out["systemMessage"].startswith("runsnap: same results for all compared calls")


def test_does_nothing_without_python_edits_or_config(project, tmp_path):
    hook(project, "UserPromptSubmit")
    assert hook(project, "PreToolUse", **edit(project.path / "README.md")) is None
    assert hook(project, "Stop", stop_hook_active=False) is None  # nothing recorded, nothing to compare

    (project.path / ".claude" / "runsnap.json").unlink()
    assert hook(project, "PreToolUse", **edit(project.path / "shop.py")) is None
    assert not (project.path / ".runsnap").exists()


def test_new_turn_starts_a_new_baseline(project):
    hook(project, "UserPromptSubmit")
    hook(project, "PreToolUse", **edit(project.path / "shop.py"))
    project.makepyfile(shop=SHOP_V1.replace("rate=0.05", "rate=0.06"))
    hook(project, "Stop", stop_hook_active=False)
    # Next turn: the new rate is now the baseline, so an unrelated edit reports no change.
    hook(project, "UserPromptSubmit")
    hook(project, "PreToolUse", **edit(project.path / "shop.py"))
    project.makepyfile(shop=SHOP_V1.replace("rate=0.05", "rate=0.06") + "\n# comment\n")
    out = hook(project, "Stop", stop_hook_active=False)
    assert "decision" not in out
