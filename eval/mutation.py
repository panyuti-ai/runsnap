"""Mutation experiment: of the code changes a project's own tests miss, how many does runsnap flag?

For each project:
  1. record two baseline snapshots of the unchanged code (base, base2);
  2. make one small mutation in the package source (e.g. `<` -> `<=`, `+` -> `-`, 2 -> 3);
  3. run the tests with recording on;
  4. if the tests fail, the tests caught it; if they pass, diff against base (ignoring
     anything that already differs between base and base2) and count it as flagged when
     any behavior changed, a call disappeared, or a new call appeared;
  5. restore the file.

Usage:
  python eval/mutation.py PROJECT_DIR PACKAGE TEST_PATH --mutants 60 --seed 1 --out results.json
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from runsnap.diff import diff  # noqa: E402

SWAPS = {
    ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.FloorDiv, ast.FloorDiv: ast.Mult,
    ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.And: ast.Or, ast.Or: ast.And,
    ast.In: ast.NotIn, ast.NotIn: ast.In, ast.Is: ast.IsNot, ast.IsNot: ast.Is,
}


def sites(tree):
    """Every place one mutation can be made: (node, field, index, description)."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.BinOp, ast.BoolOp)) and type(node.op) in SWAPS:
            out.append((node, "op", None, f"{type(node.op).__name__} -> {SWAPS[type(node.op)].__name__}"))
        elif isinstance(node, ast.Compare):
            for i, op in enumerate(node.ops):
                if type(op) in SWAPS:
                    out.append((node, "ops", i, f"{type(op).__name__} -> {SWAPS[type(op)].__name__}"))
        elif isinstance(node, ast.Constant) and type(node.value) is int and not isinstance(node.value, bool):
            out.append((node, "value", None, f"{node.value} -> {node.value + 1}"))
    return [s for s in out if hasattr(s[0], "lineno")]


def apply(site):
    node, field, index, _ = site
    if field == "op":
        old = node.op
        node.op = SWAPS[type(old)]()
        return lambda: setattr(node, "op", old)
    if field == "ops":
        old = node.ops[index]
        node.ops[index] = SWAPS[type(old)]()
        return lambda: node.ops.__setitem__(index, old)
    old = node.value
    node.value = old + 1
    return lambda: setattr(node, "value", old)


TIMEOUT = 120  # seconds; the unchanged suites take under 5
SUMMARY = re.compile(r"(\d+) (passed|failed|errors?)")


def run_tests(project, package, tests, label, snapdir):
    env = dict(os.environ, RUNSNAP_DIR=str(snapdir), PYTHONDONTWRITEBYTECODE="1")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", tests,
             f"--runsnap={label}", f"--runsnap-module={package}"],
            cwd=project, env=env, capture_output=True, text=True, timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        # A mutant that makes the suite hang (e.g. an endless loop) is caught by the tests.
        return {"passed": 0, "failed": 0, "errors": 0, "timeout": True}
    counts = {"passed": 0, "failed": 0, "errors": 0}
    for n, kind in SUMMARY.findall(proc.stdout.splitlines()[-1] if proc.stdout else ""):
        counts["errors" if kind.startswith("error") else kind] = int(n)
    return counts


def line_coverage(project, pkg_path, tests, snapdir):
    """Source lines of the package that the unchanged test suite executes."""
    out = snapdir / "lines.json"
    snapdir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, LINECOV_ROOT=str(pkg_path), LINECOV_OUT=str(out),
               PYTHONPATH=str(Path(__file__).resolve().parent), PYTHONDONTWRITEBYTECODE="1")
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "linecov", tests],
                   cwd=project, env=env, capture_output=True, text=True, timeout=600)
    data = json.loads(out.read_text())
    return {(os.path.realpath(p), ln) for p, lines in data.items() for ln in lines}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("package")
    ap.add_argument("tests")
    ap.add_argument("--mutants", type=int, default=60)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    project = Path(args.project).resolve()
    pkg_path = project / args.package
    if not pkg_path.exists():
        pkg_path = project / "src" / args.package
    files = [pkg_path] if pkg_path.suffix == ".py" else sorted(pkg_path.rglob("*.py"))
    files = [f for f in files if "test" not in f.name]
    snapdir = (Path(args.out).resolve().parent / f"snaps-{project.name}")

    # Mutants are written with ast.unparse, which also reformats the file. Normalize every
    # file the same way first, so the baseline differs from each mutant only by the mutation.
    originals = {f: f.read_text(encoding="utf-8") for f in files}
    try:
        for f, text in originals.items():
            f.write_text(ast.unparse(ast.parse(text)), encoding="utf-8")
        run_experiment(args, project, pkg_path, files, snapdir)
    finally:
        for f, text in originals.items():
            f.write_text(text, encoding="utf-8")


def run_experiment(args, project, pkg_path, files, snapdir):
    base = run_tests(project, args.package, args.tests, "base", snapdir)
    run_tests(project, args.package, args.tests, "base2", snapdir)
    executed = line_coverage(project, pkg_path, args.tests, snapdir)

    all_sites = []
    for f in files:
        src = f.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for i, s in enumerate(sites(tree)):
            all_sites.append((f, i, s[3], s[0].lineno))
    rng = random.Random(args.seed)
    chosen = rng.sample(all_sites, min(args.mutants, len(all_sites)))

    results = []
    for n, (f, idx, desc, line) in enumerate(chosen, 1):
        original = f.read_text(encoding="utf-8")
        tree = ast.parse(original)
        site = sites(tree)[idx]
        apply(site)
        try:
            f.write_text(ast.unparse(tree), encoding="utf-8")
            started = time.time()
            got = run_tests(project, args.package, args.tests, "mutant", snapdir)
            caught_by_tests = got["failed"] > 0 or got["errors"] > base["errors"] or got["passed"] == 0
            entry = {
                "file": str(f.relative_to(project)), "line": line, "mutation": desc,
                "reached": (os.path.realpath(f), line) in executed,
                "caught_by_tests": caught_by_tests, "seconds": round(time.time() - started, 1),
            }
            if not caught_by_tests:
                d = diff(str(snapdir / "base.sqlite"), str(snapdir / "mutant.sqlite"),
                         rerun=str(snapdir / "base2.sqlite"))
                entry.update(changed=len(d["changed"]), gone=len(d["gone"]), new=len(d["new"]),
                             flagged=bool(d["changed"] or d["gone"] or d["new"]),
                             flagged_changed_result=bool(d["changed"]),
                             example=(d["changed"] or d["gone"] or d["new"] or [None])[0])
            results.append(entry)
            status = "tests" if caught_by_tests else ("runsnap" if entry.get("flagged") else ("missed" if entry["reached"] else "not reached"))
            print(f"[{n}/{len(chosen)}] {entry['file']}:{line} {desc} -> {status}", flush=True)
        finally:
            f.write_text(original, encoding="utf-8")

    survived = [r for r in results if not r["caught_by_tests"]]
    reached = [r for r in survived if r["reached"]]
    summary = {
        "project": project.name, "package": args.package, "seed": args.seed,
        "baseline": base, "mutants": len(results),
        "caught_by_tests": len(results) - len(survived),
        "survived_tests": len(survived),
        "survived_not_reached": len(survived) - len(reached),
        "survived_reached": len(reached),
        "flagged_by_runsnap": sum(r["flagged"] for r in reached),
        "flagged_changed_result": sum(r["flagged_changed_result"] for r in reached),
    }
    Path(args.out).write_text(json.dumps({"summary": summary, "mutants": results}, ensure_ascii=False, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
