"""Tiny pytest plugin for the experiment: write the source lines executed during the run.

  LINECOV_ROOT=/path/to/package LINECOV_OUT=lines.json python -m pytest -p linecov ...

Uses sys.monitoring LINE events with the coverage tool id, so it can run next to runsnap.
"""
import json
import os
import sys

_mon = sys.monitoring
_TOOL = _mon.COVERAGE_ID
_root = os.path.realpath(os.environ.get("LINECOV_ROOT", "."))
_lines: dict[str, set[int]] = {}


def _on_line(code, line):
    path = os.path.realpath(code.co_filename)
    if not path.startswith(_root):
        return _mon.DISABLE
    _lines.setdefault(path, set()).add(line)
    return _mon.DISABLE  # each line only needs to be seen once


def pytest_configure(config):
    _mon.use_tool_id(_TOOL, "linecov")
    _mon.register_callback(_TOOL, _mon.events.LINE, _on_line)
    _mon.set_events(_TOOL, _mon.events.LINE)


def pytest_sessionfinish(session):
    _mon.set_events(_TOOL, 0)
    _mon.free_tool_id(_TOOL)
    with open(os.environ["LINECOV_OUT"], "w", encoding="utf-8") as f:
        json.dump({p: sorted(ls) for p, ls in _lines.items()}, f)
