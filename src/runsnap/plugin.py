"""pytest plugin: `pytest --runsnap=before --runsnap-module=mypkg` records a snapshot."""
from __future__ import annotations

import pytest

from . import store
from .recorder import Recorder


def pytest_addoption(parser):
    group = parser.getgroup("runsnap")
    group.addoption("--runsnap", metavar="LABEL", help="record a snapshot named LABEL into .runsnap/")
    group.addoption(
        "--runsnap-module", action="append", default=[], metavar="MODULE",
        help="module or package to record (repeatable); default: the runsnap_modules ini value",
    )
    parser.addini("runsnap_modules", "modules or packages to record", type="linelist", default=[])


class _Session:
    def __init__(self, label, targets):
        self.label = label
        self.recorder = Recorder(tuple(targets))

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item):
        self.recorder.begin(item.nodeid)
        try:
            yield
        finally:
            self.recorder.end()

    def pytest_sessionfinish(self, session):
        path = store.write(self.label, self.recorder.calls, self.recorder.targets, self.recorder.truncated)
        self.recorder.uninstall()
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter:
            reporter.write_line(f"runsnap: {len(self.recorder.calls)} calls recorded to {path}")


def pytest_configure(config):
    label = config.getoption("--runsnap")
    if not label:
        return
    targets = config.getoption("--runsnap-module") or config.getini("runsnap_modules")
    if not targets:
        raise pytest.UsageError("runsnap: say which code to record with --runsnap-module=PACKAGE")
    session = _Session(label, targets)
    session.recorder.install()
    config.pluginmanager.register(session, "runsnap-session")
