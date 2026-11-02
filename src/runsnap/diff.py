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

    changes = []
    for key in sorted(a.keys() | b.keys()):
        if key in unstable or (key[0], key[1]) in unstable_funcs:
            continue
        test, func, args, _ = key
        if key in a and key in b:
            if a[key] != b[key]:
                changes.append(Change("changed", test, func, args, a[key], b[key]))
        elif key in a:
            changes.append(Change("gone", test, func, args, a[key], None))
        else:
            changes.append(Change("new", test, func, args, None, b[key]))

    return {
        "before": before,
        "after": after,
        "ignored_unstable": len(unstable_funcs),
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
    for kind, title in (("gone", "Calls that no longer happen"), ("new", "New calls")):
        items = result[kind]
        funcs = sorted({c["func"] for c in items})
        lines.append(f"\n{title}: {len(items)}" + (f" in {', '.join(funcs[:8])}" + (" …" if len(funcs) > 8 else "") if funcs else ""))
    return "\n".join(lines)
