"""runsnap command line.

  runsnap diff BEFORE AFTER [--rerun BEFORE2] [--json] [--html report.html]
  runsnap calls SNAPSHOT FUNCTION [--test NAME]
  runsnap slowest SNAPSHOT
  runsnap list
  runsnap mcp                     # start the MCP server (needs the 'mcp' extra)
"""
from __future__ import annotations

import argparse
import json
import sys

from . import diff as diff_mod
from . import query, store


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="runsnap", description="Record and compare what your code actually does.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("diff", help="list behavior that changed between two snapshots")
    d.add_argument("before")
    d.add_argument("after")
    d.add_argument("--rerun", help="a second recording of BEFORE; anything that differs there is ignored as unstable")
    d.add_argument("--json", action="store_true")
    d.add_argument("--fail", action="store_true", help="exit 1 when any behavior changed")
    d.add_argument("--html", metavar="PATH", help="also write a one-file HTML report you can open in a browser")

    c = sub.add_parser("calls", help="show recorded calls of a function")
    c.add_argument("snapshot")
    c.add_argument("function")
    c.add_argument("--test")
    c.add_argument("--limit", type=int, default=20)

    s = sub.add_parser("slowest", help="functions by total time")
    s.add_argument("snapshot")
    s.add_argument("--limit", type=int, default=10)

    sub.add_parser("list", help="list snapshots in .runsnap/")
    sub.add_parser("mcp", help="run the MCP server over stdio")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "diff":
            result = diff_mod.diff(args.before, args.after, args.rerun)
            print(json.dumps(result, ensure_ascii=False, indent=1) if args.json else diff_mod.format_text(result))
            if args.html:
                from . import report
                report.write(args.html, args.before, args.after, args.rerun)
                print(f"runsnap: report written to {args.html}", file=sys.stderr)
            return 1 if args.fail and result["changed"] else 0
        if args.cmd == "calls":
            for row in query.calls(args.snapshot, args.function, args.test, args.limit):
                outcome = f"raises {row['exc']}" if row["exc"] else f"-> {row['result']}"
                print(f"{row['test']}\n  {row['func']}({row['args']}) {outcome}")
            return 0
        if args.cmd == "slowest":
            for row in query.slowest(args.snapshot, args.limit):
                print(f"{row['total_ms']:>10.3f} ms  {row['calls']:>6} calls  {row['func']}")
            return 0
        if args.cmd == "list":
            for snap in store.list_snapshots():
                print(f"{snap['label']:<20} {snap['calls']:>8} calls  {snap['created']}  [{snap['targets']}]")
            return 0
        if args.cmd == "mcp":
            from .mcp_server import main as mcp_main
            mcp_main()
            return 0
    except FileNotFoundError as exc:
        print(f"runsnap: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
