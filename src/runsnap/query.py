"""Read-only questions about one snapshot, shared by the CLI and the MCP server."""
from __future__ import annotations

from . import store


def calls(label: str, func: str, test: str | None = None, limit: int = 20) -> list[dict]:
    """Recorded calls of a function. `func` matches the end of the full name,
    so "calc_tax" finds "shop.billing.calc_tax"."""
    db = store.open_snapshot(label)
    sql = "SELECT test, func, args, result, exc, duration_ns FROM calls WHERE (func = ? OR func LIKE ?)"
    params: list = [func, f"%.{func}"]
    if test:
        sql += " AND test LIKE ?"
        params.append(f"%{test}%")
    sql += " ORDER BY test, seq LIMIT ?"
    params.append(limit)
    return [dict(r) for r in db.execute(sql, params)]


def slowest(label: str, limit: int = 10) -> list[dict]:
    """Functions by total time, with call counts."""
    db = store.open_snapshot(label)
    rows = db.execute(
        "SELECT func, COUNT(*) AS calls, SUM(duration_ns) AS total_ns, MAX(duration_ns) AS max_ns "
        "FROM calls GROUP BY func ORDER BY total_ns DESC LIMIT ?",
        (limit,),
    )
    return [
        {"func": r["func"], "calls": r["calls"], "total_ms": round(r["total_ns"] / 1e6, 3), "max_ms": round(r["max_ns"] / 1e6, 3)}
        for r in rows
    ]


def tests_calling(label: str, func: str) -> list[str]:
    """Which tests reached a function at all."""
    db = store.open_snapshot(label)
    rows = db.execute(
        "SELECT DISTINCT test FROM calls WHERE func = ? OR func LIKE ? ORDER BY test", (func, f"%.{func}")
    )
    return [r["test"] for r in rows]
