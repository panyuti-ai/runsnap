"""Compare two snapshots and list behavior that changed."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass

from . import store


@dataclass
class Change:
    kind: str          # "changed", "gone", "new"
    test: str
    func: str
    args: str
    before: str | None
    after: str | None


def _outcome(row) -> str:
    return f"raises {row['exc']}" if row["exc"] is not None else f"returns {row['result']}"


def _truncated_tests(label: str) -> set[str]:
    """Tests whose recording hit the per-test call limit, so their later calls are missing."""
    db = store.open_snapshot(label)
    row = db.execute("SELECT value FROM meta WHERE key = 'truncated_tests'").fetchone()
    return set(row["value"].splitlines()) if row and row["value"] else set()


def _index(label: str) -> dict:
    """(test, func, args, n-th occurrence) -> outcome."""
    db = store.open_snapshot(label)
    seen = defaultdict(int)
    out = {}
    for row in db.execute("SELECT test, func, args, result, exc FROM calls ORDER BY test, seq"):
        base = (row["test"], row["func"], row["args"])
        n = seen[base]
        seen[base] += 1
        out[base + (n,)] = _outcome(row)
    return out


def diff(before: str, after: str, rerun: str | None = None) -> dict:
    """Changes from `before` to `after`.

    `rerun` is a second recording of `before` made without code changes.
    Anything that already differs between `before` and `rerun` (time, random
    values, ordering) is treated as unstable and left out.
    """
    a, b = _index(before), _index(after)
    unstable = set()
    if rerun:
        r = _index(rerun)
        unstable = {k for k in a.keys() | r.keys() if a.get(k) != r.get(k)}
        unstable_funcs = {(k[0], k[1]) for k in unstable}
    else:
        unstable_funcs = set()

    # A function that exists on only one side was added or removed as a whole (a
    # refactor that splits out or inlines a helper). Its calls are not behavior changes:
    # if the split or inline broke something, the callers' results change instead.
    funcs_a, funcs_b = {k[1] for k in a}, {k[1] for k in b}
    added = defaultdict(int)
    removed = defaultdict(int)
    # Tests that hit the per-test call limit on either side stop being recorded at a point
    # that moves when the code changes, so their missing or extra calls mean nothing.
    truncated = _truncated_tests(before) | _truncated_tests(after)

    changes = []
    for key in sorted(a.keys() | b.keys()):
        if key in unstable or (key[0], key[1]) in unstable_funcs:
            continue
        test, func, args, _ = key
        if key in a and key in b:
            if a[key] != b[key]:
                changes.append(Change("changed", test, func, args, a[key], b[key]))
        elif func not in funcs_b:
            removed[func] += 1
        elif func not in funcs_a:
            added[func] += 1
        elif test in truncated:
            continue
        elif key in a:
            changes.append(Change("gone", test, func, args, a[key], None))
        else:
            changes.append(Change("new", test, func, args, None, b[key]))

    return {
        "before": before,
        "after": after,
        "ignored_unstable": len(unstable_funcs),
        "new_functions": dict(sorted(added.items())),
        "removed_functions": dict(sorted(removed.items())),
        "truncated_tests": sorted(truncated),
        "changed": [asdict(c) for c in changes if c.kind == "changed"],
        "gone": [asdict(c) for c in changes if c.kind == "gone"],
        "new": [asdict(c) for c in changes if c.kind == "new"],
    }


def format_text(result: dict, limit: int = 50) -> str:
    lines = [f"runsnap diff {result['before']} -> {result['after']}"]
    if result["ignored_unstable"]:
        lines.append(f"(ignored {result['ignored_unstable']} unstable function(s) that differ between reruns)")
    changed = result["changed"]
    lines.append(f"\nBehavior changed: {len(changed)}")
    for c in changed[:limit]:
        lines.append(f"  {c['func']}({c['args']})")
        lines.append(f"      in {c['test']}")
        lines.append(f"      before: {c['before']}")
        lines.append(f"      after:  {c['after']}")
    if len(changed) > limit:
        lines.append(f"  … {len(changed) - limit} more")
    for key, title in (("new_functions", "New functions (not compared)"),
                       ("removed_functions", "Removed functions (not compared)")):
        if result.get(key):
            lines.append(f"\n{title}: " + ", ".join(f"{f} ({n} calls)" for f, n in result[key].items()))
    if result.get("truncated_tests"):
        lines.append(f"\n(skipped missing/extra calls in {len(result['truncated_tests'])} test(s) "
                     "that hit the recording limit)")
    for kind, title in (("gone", "Calls that no longer happen"), ("new", "New calls")):
        items = result[kind]
        funcs = sorted({c["func"] for c in items})
        lines.append(f"\n{title}: {len(items)}" + (f" in {', '.join(funcs[:8])}" + (" …" if len(funcs) > 8 else "") if funcs else ""))
    return "\n".join(lines)
