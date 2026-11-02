"""One-file HTML report for `runsnap diff --html`: a map of every recorded function,
with the ones whose behavior changed in red, and a card that shows before and now."""
from __future__ import annotations

import html
import json
from collections import defaultdict

from . import diff as diff_mod
from . import store

MAX_GROUPS = 200  # per function; each group is one distinct before -> now pattern


def _call_counts(label: str) -> dict[str, int]:
    db = store.open_snapshot(label)
    return {r["func"]: r["n"] for r in db.execute("SELECT func, COUNT(*) AS n FROM calls GROUP BY func")}


def _group(func_changes: list[dict]) -> list[dict]:
    """Merge calls that changed the same way, keeping one example input."""
    groups: dict[tuple, dict] = {}
    for c in func_changes:
        key = (c["kind"], c["before"], c["after"])
        if key not in groups:
            groups[key] = {"kind": c["kind"], "before": c["before"], "after": c["after"],
                           "args": c["args"], "test": c["test"], "count": 0}
        groups[key]["count"] += 1
    order = {"changed": 0, "gone": 1, "new": 2}
    return sorted(groups.values(), key=lambda g: (order[g["kind"]], -g["count"]))[:MAX_GROUPS]


def build(before: str, after: str, rerun: str | None = None) -> str:
    result = diff_mod.diff(before, after, rerun)
    counts = _call_counts(before)
    for func, n in _call_counts(after).items():
        counts[func] = max(n, counts.get(func, 0))

    unstable = set()
    if rerun:
        noise = diff_mod.diff(before, rerun)
        unstable = {c["func"] for kind in ("changed", "gone", "new") for c in noise[kind]}

    by_func: dict[str, list[dict]] = defaultdict(list)
    for kind in ("changed", "gone", "new"):
        for c in result[kind]:
            by_func[c["func"]].append(c)

    changes = {}
    for func, items in by_func.items():
        changes[func] = {
            "changed": sum(c["kind"] == "changed" for c in items),
            "gone": sum(c["kind"] == "gone" for c in items),
            "new": sum(c["kind"] == "new" for c in items),
            "tests": len({c["test"] for c in items}),
            "groups": _group(items),
        }

    funcs = [[f, n, "changed" if f in changes else "skipped" if f in unstable else "same"]
             for f, n in sorted(counts.items())]
    data = {"before": before, "after": after, "rerun": rerun, "funcs": funcs, "changes": changes}
    # Keep "</script>" inside values from ending the script block.
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    title = f"runsnap: {html.escape(before)} → {html.escape(after)}"
    return _PAGE.replace("__TITLE__", title).replace("__DATA__", payload)


def write(path: str, before: str, after: str, rerun: str | None = None) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(build(before, after, rerun))


