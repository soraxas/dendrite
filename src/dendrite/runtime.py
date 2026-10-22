"""Per-run and per-execution context shared by pipelines and artifact tasks."""

from __future__ import annotations

import threading
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .experiment import Experiment, Lab


@dataclass
class RunState:
    run_id: str
    experiment: "Experiment"
    called: set[str] = field(default_factory=set)
    futures: list[Any] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def lab(self) -> "Lab":
        return self.experiment.lab

    def claim(self, node: str) -> None:
        with self.lock:
            if node in self.called:
                raise RuntimeError(
                    f"node {node!r} was called twice in one pipeline run; give the second call a "
                    f"distinct name with {node}.with_options(name=...)"
                )
            self.called.add(node)


@dataclass
class ExecContext:
    workdir: Path
    execution_id: str
    metrics: list[tuple[str, int, float]] = field(default_factory=list)


# Prefect may run tasks on worker threads, so runs are looked up by id rather than via a
# context variable that might not propagate.
_RUNS: dict[str, RunState] = {}
_active_run: ContextVar[str | None] = ContextVar("dendrite_active_run", default=None)
_exec: ContextVar[ExecContext | None] = ContextVar("dendrite_exec", default=None)


def register(experiment: "Experiment") -> RunState:
    state = RunState(run_id=uuid.uuid4().hex, experiment=experiment)
    _RUNS[state.run_id] = state
    return state


def unregister(run_id: str) -> None:
    _RUNS.pop(run_id, None)


def get(run_id: str) -> RunState:
    try:
        return _RUNS[run_id]
    except KeyError:
        raise RuntimeError(f"no active dendrite run {run_id!r}") from None


def active() -> RunState:
    run_id = _active_run.get()
    if run_id is None:
        raise RuntimeError(
            "artifact tasks must be called inside a @dendrite.pipeline that is being run via "
            "Experiment.run(); call the undecorated function with `.fn(...)` for ad-hoc use"
        )
    return get(run_id)


def workdir() -> Path:
    """Scratch directory for the currently executing node; outputs may be written here."""
    ctx = _exec.get()
    if ctx is None:
        raise RuntimeError("workdir() is only available inside an executing artifact task")
    return ctx.workdir


def log_metric(key: str, value: float, step: int = 0) -> None:
    """Record a metric on the current execution (shared by every experiment that reuses it)."""
    ctx = _exec.get()
    if ctx is None:
        raise RuntimeError("log_metric() is only available inside an executing artifact task")
    ctx.metrics.append((key, int(step), float(value)))
