"""``@artifact_task``: a lineage-aware wrapper around a Prefect task.

Prefect executes the node; dendrite decides whether it needs to. Each call:

1. splits arguments into artifact inputs (``ArtifactRef``\\ s, possibly nested in lists /
   dicts) and parameters, overriding parameters from the experiment's ``params[node]``;
2. computes the execution signature ``H(op, version, code, env, input hashes, params)``;
3. reuses any verified successful execution with that signature, from any experiment;
4. otherwise takes a lease (so concurrent forks don't duplicate work), executes, stores the
   outputs and publishes lineage atomically.
"""

from __future__ import annotations

import functools
import importlib.metadata
import inspect
import os
import shutil
import sys
import textwrap
import threading
import time
import traceback
import uuid
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from prefect import get_run_logger, task
from prefect.cache_policies import NO_CACHE

from . import guard, runtime
from .hashing import canonical_json, sha256_bytes, sha256_json
from .store import PENDING, ArtifactRef

POLL_INTERVAL = 0.2


def code_fingerprint(fn: Callable) -> str:
    try:
        src = textwrap.dedent(inspect.getsource(fn))
    except (OSError, TypeError):
        code = fn.__code__
        src = code.co_code.hex() + repr(code.co_consts) + repr(code.co_names)
    return sha256_bytes(src.encode())


def source_snapshot(fn: Callable) -> dict[str, Any] | None:
    """Source text and location of ``fn`` for display and editing (not part of the signature)."""
    try:
        lines, line = inspect.getsourcelines(fn)
    except (OSError, TypeError):
        return None
    source = "".join(lines)
    try:
        file = inspect.getsourcefile(fn)
        file = str(Path(file).resolve()) if file else None
    except TypeError:
        file = None
    return {
        "hash": sha256_bytes(source.encode()),
        "source": source,
        "file": file,
        "line": line,
        "module": fn.__module__,
        "qualname": fn.__qualname__,
    }


def env_fingerprint(packages: Iterable[str]) -> dict[str, Any]:
    versions = {}
    for pkg in sorted(packages):
        try:
            versions[pkg] = importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:
            versions[pkg] = None
    return {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "env": os.environ.get("DENDRITE_ENV"),
        "packages": versions,
    }


def _has_ref(value: Any) -> bool:
    if isinstance(value, ArtifactRef):
        return True
    if isinstance(value, (list, tuple)):
        return any(_has_ref(v) for v in value)
    if isinstance(value, dict):
        return any(_has_ref(v) for v in value.values())
    return False


def _map_refs(value: Any, fn: Callable[[ArtifactRef], Any]) -> Any:
    if isinstance(value, ArtifactRef):
        return fn(value)
    if isinstance(value, (list, tuple)):
        return type(value)(_map_refs(v, fn) for v in value)
    if isinstance(value, dict):
        return {k: _map_refs(v, fn) for k, v in value.items()}
    return value


class PlanFuture:
    """Plan-mode stand-in for the PrefectFuture that ``.submit()`` returns."""

    def __init__(self, value: Any):
        self._value = value

    def result(self, *args: Any, **kwargs: Any) -> Any:
        return self._value

    def wait(self, *args: Any, **kwargs: Any) -> None:
        return None


def _unwrap_plan(value: Any) -> Any:
    # Prefect resolves futures passed to tasks; plan mode has to do it itself.
    if isinstance(value, PlanFuture):
        return value.result()
    if isinstance(value, (list, tuple)):
        return type(value)(_unwrap_plan(v) for v in value)
    if isinstance(value, dict):
        return {k: _unwrap_plan(v) for k, v in value.items()}
    return value


def placeholder(node: str, output: str) -> ArtifactRef:
    return ArtifactRef(f"plan:{node}.{output}", PENDING, 0)


_JSON_TYPES = {bool: "boolean", int: "integer", float: "number", str: "string", list: "array", tuple: "array", dict: "object"}


def _json_type(annotation: Any, value: Any) -> str:
    """Editor type for a param: from a plain annotation if there is one, else from its value."""
    names = {"bool": "boolean", "int": "integer", "float": "number", "str": "string", "list": "array", "dict": "object"}
    if isinstance(annotation, str) and annotation in names:  # `from __future__ import annotations`
        return names[annotation]
    if annotation in _JSON_TYPES:
        return _JSON_TYPES[annotation]
    if value is None:
        return "any"
    return _JSON_TYPES.get(type(value), "any")