_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root {
  --bg: #f7f6f2; --panel: #ffffff; --text: #1d1d1b; --muted: #85847e; --line: #e6e4de;
  --same: #d9e7dc; --same-hover: #c4dbc9; --chg: #e5484d; --chg-soft: #fdecec; --on-chg: #ffffff;
  --skip: #efe6cf; --skip-line: #c9a94a; --old: #6b6b66; --focus: #1d1d1b;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #151514; --panel: #1f1f1d; --text: #ecebe6; --muted: #8f8e87; --line: #33322f;
    --same: #2a3a2e; --same-hover: #34493a; --chg: #ff6369; --chg-soft: #3b1c1d; --on-chg: #1b0b0b;
    --skip: #3a3322; --skip-line: #b8973a; --old: #a5a49d; --focus: #ecebe6; color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --bg: #151514; --panel: #1f1f1d; --text: #ecebe6; --muted: #8f8e87; --line: #33322f;
  --same: #2a3a2e; --same-hover: #34493a; --chg: #ff6369; --chg-soft: #3b1c1d; --on-chg: #1b0b0b;
  --skip: #3a3322; --skip-line: #b8973a; --old: #a5a49d; --focus: #ecebe6; color-scheme: dark;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
.mono { font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; }
.wrap { max-width: 1120px; margin: 0 auto; padding: 32px max(16px, env(safe-area-inset-right, 0px)) 64px max(16px, env(safe-area-inset-left, 0px)); }
.hero { display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; }
.hero .big { font-size: 56px; font-weight: 700; line-height: 1; font-variant-numeric: tabular-nums; }
.hero .big.bad { color: var(--chg); }
.hero .txt { font-size: 22px; font-weight: 600; }
.hero .txt span { color: var(--muted); font-weight: 400; }
.labels { color: var(--muted); font-size: 13px; margin-top: 6px; word-break: break-all; }
.legend { display: flex; gap: 18px; margin: 14px 0 12px; color: var(--muted); font-size: 13px; flex-wrap: wrap; }
.legend i { display: inline-block; width: 12px; height: 12px; border-radius: 3px; margin-right: 6px; vertical-align: -1px; }
.jump { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 18px; }
.jump button { border: 0; background: var(--chg-soft); color: var(--chg); border-radius: 999px; padding: 3px 11px; cursor: pointer; font: 13px ui-monospace, Menlo, Consolas, monospace; }
.jump button.sel { background: var(--chg); color: var(--on-chg); }
.layout { display: grid; grid-template-columns: 1.2fr 1fr; gap: 18px; align-items: start; }
.map { display: grid; gap: 12px; min-width: 0; }
.mod { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 12px; }
.mod h3 { margin: 0 0 8px; font-size: 13px; color: var(--muted); font-weight: 600; word-break: break-all; }
.tiles { display: flex; flex-wrap: wrap; gap: 5px; }
.tile { border-radius: 6px; background: var(--same); cursor: pointer; border: 2px solid transparent; padding: 0; transition: transform .1s; }
.tile:hover { background: var(--same-hover); transform: scale(1.06); }
.tile:focus-visible, .jump button:focus-visible, .more:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; }
.tile.changed { background: var(--chg); animation: pulse 1.6s ease-in-out infinite; }
.tile.skipped { background: var(--skip); border: 2px dashed var(--skip-line); }
.tile.sel { outline: 3px solid var(--focus); outline-offset: 2px; }
@keyframes pulse { 50% { box-shadow: 0 0 0 5px color-mix(in srgb, var(--chg) 25%, transparent); } }
@media (prefers-reduced-motion: reduce) { .tile.changed { animation: none; } .tile { transition: none; } }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: 22px; position: sticky; top: 16px; min-width: 0; }
.card .fn { font-size: 20px; font-weight: 700; word-break: break-all; }
.card .where { color: var(--muted); font-size: 13px; margin-bottom: 16px; word-break: break-all; }
.row { display: grid; grid-template-columns: 64px minmax(0, 1fr); gap: 12px; align-items: center; margin: 10px 0; }
.row .k { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .05em; }
.args { background: var(--bg); border-radius: 8px; padding: 6px 10px; font-size: 13px; word-break: break-all; }
.val { font-size: 26px; font-weight: 600; word-break: break-all; }
.val.small { font-size: 15px; font-weight: 500; color: var(--muted); }
.val.old { color: var(--old); }
.val.new { color: var(--chg); }
.val ins, .vals ins { background: var(--chg-soft); color: var(--chg); text-decoration: none; border-radius: 5px; padding: 0 2px; }
.val del, .vals del { color: var(--old); text-decoration-thickness: 3px; }
.arrow { color: var(--muted); font-size: 22px; padding-left: 76px; line-height: 1; }
.foot { margin-top: 18px; padding-top: 14px; border-top: 1px solid var(--line); color: var(--muted); font-size: 13px; display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }
.more { border: 1px solid var(--line); background: var(--panel); color: var(--text); border-radius: 8px; padding: 4px 12px; cursor: pointer; font: inherit; }
.all { margin-top: 12px; display: grid; gap: 8px; }
.all[hidden] { display: none; }
.all .g { border: 1px solid var(--line); border-radius: 10px; padding: 8px 10px; font-size: 13px; min-width: 0; }
.all .g .n { font-weight: 700; color: var(--chg); margin-right: 6px; }
.all .g .vals { margin-top: 4px; word-break: break-all; }
.all .g .a { color: var(--muted); word-break: break-all; }
.empty { color: var(--muted); }
@media (max-width: 760px) { .layout { grid-template-columns: 1fr; } .card { position: static; } .hero .big { font-size: 44px; } }
</style>
</head>
<body>
<div class="wrap">
  <div class="hero"><div class="big" id="n"></div><div class="txt" id="headline"></div></div>
  <div class="labels mono" id="labels"></div>
  <div class="legend">
    <span><i style="background:var(--same)"></i>same as before</span>
    <span><i style="background:var(--chg)"></i>behavior changed</span>
    <span><i style="background:var(--skip);border:2px dashed var(--skip-line)"></i>skipped: changes on every run</span>
    <span>bigger box = called more often</span>
  </div>
  <div class="jump" id="jump"></div>
  <div class="layout">
    <div class="map" id="map"></div>
    <div class="card" id="card"></div>
  </div>
