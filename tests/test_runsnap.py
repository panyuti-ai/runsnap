import json
import os

import pytest

from runsnap import cli, query, store
from runsnap.diff import diff
from runsnap.recorder import safe_repr

SHOP_V1 = '''
import random, time

def calc_tax(amount, rate=0.05):
    return round(amount * rate, 2)

def total(items):
    return round(sum(items) + calc_tax(sum(items)), 2)

def checkout(items):
    if not items:
        raise ValueError("empty cart")
    return {"total": total(items), "at": time.time(), "lucky": random.random()}

def numbers(n):
    for i in range(n):
        yield i

def firsts(n):
    for i in range(n):
        yield i
    return "done"

class Cart:
    def __init__(self):
        self.items = []
    def add(self, price):
        self.items.append(price)
        return len(self.items)
'''

TESTS = '''
import pytest, shop

def test_checkout():
    assert shop.checkout([100, 50])["total"] > 0

def test_empty():
    with pytest.raises(ValueError):
        shop.checkout([])

def test_numbers():
    assert len(list(shop.numbers(3))) == 3   # a loose test: checks the count, not the values
    g = shop.firsts(5)
    assert next(g) == 0

def test_cart():
    c = shop.Cart()
    assert c.add(10) == 1
'''


def record(pytester, label):
    result = pytester.runpytest_subprocess(f"--runsnap={label}", "--runsnap-module=shop", "-p", "no:cacheprovider")
    result.assert_outcomes(passed=4)
    return result


@pytest.fixture
def project(pytester, monkeypatch):
    pytester.makepyfile(shop=SHOP_V1, test_shop=TESTS)
    monkeypatch.chdir(pytester.path)
    # Tests rewrite shop.py within the same second and at the same size, which
    # would let Python reuse stale bytecode; never write it.
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    return pytester


def test_records_args_results_and_exceptions(project):
    out = record(project, "before")
    out.stdout.fnmatch_lines(["runsnap: * calls recorded to *before.sqlite"])
    rows = query.calls("before", "calc_tax")
    assert rows[0]["args"] == "amount=150, rate=0.05"
    assert rows[0]["result"] == "7.5"
    empty = query.calls("before", "checkout", test="test_empty")
    assert empty[0]["exc"] == "ValueError: 'empty cart'"


def test_records_generators_and_marks_self(project):
    record(project, "before")
    assert query.calls("before", "numbers")[0]["result"] == "yields [0, 1, 2]"
    add = query.calls("before", "Cart.add")
    assert add[0]["args"] == "self=<Cart>, price=10"


def test_diff_finds_changed_behavior_and_ignores_unstable(project):
    record(project, "before")
    record(project, "rerun")
    # An "AI refactor" that silently changes the tax rule.
    project.makepyfile(shop=SHOP_V1.replace("round(amount * rate, 2)", "round(amount * rate + 0.5, 2)"))
    record(project, "after")

    noisy = diff("before", "after")
    assert any(c["func"] == "shop.checkout" for c in noisy["changed"])  # time/random differ every run

    result = diff("before", "after", rerun="rerun")
    changed = {(c["func"], c["before"], c["after"]) for c in result["changed"]}
    assert ("shop.calc_tax", "returns 7.5", "returns 8.0") in changed
    assert ("shop.total", "returns 157.5", "returns 158.0") in changed
    assert all(c["func"] != "shop.checkout" for c in result["changed"])
    assert result["ignored_unstable"] >= 1


def test_no_change_means_empty_diff(project):
    record(project, "a")
    record(project, "b")
    result = diff("a", "b", rerun="b")
    assert result["changed"] == [] and result["gone"] == [] and result["new"] == []


def test_cli_diff_fail_and_json(project, capsys):
    record(project, "before")
    project.makepyfile(shop=SHOP_V1.replace("rate=0.05", "rate=0.06"))
    record(project, "after")
    assert cli.main(["diff", "before", "after", "--fail"]) == 1
    capsys.readouterr()
    cli.main(["diff", "before", "after", "--json"])
    data = json.loads(capsys.readouterr().out)
    # total() gets the same items but returns a different sum.
    assert any(c["func"] == "shop.total" for c in data["changed"])
    # calc_tax now receives a different default rate, so its old and new calls
    # have different arguments: they show up as gone + new, not as changed.
    assert any(c["func"] == "shop.calc_tax" for c in data["gone"])
    assert any(c["func"] == "shop.calc_tax" for c in data["new"])
    assert cli.main(["diff", "before", "missing"]) == 2


def test_slowest_and_list(project):
    record(project, "before")
    funcs = {r["func"] for r in query.slowest("before")}
    assert "shop.checkout" in funcs
    assert [s["label"] for s in store.list_snapshots()] == ["before"]


def test_requires_module(project):
    result = project.runpytest_subprocess("--runsnap=x")
    result.stderr.fnmatch_lines(["*--runsnap-module*"])


def test_safe_repr_hides_addresses_and_truncates():
    class Thing:
        pass
    assert "0x…" in safe_repr(Thing())
    assert len(safe_repr("x" * 10_000)) < 300

    class Broken:
        def __repr__(self):
            raise RuntimeError("no")
    # reprlib falls back to a generic form when __repr__ raises; it must stay stable.
    assert safe_repr(Broken()) == "<Broken instance at 0x…>"


def test_mcp_tools(project):
    pytest.importorskip("mcp")
    from runsnap import mcp_server

    record(project, "before")
    project.makepyfile(shop=SHOP_V1.replace("rate=0.05", "rate=0.06"))
    record(project, "after")
    assert mcp_server.list_snapshots()[0]["label"] == "after"
    assert mcp_server.function_calls("before", "calc_tax")[0]["result"] == "7.5"
    d = mcp_server.behavior_diff("before", "after", limit=1)
    assert d["changed_total"] >= 1 and len(d["changed"]) == 1
    assert "test_shop.py::test_checkout" in mcp_server.tests_reaching("before", "calc_tax")


def test_generator_change_and_unfinished(project):
    record(project, "before")
    rows = query.calls("before", "firsts")
    assert rows[0]["result"] == "yields [0] (closed before the end)"
    project.makepyfile(shop=SHOP_V1.replace("    for i in range(n):\n        yield i\n\ndef firsts", "    for i in range(n):\n        yield i * 1 if i < 2 else i + 1\n\ndef firsts"))
    record(project, "after")
    changed = {(c["func"], c["before"], c["after"]) for c in diff("before", "after")["changed"]}
    assert ("shop.numbers", "returns yields [0, 1, 2]", "returns yields [0, 1, 3]") in changed
