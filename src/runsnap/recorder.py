"""Record what functions actually receive and return while code runs.

Uses sys.monitoring (Python 3.12+). Only code whose module name starts with
one of the target prefixes is recorded; every other code location is disabled
on first sight, so the cost outside the target modules is close to zero.
"""
from __future__ import annotations

import inspect
import re
import reprlib
import sys
import threading
import time
from dataclasses import dataclass, field

TOOL_ID = sys.monitoring.PROFILER_ID
_EVENTS = sys.monitoring.events
_SKIP_FLAGS = inspect.CO_GENERATOR | inspect.CO_COROUTINE | inspect.CO_ASYNC_GENERATOR
_ADDRESS = re.compile(r" at 0x[0-9a-fA-F]+")

_repr = reprlib.Repr()
_repr.maxstring = 200
_repr.maxother = 200
_repr.maxlist = _repr.maxtuple = _repr.maxdict = _repr.maxset = 20
_repr.maxlevel = 4


def safe_repr(value) -> str:
    """A short, stable text form of any value.

    Memory addresses in default reprs ("<Foo object at 0x7f...>") change on
    every run, so they are blanked out to keep snapshots comparable.
    """
    try:
        text = _repr.repr(value)
    except Exception as exc:  # a broken __repr__ must not break the test run
        text = f"<repr failed: {type(exc).__name__}>"
    return _ADDRESS.sub(" at 0x…", text)


@dataclass
class Call:
    test: str
    seq: int
    func: str
    depth: int
    args: str
    result: str | None = None
    exc: str | None = None
    duration_ns: int = 0


@dataclass
class Recorder:
    """Collects calls into `calls`. Start with `begin(test)`, stop with `end()`."""

    targets: tuple[str, ...]
    max_calls_per_test: int = 20_000
    calls: list[Call] = field(default_factory=list)
    truncated: set[str] = field(default_factory=set)

    def __post_init__(self):
        self._wanted: dict[object, str | None] = {}
        self._local = threading.local()
        self._test: str | None = None
        self._seq = 0
        self._active = False

    # which code objects to record ---------------------------------------
    def _name_for(self, code, frame) -> str | None:
        if code in self._wanted:
            return self._wanted[code]
        module = frame.f_globals.get("__name__", "")
        name = None
        if (
            not (code.co_flags & _SKIP_FLAGS)
            and not module.startswith("runsnap")
            and any(module == t or module.startswith(t + ".") for t in self.targets)
        ):
            name = f"{module}.{code.co_qualname}"
        self._wanted[code] = name
        return name

    def _stack(self) -> list:
        stack = getattr(self._local, "stack", None)
        if stack is None:
            stack = self._local.stack = []
        return stack

    # sys.monitoring callbacks --------------------------------------------
    def _on_start(self, code, offset):
        frame = sys._getframe(1)
        name = self._name_for(code, frame)
        if name is None:
            return sys.monitoring.DISABLE
        if self._test is None:
            return None
        stack = self._stack()
        if self._seq >= self.max_calls_per_test:
            self.truncated.add(self._test)
            stack.append(None)
            return None
        argnames = code.co_varnames[: code.co_argcount + code.co_kwonlyargcount
                                    + bool(code.co_flags & inspect.CO_VARARGS)
                                    + bool(code.co_flags & inspect.CO_VARKEYWORDS)]
        args = {n: safe_repr(frame.f_locals[n]) for n in argnames if n in frame.f_locals}
        if "self" in args:
            args["self"] = f"<{type(frame.f_locals['self']).__name__}>"
        call = Call(self._test, self._seq, name, len(stack), _format_args(args))
        self._seq += 1
        stack.append((call, time.perf_counter_ns()))
        return None

    def _finish(self, code, result=None, exc=None):
        if self._wanted.get(code) is None:
            return sys.monitoring.DISABLE
        stack = self._stack()
        if not stack:
            return None
        top = stack.pop()
        if top is None:
            return None
        call, started = top
        call.duration_ns = time.perf_counter_ns() - started
        if exc is not None:
            call.exc = f"{type(exc).__name__}: {safe_repr(str(exc))}"
        else:
            call.result = safe_repr(result)
        self.calls.append(call)
        return None

    def _on_return(self, code, offset, value):
        return self._finish(code, result=value)

    def _on_unwind(self, code, offset, exc):
        self._finish(code, exc=exc)  # PY_UNWIND cannot be disabled

    # control ---------------------------------------------------------------
    def install(self):
        mon = sys.monitoring
        mon.use_tool_id(TOOL_ID, "runsnap")
        mon.register_callback(TOOL_ID, _EVENTS.PY_START, self._on_start)
        mon.register_callback(TOOL_ID, _EVENTS.PY_RETURN, self._on_return)
        mon.register_callback(TOOL_ID, _EVENTS.PY_UNWIND, self._on_unwind)

    def uninstall(self):
        mon = sys.monitoring
        mon.set_events(TOOL_ID, 0)
        for event in (_EVENTS.PY_START, _EVENTS.PY_RETURN, _EVENTS.PY_UNWIND):
            mon.register_callback(TOOL_ID, event, None)
        mon.free_tool_id(TOOL_ID)

    def begin(self, test: str):
        self._test = test
        self._seq = 0
        self._local = threading.local()
        sys.monitoring.set_events(TOOL_ID, _EVENTS.PY_START | _EVENTS.PY_RETURN | _EVENTS.PY_UNWIND)

    def end(self):
        sys.monitoring.set_events(TOOL_ID, 0)
        self._test = None


def _format_args(args: dict[str, str]) -> str:
    return ", ".join(f"{k}={v}" for k, v in args.items())
