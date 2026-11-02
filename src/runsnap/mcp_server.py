"""MCP server so Claude Code, Codex and other agents can ask what the code actually did.

Add to Claude Code:   claude mcp add runsnap -- runsnap mcp
Add to Codex:         codex mcp add runsnap -- runsnap mcp
Snapshots are read from .runsnap/ in the working directory (or RUNSNAP_DIR).
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from . import diff as diff_mod
from . import query, store

server = MCPServer(
    name="runsnap",
    instructions=(
        "Recorded test runs of this project: the real arguments, return values and exceptions of "
        "functions, captured while the tests ran. Use list_snapshots first. Use behavior_diff after "
        "changing code to see which function results changed between two recordings. Use "
        "function_calls instead of guessing what values a function receives."
    ),
)


@server.tool()
def list_snapshots() -> list[dict]:
    """List recorded snapshots with their call counts."""
    return store.list_snapshots()


@server.tool()
def function_calls(snapshot: str, function: str, test: str | None = None, limit: int = 20) -> list[dict]:
    """Real calls of a function recorded in a snapshot: arguments, return value or exception.
    `function` can be the short name (calc_tax) or the full one (shop.billing.calc_tax)."""
    return query.calls(snapshot, function, test, limit)


@server.tool()
def behavior_diff(before: str, after: str, rerun: str | None = None, limit: int = 50) -> dict:
    """Function results that changed between two snapshots. Pass `rerun` (a second recording
    of `before`) to drop values that change on every run, like timestamps."""
    result = diff_mod.diff(before, after, rerun)
    for kind in ("changed", "gone", "new"):
        result[f"{kind}_total"] = len(result[kind])
        result[kind] = result[kind][:limit]
    return result


@server.tool()
def slowest_functions(snapshot: str, limit: int = 10) -> list[dict]:
    """Functions that took the most total time in a snapshot."""
    return query.slowest(snapshot, limit)


@server.tool()
def tests_reaching(snapshot: str, function: str) -> list[str]:
    """Tests that called a function at least once."""
    return query.tests_calling(snapshot, function)


def main():
    server.run("stdio")
