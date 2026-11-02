"""Rebuild the agent experiment table from the saved patches and recordings.

  python eval/agent_summary.py eval/results/agents
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from runsnap.diff import diff  # noqa: E402

out = Path(sys.argv[1])
rows = []
for patch in sorted(out.glob("*.patch")):
    name = patch.stem
    project, _, agent = name.rpartition("-")[0].rpartition("-")[0], None, name.rpartition("-")[2]
    snaps = out.parent / f"snaps-agents-{project}"
    d = diff(str(snaps / "base.sqlite"), str(snaps / f"{name}.sqlite"), rerun=str(snaps / "base2.sqlite"))
    lines = sum(1 for line in patch.read_text().splitlines() if line[:1] in "+-" and line[:3] not in ("+++", "---"))
    rows.append({"run": name, "agent": agent, "patch_lines": lines,
                 "changed": len(d["changed"]), "gone": len(d["gone"]), "new": len(d["new"]),
                 "new_functions": len(d["new_functions"]), "removed_functions": len(d["removed_functions"]),
                 "changed_examples": [{k: c[k] for k in ("func", "args", "before", "after")} for c in d["changed"][:3]]})
(out / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
for r in rows:
    print(f"{r['run']:<40} lines {r['patch_lines']:>4}  changed {r['changed']:>3}  gone {r['gone']:>3}  new {r['new']:>5}"
          f"  +fn {r['new_functions']}  -fn {r['removed_functions']}")