def _annotation_text(annotation: Any) -> str | None:
    if annotation is inspect.Parameter.empty:
        return None
    if isinstance(annotation, str):
        return annotation
    return getattr(annotation, "__name__", None) or repr(annotation)


def _flatten_refs(name: str, value: Any, out: dict[str, ArtifactRef]) -> None:
    if isinstance(value, ArtifactRef):
        out[name] = value
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _flatten_refs(f"{name}[{i}]", v, out)
    elif isinstance(value, dict):
        for k, v in value.items():
            _flatten_refs(f"{name}.{k}", v, out)


class _Heartbeat:
    def __init__(self, db, signature: str, owner: str, ttl: float):
        self.db, self.signature, self.owner, self.ttl = db, signature, owner, ttl
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._beat, daemon=True)

    def _beat(self) -> None:
        while not self._stop.wait(self.ttl / 3):
            self.db.renew_lease(self.signature, self.owner, self.ttl)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()


class ArtifactTask:
    def __init__(
        self,
        fn: Callable,
        *,
        outputs: Sequence[str] | None = None,
        name: str | None = None,
        version: str = "1",
        cache: bool = True,
        packages: Sequence[str] = (),
        hash_source: bool = True,
        lease_ttl: float = 300.0,
        **task_options: Any,
    ):
        self.fn = fn
        self.name = name or fn.__name__
        self.outputs = tuple(outputs) if outputs else ("output",)
        if len(set(self.outputs)) != len(self.outputs):
            raise ValueError(f"duplicate output names in {self.outputs}")
        self.version = str(version)
        self.cache = cache
        self.packages = tuple(packages)
        self.hash_source = hash_source
        self.lease_ttl = lease_ttl
        self.signature = inspect.signature(fn)
        for p in self.signature.parameters.values():
            if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                raise TypeError(f"{self.name}: *args/**kwargs are not supported in artifact tasks")
        self.code = code_fingerprint(fn) if hash_source else None
        self.source = source_snapshot(fn)
        self._task_options = task_options
        functools.update_wrapper(self, fn)
        runtime.track_module(fn.__module__)

        node = self

        def run_node(run_id: str, call: dict[str, Any]) -> Any:
            return node._execute(run_id, call)

        run_node.__name__ = run_node.__qualname__ = self.name
        run_node.__module__ = fn.__module__
        self._prefect_task = task(
            run_node, name=self.name, task_run_name=self.name, cache_policy=NO_CACHE, **task_options
        )

    def __repr__(self) -> str:
        return f"<artifact_task {self.name} outputs={list(self.outputs)} v{self.version}>"

    def with_options(self, **overrides: Any) -> "ArtifactTask":
        opts = dict(
            outputs=self.outputs,
            name=self.name,
            version=self.version,
            cache=self.cache,
            packages=self.packages,
            hash_source=self.hash_source,
            lease_ttl=self.lease_ttl,
            **self._task_options,
        )
        opts.update(overrides)
        return ArtifactTask(self.fn, **opts)

    # -- calling from a pipeline ---------------------------------------------------
    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        state = runtime.active()
        if state.plan:
            return self._plan(state, args, kwargs)
        return self._prefect_task(**self._enqueue(args, kwargs))

    def submit(self, *args: Any, **kwargs: Any):
        state = runtime.active()
        if state.plan:
            return PlanFuture(self._plan(state, args, kwargs))
        future = self._prefect_task.submit(**self._enqueue(args, kwargs))
        state.futures.append(future)
        return future

    def _enqueue(self, args: tuple, kwargs: dict) -> dict[str, Any]:
        state = runtime.active()
        bound = self.signature.bind_partial(*args, **kwargs)
        state.claim(self.name)
        known: dict[str, ArtifactRef] = {}
        for k, v in bound.arguments.items():
            _flatten_refs(k, v, known)  # futures are resolved later; record what we know
        state.lab.db.upsert_node_run(
            state.experiment.id,
            self.name,
            status="queued",
            queued_at=time.time(),
            inputs={k: r.hash for k, r in known.items()},
            started_at=None,
            finished_at=None,
            error=None,
        )
        return {"run_id": state.run_id, "call": dict(bound.arguments)}

    # -- planning ------------------------------------------------------------------
    def _plan(self, state: runtime.RunState, args: tuple, kwargs: dict) -> Any:
        """Predict this step's outcome without executing it or writing anything.

        Mirrors :meth:`_execute` up to the cache check, using the same ``_split``/``spec``
        so the predicted signature is the one a run would compute. A step that would run
        returns placeholders, which makes everything downstream "run (upstream)".
        """
        lab, exp = state.lab, state.experiment
        db = lab.db
        bound = self.signature.bind_partial(*args, **kwargs)
        call = {k: _unwrap_plan(v) for k, v in bound.arguments.items()}
        state.claim(self.name)
        entry: dict[str, Any] = {"node": self.name, "seq": len(state.planned), "signature": None, "reason": None,
                                 "reuse_from": None, "estimate": None, "param_schema": []}
        state.planned.append(entry)

        pinned = exp.pins.get(self.name)
        if pinned is not None:
            producer = db.producer_of(next(iter(pinned.values())))
            ex = db.execution(producer) if producer else None
            known: dict[str, ArtifactRef] = {}
            for k, v in call.items():  # keep its incoming edges, as a real pinned run does
                _flatten_refs(k, v, known)
            entry.update(plan="frozen", inputs={k: r.hash for k, r in known.items()}, params={},
                         outputs=dict(pinned), reuse_from=_origin(ex))
            state.refs.update({r.hash: r for r in known.values()})
            return self._shape({o: db.artifact(pinned[o]) for o in self.outputs})

        structure, flat, params = self._split(call, exp.params.get(self.name, {}))
        entry.update(inputs={k: r.hash for k, r in flat.items()}, params=params,
                     param_schema=self._param_schema(call, params, state))
        parent_run = state.parent_runs.get(self.name)
        parent_ex = db.execution(parent_run["execution_id"]) if parent_run and parent_run.get("execution_id") else None
        if any(r.kind == PENDING for r in flat.values()):
            refs = {o: placeholder(self.name, o) for o in self.outputs}
            entry.update(plan="run", reason="upstream", estimate=_duration(parent_ex) or _duration(db.latest_success_for_node(self.name)))
        else:
            spec = self.spec(structure, params)
            signature = sha256_json(spec)
            entry["signature"] = signature
            hit = self._valid_hit(lab, signature) if self.cache else None
            if hit:
                row, refs = hit
                entry.update(plan="reuse", reuse_from=_origin(row), estimate=_duration(row))
            else:
                refs = {o: placeholder(self.name, o) for o in self.outputs}
                prior = db.find_success(signature) if self.cache else []
                if not self.cache:
                    reason = "uncached"
                elif prior:
                    reason = "missing_blob"
                else:
                    reason = _reason(spec, parent_ex["spec"] if parent_ex else None)
                same = prior[0][0] if prior else None
                entry.update(plan="run", reason=reason,
                             estimate=_duration(same) or _duration(parent_ex) or _duration(db.latest_success_for_node(self.name)))
        entry["outputs"] = {o: r.hash for o, r in refs.items()}
        state.refs.update({r.hash: r for r in list(flat.values()) + list(refs.values())})
        return self._shape(refs)

    def _param_schema(self, call: dict[str, Any], params: dict[str, Any], state: runtime.RunState) -> list[dict]:
        overrides = state.experiment.params.get(self.name, {})
        inherited_over = state.parent_params.get(self.name, {})
        out = []
        for pname, value in params.items():
            p = self.signature.parameters[pname]
            default = None if p.default is inspect.Parameter.empty else p.default
            base = call.get(pname, default)  # what the value is without any experiment override
            inherited = inherited_over.get(pname, base)
            out.append({
                "name": pname,
                "value": value,
                "default": default,
                "inherited": inherited,
                "overridden": pname in overrides,
                "changed": canonical_json(value) != canonical_json(inherited),
                "annotation": _annotation_text(p.annotation),
                "json_type": _json_type(p.annotation, default if default is not None else value),
            })
        return out

    # -- execution -----------------------------------------------------------------
    def _split(self, call: dict[str, Any], node_params: dict[str, Any]):
        unknown = set(node_params) - set(self.signature.parameters)
        if unknown:
            raise ValueError(f"experiment params for node {self.name!r} have unknown keys: {sorted(unknown)}")
        structure: dict[str, Any] = {}
        flat: dict[str, ArtifactRef] = {}
        params: dict[str, Any] = {}
        for pname, p in self.signature.parameters.items():
            if pname in call and _has_ref(call[pname]):
                if pname in node_params:
                    raise ValueError(f"{self.name}.{pname} is an artifact input and cannot be set via params")
                structure[pname] = _map_refs(call[pname], lambda r: {"$artifact": r.hash})
                _flatten_refs(pname, call[pname], flat)
            elif pname in node_params:
                params[pname] = node_params[pname]
            elif pname in call:
                params[pname] = call[pname]
            elif p.default is not inspect.Parameter.empty:
                params[pname] = p.default
            else:
                raise TypeError(f"{self.name}() missing required argument {pname!r}")
        return structure, flat, params

    def spec(self, structure: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        return {
            "op": self.name,
            "version": self.version,
            "code": self.code,
            "env": env_fingerprint(self.packages),
            "inputs": structure,
            "params": params,
            "outputs": list(self.outputs),
        }

    def _shape(self, refs: dict[str, ArtifactRef]) -> Any:
        if len(self.outputs) == 1:
            return refs[self.outputs[0]]
        return tuple(refs[o] for o in self.outputs)

    def _normalise(self, result: Any) -> dict[str, Any]:
        if len(self.outputs) == 1 and not (isinstance(result, dict) and set(result) == set(self.outputs)):
            return {self.outputs[0]: result}
        if isinstance(result, dict):
            if set(result) != set(self.outputs):
                raise ValueError(f"{self.name} returned keys {sorted(result)}, expected {list(self.outputs)}")
            return {o: result[o] for o in self.outputs}
        if isinstance(result, tuple) and len(result) == len(self.outputs):
            return dict(zip(self.outputs, result))
        raise ValueError(f"{self.name} must return a dict or {len(self.outputs)}-tuple for outputs {self.outputs}")

    def _valid_hit(self, lab, signature: str):
        for row, refs in lab.db.find_success(signature):
            if set(refs) == set(self.outputs) and all(lab.store.verify(r) for r in refs.values()):
                return row, refs
        return None

    def _execute(self, run_id: str, call: dict[str, Any]) -> Any:
        state = runtime.get(run_id)
        lab, exp = state.lab, state.experiment
        db = lab.db
        logger = get_run_logger()

        pinned = exp.pins.get(self.name)
        if pinned is not None:
            if set(pinned) != set(self.outputs):
                raise ValueError(f"pin for {self.name!r} must cover outputs {list(self.outputs)}, got {sorted(pinned)}")
            refs = {o: db.artifact(pinned[o]) for o in self.outputs}
            now = time.time()
            db.upsert_node_run(
                exp.id,
                self.name,
                status="pinned",
                execution_id=db.producer_of(pinned[self.outputs[0]]),
                outputs=pinned,
                started_at=now,
                finished_at=now,
            )
            logger.info("pinned outputs %s", {o: r.short for o, r in refs.items()})
            return self._shape(refs)

        structure, flat, params = self._split(call, exp.params.get(self.name, {}))
        spec = self.spec(structure, params)
        signature = sha256_json(spec)
        db.upsert_node_run(
            exp.id,
            self.name,
            status="running",
            signature=signature,
            inputs={k: r.hash for k, r in flat.items()},
            params=params,
            started_at=time.time(),
        )
        try:
            row, refs, status = self._resolve(lab, exp.id, signature, spec, flat, params, call)
        except BaseException as e:
            db.upsert_node_run(exp.id, self.name, status="failed", error=repr(e), finished_at=time.time())
            raise
        if status == "cached":
            db.upsert_node_run(
                exp.id,
                self.name,
                status="cached",
                execution_id=row["id"],
                outputs={o: r.hash for o, r in refs.items()},
                finished_at=time.time(),
            )
            saved = (row["finished_at"] or 0) - (row["started_at"] or 0)
            logger.info("cache hit %s (execution %s, saved %.2fs)", signature[:12], row["id"], saved)
        else:
            logger.info("computed %s (execution %s)", signature[:12], row["id"])
        return self._shape(refs)

    def _resolve(self, lab, exp_id, signature, spec, flat, params, call):
        db = lab.db
        owner = uuid.uuid4().hex
        lease = None
        if self.cache:
            waiting = False
            while True:
                hit = self._valid_hit(lab, signature)
                if hit:
                    return hit[0], hit[1], "cached"
                if db.acquire_lease(signature, owner, self.lease_ttl):
                    hit = self._valid_hit(lab, signature)  # lost a race with a publisher
                    if hit:
                        db.release_lease(signature, owner)
                        return hit[0], hit[1], "cached"
                    lease = (signature, owner)
                    break
                if not waiting:
                    db.upsert_node_run(exp_id, self.name, status="waiting")
                    waiting = True
                time.sleep(POLL_INTERVAL)
            db.upsert_node_run(exp_id, self.name, status="running")

        try:
            from prefect.runtime import task_run

            task_run_id = str(task_run.id) if task_run.id else None
        except Exception:  # pragma: no cover - outside a task run
            task_run_id = None
        exec_id = db.start_execution(
            signature=signature,
            node=self.name,
            spec=spec,
            cacheable=self.cache,
            experiment_id=exp_id,
            task_run_id=task_run_id,
        )
        if self.source:
            db.record_source(exec_id, self.source)
        try:
            with _Heartbeat(db, signature, owner, self.lease_ttl) if lease else nullcontext():
                refs, metric_values = self._compute(lab, exec_id, call, params)
            db.publish(
                exec_id,
                experiment_id=exp_id,
                node=self.name,
                inputs=flat,
                outputs=refs,
                metric_values=metric_values,
                node_run=dict(
                    status="computed",
                    execution_id=exec_id,
                    outputs={o: r.hash for o, r in refs.items()},
                    finished_at=time.time(),
                ),
                lease=lease,
            )
        except BaseException:
            db.fail_execution(exec_id, traceback.format_exc(limit=20))
            if lease:
                db.release_lease(*lease)
            raise
        return {"id": exec_id}, refs, "computed"

    def _compute(self, lab, exec_id: str, call: dict[str, Any], params: dict[str, Any]):
        kwargs = {}
        for pname in self.signature.parameters:
            if pname in params:
                kwargs[pname] = params[pname]
            elif pname in call:
                kwargs[pname] = _map_refs(call[pname], lab.store.load)
        wd = lab.home / "tmp" / exec_id
        wd.mkdir(parents=True, exist_ok=True)
        ctx = runtime.ExecContext(workdir=wd, execution_id=exec_id)
        token = runtime._exec.set(ctx)
        settings = lab.settings
        allowed = [wd, lab.home, lab.store.cache_dir, getattr(lab.store, "root", lab.home)]
        reads = guard.start(self.name, settings.check_reads, settings.project_root, allowed)
        try:
            try:
                result = self.fn(**kwargs)
            finally:
                watched = guard.stop(reads)
            if watched and watched.violation:  # even if the step swallowed the exception
                raise guard.UndeclaredRead(watched.violation)
            outputs = self._normalise(result)
            refs: dict[str, ArtifactRef] = {}
            for name, value in outputs.items():
                if isinstance(value, ArtifactRef):
                    refs[name] = value
                elif isinstance(value, Path):
                    refs[name] = lab.store.put_path(value)
                else:
                    refs[name] = lab.store.put_value(value)
        finally:
            runtime._exec.reset(token)
            shutil.rmtree(wd, ignore_errors=True)
        return refs, ctx.metrics


def _duration(row: dict | None) -> float | None:
    if row and row.get("finished_at") and row.get("started_at"):
        return row["finished_at"] - row["started_at"]
    return None


def _origin(row: dict | None) -> dict | None:
    if not row:
        return None
    return {"execution_id": row["id"], "experiment_id": row.get("experiment_id"), "duration": _duration(row)}


def _reason(spec: dict[str, Any], before: dict[str, Any] | None) -> str:
    """Why a step needs to run, judged against the parent experiment's execution of it."""
    if not before:
        return "new"

    def differs(*keys: str) -> bool:  # compared as hashed, so 20 and 20.0 differ
        return canonical_json([spec.get(k) for k in keys]) != canonical_json([before.get(k) for k in keys])

    for reason, keys in (("code", ("op", "code", "version", "outputs")), ("params", ("params",)),
                         ("inputs", ("inputs",)), ("env", ("env",))):
        if differs(*keys):
            return reason
    return "new"


def artifact_task(
    fn: Callable | None = None,
    *,
    outputs: Sequence[str] | None = None,
    name: str | None = None,
    version: str = "1",
    cache: bool = True,
    packages: Sequence[str] = (),
    hash_source: bool = True,
    lease_ttl: float = 300.0,
    **task_options: Any,
):
    """Declare a lineage-tracked, globally cached pipeline node.

    Arguments that receive ``ArtifactRef`` values are artifact inputs (materialised to a
    local ``Path`` – or the original object for value artifacts – before the function
    runs). All other arguments are parameters and can be overridden per experiment via
    ``params={"<node>": {"<arg>": value}}``. Return a ``Path`` (file or directory) or any
    picklable value per declared output. Extra keyword arguments go to ``prefect.task``.
    """

    def wrap(f: Callable) -> ArtifactTask:
        return ArtifactTask(
            f,
            outputs=outputs,
            name=name,
            version=version,
            cache=cache,
            packages=packages,
            hash_source=hash_source,
            lease_ttl=lease_ttl,
            **task_options,
        )

    return wrap(fn) if fn is not None else wrap