</div>
<script>
const DATA = __DATA__;
const changedFuncs = Object.keys(DATA.changes).sort((a, b) => total(DATA.changes[b]) - total(DATA.changes[a]));
let selected = changedFuncs[0] || (DATA.funcs[0] && DATA.funcs[0][0]);
let showAll = false;

function total(c) { return c.changed + c.gone + c.new; }
function esc(s) { return String(s).replace(/[&<>"]/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch])); }
function moduleOf(f) {
  const parts = f.split(".");
  parts.pop();
  while (parts.length > 1 && /^[A-Z]/.test(parts[parts.length - 1])) parts.pop();  // methods go with their module
  return parts.join(".") || f;
}
function shortName(f) { const m = moduleOf(f); return f.startsWith(m + ".") ? f.slice(m.length + 1) : f; }
function strip(outcome) { return outcome.startsWith("returns ") ? outcome.slice(8) : outcome; }

// Word-level diff, so False -> True is marked as one word and (7.0, 0) -> (7.0, 1) marks only the 0.
function wordDiff(sa, sb) {
  const tok = s => s.match(/[A-Za-z0-9_.]+|[^A-Za-z0-9_.]/g) || [];
  const a = tok(sa), b = tok(sb), n = a.length, m = b.length;
  if (n * m > 250000) return [esc(sa), "<ins>" + esc(sb) + "</ins>"];
  const dp = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  let L = "", R = "", i = 0, j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && a[i] === b[j]) { L += esc(a[i]); R += esc(b[j]); i++; j++; }
    else if (j < m && (i >= n || dp[i][j + 1] >= dp[i + 1][j])) { R += "<ins>" + esc(b[j]) + "</ins>"; j++; }
    else { L += "<del>" + esc(a[i]) + "</del>"; i++; }
  }
  return [L, R];
}

function drawHeader() {
  const n = changedFuncs.length;
  document.getElementById("n").textContent = n;
  document.getElementById("n").className = "big" + (n ? " bad" : "");
  document.getElementById("headline").innerHTML =
    (n === 1 ? "function behaves differently" : "functions behave differently") +
    ` <span>of ${DATA.funcs.length} recorded</span>`;
  document.getElementById("labels").textContent =
    `before: ${DATA.before} → now: ${DATA.after}` + (DATA.rerun ? ` · noise filtered with: ${DATA.rerun}` : "");
  document.getElementById("jump").innerHTML = changedFuncs.map(f =>
    `<button class="${f === selected ? "sel" : ""}" data-f="${esc(f)}">${esc(shortName(f))}</button>`).join("");
  document.querySelectorAll(".jump button").forEach(b => b.addEventListener("click", () => select(b.dataset.f)));
}

function drawMap() {
  const mods = {};
  DATA.funcs.forEach(row => (mods[moduleOf(row[0])] ??= []).push(row));
  const max = Math.max(1, ...DATA.funcs.map(r => r[1]));
  document.getElementById("map").innerHTML = Object.keys(mods).sort().map(m => `
    <div class="mod"><h3 class="mono">${esc(m)}</h3><div class="tiles">${mods[m].map(([f, n, state]) => {
      const s = Math.round(18 + 62 * Math.sqrt(n / max));
      const label = `${shortName(f)} · ${n} calls` + (state === "skipped" ? " · skipped (changes every run)" : "");
      return `<button class="tile ${state} ${f === selected ? "sel" : ""}" data-f="${esc(f)}" title="${esc(label)}"
        aria-label="${esc(label)}" style="width:${s}px;height:${s}px"></button>`;
    }).join("")}</div></div>`).join("") || `<p class="empty">No calls were recorded.</p>`;
  document.querySelectorAll(".tile").forEach(t => t.addEventListener("click", () => select(t.dataset.f)));
}

function valuePair(g) {
  if (g.kind === "changed") return wordDiff(strip(g.before), strip(g.after));
  if (g.kind === "gone") return [esc(strip(g.before)), null];
  return [null, esc(strip(g.after))];
}

function drawCard() {
  const card = document.getElementById("card");
  if (!selected) { card.innerHTML = `<p class="empty">Nothing to show.</p>`; return; }
  const c = DATA.changes[selected];
  const head = `<div class="fn mono">${esc(shortName(selected))}</div><div class="where mono">${esc(moduleOf(selected))}</div>`;
  if (!c) {
    const row = DATA.funcs.find(r => r[0] === selected);
    const msg = row[2] === "skipped"
      ? "Skipped: its results already differ between two runs of the same code (time, random values), so changes here are not reported."
      : "✓ Every recorded call gives the same result as before.";
    card.innerHTML = head + `<p class="empty">${row[1]} calls recorded. ${msg}</p>`;
    return;
  }
  const g = c.groups[0];
  const [b, a] = valuePair(g);
  const parts = [];
  if (c.changed) parts.push(`${c.changed} results changed`);
  if (c.gone) parts.push(`${c.gone} calls gone`);
  if (c.new) parts.push(`${c.new} new calls`);
  parts.push(`${c.tests} test${c.tests === 1 ? "" : "s"}`);
  const n = total(c);
  card.innerHTML = head + `
    <div class="row"><div class="k">Input</div><div class="args mono">${esc(g.args) || "(no arguments)"}</div></div>
    <div class="row"><div class="k">Before</div><div class="val old mono ${b === null ? "small" : ""}">${b ?? "not called with this input"}</div></div>
    <div class="arrow">↓</div>
    <div class="row"><div class="k">Now</div><div class="val new mono ${a === null ? "small" : ""}">${a ?? "not called with this input"}</div></div>
    <div class="foot"><span>${parts.join(" · ")} · e.g. in ${esc(g.test.split("::").pop())}</span>
      ${n > 1 ? `<button class="more" id="more">${showAll ? "Hide" : `Show all ${n}`}</button>` : ""}</div>
    <div class="all" id="all" ${showAll ? "" : "hidden"}>${c.groups.map(gr => {
      const [x, y] = valuePair(gr);
      return `<div class="g"><span class="n">${gr.count}×</span><span class="a mono">${esc(gr.args)}</span>
        <div class="vals mono">${x ?? "<i>not called</i>"} → ${y ?? "<i>not called</i>"}</div></div>`;
    }).join("")}</div>`;
  const more = document.getElementById("more");
  if (more) more.addEventListener("click", () => { showAll = !showAll; drawCard(); });
}

function select(f) { selected = f; showAll = false; drawHeader(); drawMap(); drawCard(); }
select(selected);
</script>
</body>
</html>
"""
