"""Per-run and per-execution context shared by pipelines and artifact tasks."""

from __future__ import annotations

import functools
import importlib
import os
import sys
import threading
import time
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
    # Plan mode: steps stop after the cache check and append their predicted outcome here.
    plan: bool = False
    planned: list[dict[str, Any]] = field(default_factory=list)
    refs: dict[str, Any] = field(default_factory=dict)  # hash -> ArtifactRef seen while planning
    parent_runs: dict[str, dict[str, Any]] = field(default_factory=dict)  # node -> parent's node_run
    parent_params: dict[str, dict[str, Any]] = field(default_factory=dict)
    reads: list[dict[str, Any]] = field(default_factory=list)  # artifacts the body itself loaded
    # id(future) -> (node, outputs) for submitted steps, so a queued step's inputs can name the
    # step they will come from before that step has produced anything.
    future_nodes: dict[int, tuple[str, tuple[str, ...]]] = field(default_factory=dict)

    @property
    def lab(self) -> "Lab":
        return self.experiment.lab

    def claim(self, node: str) -> None:
        with self.lock:
            if node in self.called:
                raise RuntimeError(
                    f"node {node!r} was called twice in one pipeline run; give each call its own node "
                    f"with {node}.named(key)"
                )
            self.called.add(node)


@dataclass
class ExecContext:
    workdir: Path
    execution_id: str
    metrics: list[tuple[str, int, float]] = field(default_factory=list)
    db: Any = None
    last_progress: float = 0.0


# Prefect may run tasks on worker threads, so runs are looked up by id rather than via a
# context variable that might not propagate.
_RUNS: dict[str, RunState] = {}
_active_run: ContextVar[str | None] = ContextVar("dendrite_active_run", default=None)
_exec: ContextVar[ExecContext | None] = ContextVar("dendrite_exec", default=None)


# Modules that define pipelines/steps, with the source mtime they were imported at. A long-lived
# process (the UI server) reloads them before planning so code fingerprints match a new run.
_MODULES: dict[str, float] = {}
_PIPELINE_MODULES: set[str] = set()
_RELOAD_LOCK = threading.Lock()  # the UI server plans on worker threads


def _mtime(name: str) -> float | None:
    file = getattr(sys.modules.get(name), "__file__", None)
    try:
        return os.stat(file).st_mtime if file else None
    except OSError:
        return None


def track_module(name: str, pipeline: bool = False) -> None:
    if name == "__main__":
        return  # can't be reloaded; scripts keep the code they started with
    if name not in _MODULES:
        t = _mtime(name)
        if t is not None:
            _MODULES[name] = t
    if pipeline:
        _PIPELINE_MODULES.add(name)


def refresh_modules() -> str | None:
    """Reload tracked modules whose source changed (then every pipeline module, since they
    hold references to step objects). Returns an error message if a reload failed."""
    with _RELOAD_LOCK:
        return _refresh()


def _refresh() -> str | None:
    changed = [m for m, t in _MODULES.items() if m in sys.modules and _mtime(m) not in (None, t)]
    if not changed:
        return None
    order = [m for m in changed if m not in _PIPELINE_MODULES] + sorted(_PIPELINE_MODULES & set(sys.modules))
    for name in dict.fromkeys(order):
        try:
            importlib.reload(sys.modules[name])
        except Exception as e:  # keep whatever was loaded; retried on the next plan
            return f"reloading {name} failed: {type(e).__name__}: {e}"
        _MODULES[name] = _mtime(name) or _MODULES[name]
    return None


def register(experiment: "Experiment", plan: bool = False) -> RunState:
    state = RunState(run_id=uuid.uuid4().hex, experiment=experiment, plan=plan)
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


PROGRESS_INTERVAL = 0.25  # seconds between progress writes; the final one is always written


def progress(done: float, total: float | None = None, unit: str | None = None) -> None:
    """Report how far the current step has got, e.g. ``progress(40, 85, "frames")``.

    The UI shows it live on the step's card, with time remaining. Writes are throttled, so it
    is fine to call per item. Progress is display only and never part of a signature.
    """
    ctx = _exec.get()
    if ctx is None:
        raise RuntimeError("progress() is only available inside an executing artifact task")
    now = time.monotonic()
    final = total is not None and done >= total
    if ctx.db is None or (not final and now - ctx.last_progress < PROGRESS_INTERVAL):
        return
    ctx.last_progress = now
    ctx.db.set_progress(ctx.execution_id, float(done), None if total is None else float(total), unit)


_stage: ContextVar[tuple[dict[str, Any], ...]] = ContextVar("dendrite_stage", default=())


class stage:
    """A named phase of a pipeline (``Privacy``, ``COLMAP``…), for the graph's overview.

    Use it as a decorator on a function that calls steps, or as a ``with`` block in a pipeline
    body; every step called within belongs to the stage. Stages nest, and are display-only:
    they never change a step's signature.

        @dendrite.stage("Privacy", "Sanitised media", color="teal")
        def privacy(video):
            return encode(blur(extract(video)), video)
    """

    def __init__(self, name: str, description: str | None = None, *, color: str | None = None):
        self.info = {"name": name, "description": description, "color": color}
        self._tokens: list[Any] = []

    def __enter__(self) -> "stage":
        self._tokens.append(_stage.set(_stage.get() + (self.info,)))
        return self

    def __exit__(self, *exc: Any) -> None:
        _stage.reset(self._tokens.pop())

    def __call__(self, fn: Any) -> Any:
        info = self.info

        @functools.wraps(fn)
        def staged(*args: Any, **kwargs: Any) -> Any:
            with stage(info["name"], info["description"], color=info["color"]):
                return fn(*args, **kwargs)

        staged.__dendrite_stage__ = info  # read by the static shape (analysis.py)
        return staged


def current_stage() -> list[dict[str, Any]]:
    """The stage path (outermost first) of a step called now."""
    return list(_stage.get())


def note_read(ref: Any) -> None:
    """Record that the pipeline body (not a step) read ``ref``, e.g. a config or a fan-out plan."""
    run_id = _active_run.get()
    if run_id is None or _exec.get() is not None or run_id not in _RUNS:
        return
    state = _RUNS[run_id]
    with state.lock:
        entry = {"artifact": ref.hash, "after": len(state.called)}
        if any(r["artifact"] == ref.hash for r in state.reads):
            return
        seq = len(state.reads)
        state.reads.append(entry)
    if not state.plan:
        state.lab.db.record_read(state.experiment.id, seq, ref.hash, entry["after"])


def load(ref: Any) -> Any:
    """Read an artifact inside a pipeline body, e.g. a plan whose contents decide a fan-out.

    Same as ``lab.load``; the read is recorded so the graph can show what it decided.
    """
    return active().lab.load(ref)


def log_metric(key: str, value: float, step: int = 0) -> None:
    """Record a metric on the current execution (shared by every experiment that reuses it)."""
    ctx = _exec.get()
    if ctx is None:
        raise RuntimeError("log_metric() is only available inside an executing artifact task")
    ctx.metrics.append((key, int(step), float(value)))
