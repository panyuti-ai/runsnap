"""Claude Code hook for runsnap.

UserPromptSubmit  a new turn starts: forget the previous turn's recordings.
PreToolUse        before Claude's first edit of a .py file in this turn, record the
                  tests twice (the second run tells apart values that change on every run).
                  If no .py file changed since the last turn's final recording, that
                  recording is reused as the first of the two.
Stop              record the tests again and compare. If any call now returns a different
                  result for the same input, ask Claude once to explain or fix it.

The project opts in with .claude/runsnap.json:
  {"modules": ["mypkg"], "tests": ["tests"], "python": ".venv/bin/python"}
runsnap must be installed in that Python. Without the file the hook does nothing.
Standard library only, so it runs with any python3.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path

CONFIG = Path(".claude") / "runsnap.json"
LISTED_FUNCTIONS = 8


def load_config(cwd: Path) -> dict | None:
    path = cwd / CONFIG
    if not path.is_file():
        return None
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if not cfg.get("modules"):
        return None
    python = cfg.get("python")
    if not python:
        venv = cwd / ".venv" / "bin" / "python"
        python = str(venv) if venv.exists() else "python3"
    tests = cfg.get("tests", [])
    return {
        "python": python,
        "modules": list(cfg["modules"]),
        "tests": [tests] if isinstance(tests, str) else list(tests),
        "timeout": int(cfg.get("timeout", 600)),
    }


def state_dir(cwd: Path, session: str) -> Path:
    """This turn's recordings; cleared when a new turn starts."""
    return cwd / ".runsnap" / "claude" / (session or "default") / "turn"


def last_dir(cwd: Path, session: str) -> Path:
    """The last turn's final recording, kept for the next turn."""
    return cwd / ".runsnap" / "claude" / (session or "default") / "last"


def fingerprint(cwd: Path) -> str:
    """Hash of every .py file in the project, so a reused recording is only used for
    exactly the code it was made from. Skips hidden folders (.venv, .git, .runsnap)."""
    digest = hashlib.sha256()
    for root, dirs, files in os.walk(cwd):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in ("__pycache__", "node_modules")
                         and not d.endswith(".egg-info"))
        for name in sorted(files):
            if name.endswith(".py"):
                path = Path(root) / name
                digest.update(str(path.relative_to(cwd)).encode() + b"\0")
                digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def run_tests(cfg: dict, cwd: Path, snapshot: Path) -> str:
    """Run the tests with recording on; return pytest's summary line."""
    cmd = [cfg["python"], "-m", "pytest", "-q", "-p", "no:cacheprovider", *cfg["tests"],
           f"--runsnap={snapshot}", *(f"--runsnap-module={m}" for m in cfg["modules"])]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=cfg["timeout"])
    if not snapshot.exists():
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
        raise RuntimeError("the tests did not produce a recording: " + " / ".join(tail))
    lines = proc.stdout.strip().splitlines()
    return lines[-1].strip("= ") if lines else ""


def compare(cfg: dict, cwd: Path, state: Path) -> dict:
    cmd = [cfg["python"], "-m", "runsnap", "diff", str(state / "base.sqlite"), str(state / "after.sqlite"),
           "--rerun", str(state / "base2.sqlite"), "--json", "--html", str(state / "report.html")]
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=cfg["timeout"])
    if proc.returncode not in (0, 1):
        raise RuntimeError(proc.stderr.strip() or "runsnap diff failed")
    return json.loads(proc.stdout)


def count_calls(snapshot: Path) -> int:
    with sqlite3.connect(snapshot) as db:
        return db.execute("SELECT COUNT(*) FROM calls").fetchone()[0]


