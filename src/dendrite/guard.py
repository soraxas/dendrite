"""Best-effort tripwire for hidden inputs: files a step reads without declaring them.

A step's outputs should depend only on what its signature hashes (code, params, artifact
inputs, env). Reading a project file that was not passed in as an artifact breaks that: the
file can change without invalidating the cache. While a step function runs, an audit hook
watches Python-level opens for reading and reports project files outside the allowed places
(its workdir, artifact checkouts, ``DENDRITE_HOME``, the interpreter's prefixes).

It is a tripwire, not a sandbox: C extensions that call ``fopen``, subprocesses and threads the
step starts are not seen, and reads outside the project directory are not reported.
"""

from __future__ import annotations

import os
import sys
import threading
import warnings
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path


class UndeclaredReadWarning(UserWarning):
    """A step read a project file that is not one of its declared inputs."""


class UndeclaredRead(RuntimeError):
    """Raised instead of the warning when ``DENDRITE_CHECK_READS=error``."""


_CODE_SUFFIXES = (".py", ".pyc", ".pyi", ".so", ".pyd", ".pth", ".dylib")
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


@dataclass
class ReadGuard:
    step: str
    mode: str  # "warn" | "error"
    root: str
    allowed: tuple[str, ...]
    seen: set[str] = field(default_factory=set)
    violation: str | None = None
    busy: bool = False  # the hook's own work (realpath, warnings' linecache) must not recurse

    def check(self, path: str) -> None:
        if not _under(path, self.root) or any(_under(path, a) for a in self.allowed):
            return
        if path.endswith(_CODE_SUFFIXES) or path in self.seen:
            return
        self.seen.add(path)
        rel = os.path.relpath(path, self.root)
        msg = (
            f"step {self.step!r} read {rel}, which is not one of its declared inputs; the cache can't "
            f"see changes to it. Pass it in as an artifact (lab.ingest(...) or an experiment input)."
        )
        if self.mode == "error":
            self.violation = msg
            raise UndeclaredRead(msg)
        warnings.warn(msg, UndeclaredReadWarning, stacklevel=3)


def _under(path: str, base: str) -> bool:
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


_active: ContextVar[ReadGuard | None] = ContextVar("dendrite_read_guard", default=None)
_installed = False
_install_lock = threading.Lock()


def _hook(event: str, args: tuple) -> None:
    if event != "open":
        return
    g = _active.get()
    if g is None or g.busy:
        return
    path, mode, flags = args
    if not isinstance(path, (str, bytes, os.PathLike)):
        return  # an fd
    if mode is None:  # os.open
        if flags & _WRITE_FLAGS:
            return
    elif any(c in mode for c in "wax+"):
        return
    g.busy = True
    try:
        g.check(os.path.realpath(os.fsdecode(path)))
    finally:
        g.busy = False


def _install() -> None:
    global _installed
    with _install_lock:
        if not _installed:
            sys.addaudithook(_hook)  # process-wide and permanent; idle unless a guard is active
            _installed = True


def start(step: str, mode: str, root: Path, allowed: list[Path]) -> object | None:
    """Watch reads for the rest of this context; returns a token for :func:`stop`."""
    if mode == "off":
        return None
    _install()
    prefixes = {sys.prefix, sys.base_prefix, sys.exec_prefix}
    real = lambda p: os.path.realpath(os.fspath(p))  # noqa: E731
    guard = ReadGuard(step, mode, real(root), tuple(real(p) for p in [*allowed, *prefixes]))
    return _active.set(guard)


def stop(token: object | None) -> ReadGuard | None:
    if token is None:
        return None
    guard = _active.get()
    _active.reset(token)  # type: ignore[arg-type]
    return guard
