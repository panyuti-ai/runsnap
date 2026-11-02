"""Agent refactor experiment: ask a coding agent to refactor one file without changing
behavior, then compare what the project's tests say with what runsnap sees.

For one (project, file, agent):
  1. check the project matches its clean copy, then record two baselines (base, base2);
  2. run the agent in the project with the same prompt for every agent;
  3. run the tests with recording on (after) and diff against base, ignoring anything
     that already differs between base and base2;
  4. save the agent's patch, the HTML report and a JSON summary;
  5. restore the project from its clean copy.

Usage:
  python eval/agent_refactor.py PROJECT_DIR PRISTINE_DIR PACKAGE TESTS FILE {claude,codex} --out DIR
"""
from __future__ import annotations

import argparse
import difflib
import filecmp
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from runsnap import report  # noqa: E402
from runsnap.diff import diff  # noqa: E402

PROMPT = (
    "Refactor {file} to make it cleaner and easier to maintain: simplify logic, remove "
    "duplication, and use modern Python idioms. Keep the public API the same and do not "
    "change behavior. Only edit {file}. You can run the test suite with:\n"
    "  {python} -m pytest -q -p no:cacheprovider {tests}"
)
AGENT_TIMEOUT = 30 * 60
CODEX_DIRS = (".git", ".agents", ".codex")
IGNORE = shutil.ignore_patterns("*.egg-info", "__pycache__", ".pytest_cache", ".runsnap", ".git", ".agents", ".codex")
SUMMARY = re.compile(r"(\d+) (passed|failed|errors?)")


def run_tests(project, package, tests, label=None, snapdir=None):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", tests]
    if label:
        env["RUNSNAP_DIR"] = str(snapdir)
        cmd += [f"--runsnap={label}", f"--runsnap-module={package}"]
    proc = subprocess.run(cmd, cwd=project, env=env, capture_output=True, text=True, timeout=600)
    counts = {"passed": 0, "failed": 0, "errors": 0}
    last = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    for n, kind in SUMMARY.findall(last):
        counts["errors" if kind.startswith("error") else kind] = int(n)
    return counts


def differences(project: Path, pristine: Path) -> list[str]:
    """Files that differ between the project and its clean copy (ignoring build leftovers)."""
    out = []

    def walk(cmp, prefix=""):
        skip = {"__pycache__", ".pytest_cache", ".runsnap", ".git", ".agents", ".codex"}
        out.extend(prefix + n for n in cmp.diff_files)
        out.extend(prefix + n for n in cmp.left_only if n not in skip and not n.endswith(".egg-info"))
        out.extend(prefix + n for n in cmp.right_only)
        for name, sub in cmp.subdirs.items():
            if name not in skip and not name.endswith(".egg-info"):
                walk(sub, prefix + name + "/")

    walk(filecmp.dircmp(project, pristine))
    return out


def restore(project: Path, pristine: Path):
    for rel in differences(project, pristine):
        target = project / rel
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    shutil.copytree(pristine, project, dirs_exist_ok=True, ignore=IGNORE)
    left = differences(project, pristine)
    if left:
        raise RuntimeError(f"could not restore {project}: {left}")


def run_agent(agent, project, prompt, log_path):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    if agent == "claude":
        cmd = ["claude", "-p", prompt, "--output-format", "json", "--setting-sources", "",
               "--permission-mode", "acceptEdits",
               "--allowedTools", "Read", "Edit", "Write", "Glob", "Grep",
               f"Bash({sys.executable} -m pytest:*)"]
    else:
        # Codex's workspace sandbox (bubblewrap) fails to start unless the folders it keeps
        # read-only already exist; empty ones are enough. They are removed after the run.
        for name in CODEX_DIRS:
            (project / name).mkdir(exist_ok=True)
        codex = os.environ.get("CODEX_BIN", "codex")
        cmd = [codex, "exec", "--json", "--skip-git-repo-check", "--sandbox", "workspace-write", prompt]
    started = time.time()
    try:
        proc = subprocess.run(cmd, cwd=project, env=env, capture_output=True, text=True, timeout=AGENT_TIMEOUT)
        out, err, code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        out, err, code = exc.stdout or "", exc.stderr or "", "timeout"
        out = out.decode() if isinstance(out, bytes) else out
        err = err.decode() if isinstance(err, bytes) else err
    log_path.write_text(out + "\n--- stderr ---\n" + err, encoding="utf-8")
    return {"exit": code, "seconds": round(time.time() - started)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("pristine")
    ap.add_argument("package")
    ap.add_argument("tests")
    ap.add_argument("file", help="file to refactor, relative to the project")
    ap.add_argument("agent", choices=["claude", "codex"])
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    project, pristine = Path(args.project).resolve(), Path(args.pristine).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    name = f"{project.name}-{Path(args.file).stem}-{args.agent}"
    snapdir = out.parent / f"snaps-agents-{project.name}"
    snapdir.mkdir(parents=True, exist_ok=True)

    if differences(project, pristine):
        sys.exit(f"{project} does not match {pristine}; restore it first")
    original = (project / args.file).read_text(encoding="utf-8")

    if not (snapdir / "base2.sqlite").exists():
        run_tests(project, args.package, args.tests, "base", snapdir)
        run_tests(project, args.package, args.tests, "base2", snapdir)
    before_tests = run_tests(project, args.package, args.tests)

    prompt = PROMPT.format(file=args.file, python=sys.executable, tests=args.tests)
    try:
        agent = run_agent(args.agent, project, prompt, out / f"{name}.log")
        changed_files = differences(project, pristine)
        refactored = (project / args.file).read_text(encoding="utf-8")
        patch = "".join(difflib.unified_diff(original.splitlines(True), refactored.splitlines(True),
                                             f"a/{args.file}", f"b/{args.file}"))
        (out / f"{name}.patch").write_text(patch, encoding="utf-8")

        after_tests = run_tests(project, args.package, args.tests, name, snapdir)
        d = diff(str(snapdir / "base.sqlite"), str(snapdir / f"{name}.sqlite"), rerun=str(snapdir / "base2.sqlite"))
        report.write(str(out / f"{name}.html"), str(snapdir / "base.sqlite"),
                     str(snapdir / f"{name}.sqlite"), str(snapdir / "base2.sqlite"))
    finally:
        if args.agent == "codex":
            for name in CODEX_DIRS:
                if not (pristine / name).exists():
                    shutil.rmtree(project / name, ignore_errors=True)
        restore(project, pristine)

    funcs = sorted({c["func"] for kind in ("changed", "gone", "new") for c in d[kind]})
    summary = {
        "project": project.name, "file": args.file, "agent": args.agent, "prompt": prompt,
        "agent_run": agent, "files_touched": changed_files,
        "patch_lines": sum(1 for line in patch.splitlines() if line[:1] in "+-" and line[:3] not in ("+++", "---")),
        "tests_before": before_tests, "tests_after": after_tests,
        "tests_pass": after_tests["failed"] == 0 and after_tests["errors"] <= before_tests["errors"] and after_tests["passed"] > 0,
        "changed": len(d["changed"]), "gone": len(d["gone"]), "new": len(d["new"]),
        "functions_flagged": funcs,
        "examples": (d["changed"] + d["gone"] + d["new"])[:5],
    }
    (out / f"{name}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("agent_run", "files_touched", "patch_lines", "tests_after",
                                               "tests_pass", "changed", "gone", "new", "functions_flagged")}, indent=1))


if __name__ == "__main__":
    main()