def describe(result: dict, tests_line: str, report: Path) -> str:
    """The message Claude gets: one example per function whose results changed."""
    by_func: "OrderedDict[str, list]" = OrderedDict()
    for c in result["changed"]:
        by_func.setdefault(c["func"], []).append(c)
    n = len(result["changed"])
    lines = [f"runsnap recorded the tests before and after your edits. {n} call{'s' if n != 1 else ''} "
             f"now return{'' if n != 1 else 's'} a different result for the same input "
             f"({tests_line or 'tests ran'}):", ""]
    for func, calls in list(by_func.items())[:LISTED_FUNCTIONS]:
        c = calls[0]
        more = f"  (+{len(calls) - 1} more calls)" if len(calls) > 1 else ""
        lines += [f"- {func}({c['args']}){more}",
                  f"    in {c['test']}",
                  f"    before: {c['before']}",
                  f"    now:    {c['after']}"]
    if len(by_func) > LISTED_FUNCTIONS:
        lines.append(f"- … and {len(by_func) - LISTED_FUNCTIONS} more functions")
    lines += ["",
              "If these changes are intended, tell the user what changed and why. If they are not, "
              "fix the code so these calls return what they returned before.",
              f"Full report: {report}"]
    return "\n".join(lines)


def on_prompt(cwd: Path, session: str, cfg: dict) -> dict | None:
    shutil.rmtree(state_dir(cwd, session), ignore_errors=True)
    return None


def on_edit(cwd: Path, session: str, cfg: dict, event: dict) -> dict | None:
    tool_input = event.get("tool_input") or {}
    target = tool_input.get("file_path") or ""
    if not target.endswith(".py"):
        return None
    state = state_dir(cwd, session)
    if (state / "base2.sqlite").exists() or (state / "failed").exists():
        return None
    state.mkdir(parents=True, exist_ok=True)
    last = last_dir(cwd, session)
    try:
        reusable = (last / "after.sqlite").exists() and (last / "fingerprint").exists()
        if reusable and (last / "fingerprint").read_text() == fingerprint(cwd):
            shutil.copyfile(last / "after.sqlite", state / "base.sqlite")
            (state / "baseline").write_text("reused", encoding="utf-8")
        else:
            run_tests(cfg, cwd, state / "base.sqlite")
            (state / "baseline").write_text("recorded", encoding="utf-8")
        run_tests(cfg, cwd, state / "base2.sqlite")
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        (state / "failed").write_text(str(exc), encoding="utf-8")
        return {"systemMessage": f"runsnap: could not record the tests before the edit, so this turn is not checked ({exc})"}
    return None


def on_stop(cwd: Path, session: str, cfg: dict, event: dict) -> dict | None:
    state = state_dir(cwd, session)
    if not (state / "base2.sqlite").exists() or (state / "reported").exists():
        return None
    (state / "reported").write_text("1", encoding="utf-8")
    if event.get("stop_hook_active"):
        return None
    try:
        code = fingerprint(cwd)
        tests_line = run_tests(cfg, cwd, state / "after.sqlite")
        # Keep this recording for the next turn. If Claude or the user edits a .py file
        # after this point, the fingerprint no longer matches and it is not reused.
        last = last_dir(cwd, session)
        last.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(state / "after.sqlite", last / "after.sqlite")
        (last / "fingerprint").write_text(code, encoding="utf-8")
        result = compare(cfg, cwd, state)
    except (RuntimeError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return {"systemMessage": f"runsnap: could not compare behavior ({exc})"}
    report = state / "report.html"
    if result["changed"]:
        return {"decision": "block", "reason": describe(result, tests_line, report)}
    calls = count_calls(state / "after.sqlite")
    moved = len(result["gone"]) + len(result["new"])
    extra = f"; {moved} calls happen differently, see {report}" if moved else ""
    return {"systemMessage": f"runsnap: same results for all compared calls ({calls} recorded){extra}"}


def main() -> int:
    event = json.load(sys.stdin)
    cwd = Path(event.get("cwd") or os.getcwd())
    cfg = load_config(cwd)
    if cfg is None:
        return 0
    session = event.get("session_id", "")
    name = event.get("hook_event_name")
    if name == "UserPromptSubmit":
        out = on_prompt(cwd, session, cfg)
    elif name == "PreToolUse":
        out = on_edit(cwd, session, cfg, event)
    elif name == "Stop":
        out = on_stop(cwd, session, cfg, event)
    else:
        out = None
    if out:
        print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
