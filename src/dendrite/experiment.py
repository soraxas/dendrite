"""Experiments, pipelines and the ``Lab`` that ties metadata and blob storage together."""

from __future__ import annotations

import copy
import os
import time
import uuid
import warnings
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from prefect import flow

from . import runtime
from .config import Settings
from .db import Metadata
from .store import PENDING, ArtifactRef, PlanIncomplete, open_store

_PIPELINES: dict[str, "Pipeline"] = {}
_MISSING = object()


class NotADraft(RuntimeError):
    """Raised when editing or discarding an experiment whose config is already frozen."""


class Pipeline:
    """A Prefect flow whose arguments are an experiment's input artifacts."""

    def __init__(self, fn: Callable, name: str | None = None, **flow_options: Any):
        self.fn = fn
        self.name = name or fn.__name__
        pipe = self

        def body(run_id: str, inputs: dict[str, Any]) -> Any:
            return pipe._body(run_id, inputs)

        body.__name__ = body.__qualname__ = self.name
        body.__module__ = fn.__module__
        self.flow = flow(body, name=self.name, validate_parameters=False, **flow_options)
        _PIPELINES[self.name] = self
        runtime.track_module(fn.__module__, pipeline=True)

    def __repr__(self) -> str:
        return f"<pipeline {self.name}>"

    def source_snapshot(self) -> dict[str, Any] | None:
        from .task import source_snapshot

        return source_snapshot(self.fn)

    def shape(self, source: str | None = None) -> dict[str, Any]:
        """Static dataflow graph of the body (or of ``source``, e.g. unsaved edits), never run.

        Names resolve against the loaded module, so steps are recognised even in edited text.
        Lines are relative to the analysed source; ``first_line``/``file`` locate it on disk.
        """
        from . import analysis

        snap = self.source_snapshot()
        text = source if source is not None else (snap or {}).get("source", "")
        g = self.fn.__globals__
        out = analysis.pipeline_shape(text, lambda n: g.get(n), module=self.fn.__module__)
        out["file"] = (snap or {}).get("file")
        out["first_line"] = (snap or {}).get("line") or 1
        return out

    def _body(self, run_id: str, inputs: dict[str, Any]) -> Any:
        from prefect.futures import wait
        from prefect.runtime import flow_run

        state = runtime.get(run_id)
        exp = state.experiment
        if flow_run.id:
            exp.lab.db.update_experiment(exp.id, flow_run_id=str(flow_run.id))
        token = runtime._active_run.set(run_id)
        try:
            result = self.fn(**inputs)
        finally:
            runtime._active_run.reset(token)
        if state.futures:
            wait(state.futures)
            for f in state.futures:
                f.result()  # re-raise the first failure
        _publish_prefect_summary(exp)
        return result


def pipeline(fn: Callable | None = None, *, name: str | None = None, **flow_options: Any):
    """Declare a pipeline. Its parameters are the experiment's named input artifacts."""

    def wrap(f: Callable) -> Pipeline:
        return Pipeline(f, name=name, **flow_options)

    return wrap(fn) if fn is not None else wrap


def _publish_prefect_summary(exp: "Experiment") -> None:
    try:
        from prefect.artifacts import create_markdown_artifact

        from .viz import mermaid

        rows = "\n".join(
            f"| {n['name']} | {n['status']} | `{(n.get('execution_id') or '')[:10]}` | "
            + ", ".join(f"{k}=`{v['hash'][:10]}`" for k, v in (n.get("outputs") or {}).items())
            + " |"
            for n in exp.dag()["nodes"]
            if n["kind"] == "node"
        )
        md = (
            f"# dendrite experiment `{exp.id}` {exp.name or ''}\n\n"
            f"parent: `{exp.parent_id or '-'}`\n\n"
            "| node | status | execution | outputs |\n|---|---|---|---|\n"
            f"{rows}\n\n```mermaid\n{mermaid(exp.dag())}\n```\n"
        )
        create_markdown_artifact(key=f"dendrite-{exp.id}", markdown=md, description="dendrite lineage")
    except Exception as e:  # visibility only; never fail a run over it
        warnings.warn(f"could not publish Prefect summary artifact: {e}")


def _flatten(d: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, Mapping) and v:
            out.update(_flatten(v, f"{key}."))
        else:
            out[key] = v
    return out


def _apply_overrides(params: dict[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(params)
    for key, value in overrides.items():
        if "." in key:
            node, arg = key.split(".", 1)
            out.setdefault(node, {})[arg] = value
        elif isinstance(value, Mapping):
            out.setdefault(key, {}).update(copy.deepcopy(dict(value)))
        else:
            raise ValueError(f"override {key!r} must be '<node>.<arg>' or a dict of node params")
    return out


class Lab:
    """Entry point: metadata DB + artifact store.

    ``Lab()`` reads ``DENDRITE_HOME`` (default ``.dendrite``), ``DENDRITE_DB_URL``
    (SQLite or ``postgresql+psycopg://…``) and ``DENDRITE_STORE_URL`` (a directory, or
    ``s3://bucket/prefix`` / any fsspec URL; pass ``storage_options`` for MinIO).
    """

    def __init__(
        self,
        home: str | os.PathLike | None = None,
        *,
        db_url: str | None = None,
        store_url: str | None = None,
        storage_options: dict[str, Any] | None = None,
    ):
        self.settings = Settings.resolve(home, db_url, store_url)
        self.home = self.settings.home
        self.home.mkdir(parents=True, exist_ok=True)
        self.db = Metadata(self.settings.db_url)
        self.store = open_store(self.settings.store_url, self.home / "cache", **(storage_options or {}))

    def __repr__(self) -> str:
        return f"<Lab db={self.settings.db_url} store={self.store.url}>"

    # -- artifacts -----------------------------------------------------------------
    def ingest(self, source: str | os.PathLike | ArtifactRef) -> ArtifactRef:
        ref = source if isinstance(source, ArtifactRef) else self.store.put_path(Path(source))
        self.db.register_artifacts([ref])
        return ref

    def artifact(self, key: str | ArtifactRef) -> ArtifactRef:
        if isinstance(key, ArtifactRef):
            return key
        ref = self.db.artifact(key) or self.db.resolve_artifact_prefix(key)
        if ref is None:
            raise KeyError(f"unknown or ambiguous artifact {key!r}")
        return ref

    def load(self, ref: str | ArtifactRef) -> Any:
        """Local path (file/dir artifacts) or Python object (value artifacts)."""
        return self.store.load(self.artifact(ref))

    def lineage(self, key: str | ArtifactRef) -> dict[str, Any]:
        """Provenance graph around an artifact: ancestors, descendants and users."""
        ref = self.artifact(key)
        anc = self.db.ancestor_executions(ref.hash)
        desc = self.db.descendant_executions(ref.hash)
        execs = {}
        for eid in [*anc, *desc]:
            if eid not in execs:
                execs[eid] = self.db.execution(eid)
        hashes = {ref.hash}
        edges = []
        for e in execs.values():
            for name, h in e["inputs"].items():
                hashes.add(h)
                edges.append({"from": h, "to": e["id"], "label": name})
            for name, h in e["outputs"].items():
                hashes.add(h)
                edges.append({"from": e["id"], "to": h, "label": name})
        refs = self.db.artifacts_by_hash(hashes)
        users = self.db.experiments_using_artifact(ref.hash)
        producer = self.db.producer_of(ref.hash)
        producer_row = self.db.execution(producer) if producer else None

        # Label every artifact by what it *is* in the pipeline (an output or input name), not by
        # its storage kind; fall back to filename, then kind.
        produced_by: dict[str, tuple[str, str]] = {}  # hash -> (node, output name)
        read_as: dict[str, str] = {}  # hash -> first input name it was read under
        desc_set = set(desc)
        from_desc: set[str] = set()
        for e in execs.values():
            for name, h in e["outputs"].items():
                produced_by.setdefault(h, (e["node"], name))
                if e["id"] in desc_set:
                    from_desc.add(h)
            for name, h in e["inputs"].items():
                read_as.setdefault(h, name)
        if producer_row:
            for name, h in producer_row["outputs"].items():
                if h == ref.hash:
                    produced_by.setdefault(h, (producer_row["node"], name))
        source_names = self._source_input_names(hashes)

        def annotate(r: ArtifactRef) -> dict[str, Any]:
            d = _ref_dict(r)
            node, out = produced_by.get(r.hash, (None, None))
            d["producer_node"] = node
            d["label"] = out or source_names.get(r.hash) or read_as.get(r.hash) or r.filename or r.kind
            d["role"] = "focus" if r.hash == ref.hash else "descendant" if r.hash in from_desc else "ancestor"
            return d

        return {
            "artifact": annotate(ref),
            "producer": producer,
            "ancestors": anc,
            "descendants": desc,
            "executions": [
                {k: e[k] for k in ("id", "node", "signature", "status", "started_at", "finished_at", "experiment_id")}
                for e in execs.values()
            ],
            "artifacts": [annotate(r) for r in refs.values()],
            "edges": edges,
            "experiments": users,
        }

    def _source_input_names(self, hashes: set[str]) -> dict[str, str]:
        names: dict[str, str] = {}
        for row in self.db.experiments():
            for name, h in (row["inputs"] or {}).items():
                if h in hashes:
                    names.setdefault(h, name)
        return names

    # -- experiments ---------------------------------------------------------------
    def create(
        self,
        pipeline: "Pipeline | str",
        *,
        params: Mapping[str, Any] | None = None,
        inputs: Mapping[str, Any] | None = None,
        name: str | None = None,
        draft: bool = False,
        _parent: str | None = None,
        _pins: dict[str, dict[str, str]] | None = None,
    ) -> "Experiment":
        """New experiment. ``draft=True`` keeps its config editable until it first runs."""
        pipe = _resolve_pipeline(pipeline)
        nested = _apply_overrides({}, params or {})
        input_hashes = {k: self.ingest(v).hash for k, v in (inputs or {}).items()}
        exp_id = "e-" + uuid.uuid4().hex[:10]
        self.db.create_experiment(
            id=exp_id,
            name=name,
            parent_id=_parent,
            pipeline=pipe.name,
            params=nested,
            inputs=input_hashes,
            pins=_pins or {},
            status="draft" if draft else "created",
            created_at=time.time(),
        )
        return self.get(exp_id)

    def get(self, key: str) -> "Experiment":
        row = self.db.experiment(key)
        if row is None:
            raise KeyError(f"no experiment {key!r}")
        return Experiment(self, row)

    def experiments(self) -> list["Experiment"]:
        return [Experiment(self, r) for r in self.db.experiments()]

    # -- deleting ------------------------------------------------------------------
    def delete(self, ids: Iterable[str], *, forks: str = "refuse", force: bool = False) -> dict[str, Any]:
        """Delete experiments in one transaction; lineage and executions stay (see ``storage.py``).

        ``forks`` decides what happens to forks of a deleted experiment that weren't selected:
        ``"refuse"`` (raise ``ValueError``), ``"delete"`` (delete them too) or ``"keep"``
        (they become forks of the nearest ancestor that stays, or of nothing). Running
        experiments are refused unless ``force``. Returns ``{"deleted", "reparented"}``.
        """
        rows = {r["id"]: r for r in self.db.experiments()}
        targets = set(ids)
        unknown = targets - set(rows)
        if unknown:
            raise KeyError(f"no experiment {sorted(unknown)[0]!r}")
        if forks not in ("refuse", "delete", "keep"):
            raise ValueError("forks must be 'refuse', 'delete' or 'keep'")
        kids: dict[str, list[str]] = {}
        for r in rows.values():
            kids.setdefault(r["parent_id"], []).append(r["id"])
        if forks == "delete":
            stack = list(targets)
            while stack:
                for c in kids.get(stack.pop(), []):
                    if c not in targets:
                        targets.add(c)
                        stack.append(c)
        reparent: dict[str, str | None] = {}
        for r in rows.values():
            if r["id"] not in targets and r["parent_id"] in targets:
                if forks == "refuse":
                    raise ValueError(f"experiment {r['parent_id']} has fork {r['id']}; delete or keep its forks")
                up = r["parent_id"]
                while up in targets:
                    up = rows[up]["parent_id"]
                reparent[r["id"]] = up

        def depth(eid: str) -> int:
            d = 0
            while rows[eid]["parent_id"] in rows:
                eid, d = rows[eid]["parent_id"], d + 1
            return d

        order = sorted(targets, key=depth, reverse=True)  # children before parents
        self.db.delete_experiments(order, refuse_status=() if force else ("running",), reparent=reparent)
        return {"deleted": order, "reparented": reparent}

    def deletion_estimate(self, ids: Iterable[str], forks: str = "keep") -> dict[str, Any]:
        """Bytes that deleting ``ids`` (and with ``forks="delete"`` their forks) would free after GC."""
        from .storage import Storage

        targets = set(ids)
        if forks == "delete":
            rows = self.db.experiments()
            grew = True
            while grew:
                more = {r["id"] for r in rows if r["parent_id"] in targets} - targets
                targets |= more
                grew = bool(more)
        return {"ids": sorted(targets), "bytes": Storage(self).frees(targets)}

    # -- storage -------------------------------------------------------------------
    def storage(self) -> dict[str, Any]:
        """Where the space goes: store totals (live vs collectable), per experiment, cache
        entries, the largest artifacts and local scratch. See ``storage.py``."""
        from .storage import Storage

        return Storage(self).inventory()

    def gc(
        self,
        older_than: float | None = None,
        *,
        dry_run: bool = True,
        grace: float | None = None,
        force: bool = False,
        hashes: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """Delete blobs no experiment reaches (a dry run unless ``dry_run=False``).

        ``older_than`` (seconds) also evicts cache last used before then, sparing kept
        experiments, pins and each pipeline's newest completed run.
        """
        from .storage import Storage

        return Storage(self).gc(older_than, dry_run=dry_run, grace=grace, force=force, hashes=hashes)

    def evict(self, target: str, *, dry_run: bool = True, force: bool = False) -> dict[str, Any]:
        """Delete an execution's (or artifact's) output blobs; they recompute when next needed."""
        from .storage import Storage

        return Storage(self).evict(target, dry_run=dry_run, force=force)

    def clean(self, *, force: bool = False) -> dict[str, Any]:
        """Clear local checkouts and stale step workdirs (never stored objects)."""
        from .storage import Storage

        return Storage(self).clean(force=force)

    def tree(self) -> list[dict[str, Any]]:
        """Flat list of experiments with parent links (the fork tree)."""
        out = []
        for e in self.experiments():
            counts: dict[str, int] = {}
            for nr in self.db.node_runs(e.id):
                counts[nr["status"]] = counts.get(nr["status"], 0) + 1
            out.append(
                {
                    "id": e.id,
                    "name": e.name,
                    "parent_id": e.parent_id,
                    "pipeline": e.pipeline_name,
                    "status": e.status,
                    "keep": e.kept,
                    "created_at": e.created_at,
                    "started_at": e._row["started_at"],
                    "finished_at": e._row["finished_at"],
                    "counts": counts,
                }
            )
        return out


def analysis_suggest(name: str, options: list[str]) -> str:
    from .analysis import _suggest

    return _suggest(name, options)


def _resolve_pipeline(p: "Pipeline | str") -> "Pipeline":
    if isinstance(p, Pipeline):
        return p
    try:
        return _PIPELINES[p]
    except KeyError:
        raise KeyError(f"pipeline {p!r} is not registered; import the module that defines it") from None


def _ref_dict(ref: ArtifactRef | None) -> dict[str, Any] | None:
    if ref is None:
        return None
    return {"hash": ref.hash, "kind": ref.kind, "size": ref.size, "filename": ref.filename}


class Experiment:
    """An immutable configuration snapshot of a pipeline, plus the results of running it."""

    def __init__(self, lab: Lab, row: dict[str, Any]):
        self.lab = lab
        self._row = row

    def __repr__(self) -> str:
        return f"<Experiment {self.id} {self.name or ''} [{self.status}]>"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Experiment) and other.id == self.id

    def __hash__(self) -> int:
        return hash(self.id)

    id = property(lambda self: self._row["id"])
    name = property(lambda self: self._row["name"])
    parent_id = property(lambda self: self._row["parent_id"])
    pipeline_name = property(lambda self: self._row["pipeline"])
    params = property(lambda self: self._row["params"])
    inputs = property(lambda self: self._row["inputs"])
    pins = property(lambda self: self._row["pins"])
    status = property(lambda self: self._row["status"])
    created_at = property(lambda self: self._row["created_at"])
    flow_run_id = property(lambda self: self._row["flow_run_id"])
    error = property(lambda self: self._row["error"])
    kept = property(lambda self: bool(self._row.get("keep")))

    @property
    def pipeline(self) -> Pipeline:
        return _resolve_pipeline(self.pipeline_name)

    @property
    def parent(self) -> "Experiment | None":
        return self.lab.get(self.parent_id) if self.parent_id else None

    def refresh(self) -> "Experiment":
        self._row = self.lab.db.experiment(self.id)
        return self

    # -- running -------------------------------------------------------------------
    def run(self) -> "Experiment":
        db = self.lab.db
        pipe = self.pipeline
        start = dict(status="running", started_at=time.time(), finished_at=None, error=None)
        if self.status in ("draft", "created"):
            # Freezing point: an edit racing this either landed before it or fails afterwards, and
            # two processes starting the same never-run experiment can't both win.
            if not db.update_experiment_if(self.id, self.status, **start):
                raise NotADraft(f"experiment {self.id} was started or changed elsewhere")
            self.refresh()
        else:
            db.update_experiment(self.id, **start)
        refs = {n: self.lab.artifact(h) for n, h in self.inputs.items()}
        db.clear_node_runs(self.id)
        state = runtime.register(self)
        try:
            pipe.flow.with_options(flow_run_name=self.name or self.id)(run_id=state.run_id, inputs=refs)
        except BaseException as e:
            db.update_experiment(self.id, status="failed", finished_at=time.time(), error=repr(e))
            raise
        finally:
            runtime.unregister(state.run_id)
        db.update_experiment(self.id, status="completed", finished_at=time.time())
        ran = state.called
        for node in self.params:
            if node not in ran:
                warnings.warn(f"params given for node {node!r}, which the pipeline did not run")
        return self.refresh()

    def fork(
        self,
        params: Mapping[str, Any] | None = None,
        *,
        pin: Mapping[str, Any] | None = None,
        inputs: Mapping[str, Any] | None = None,
        name: str | None = None,
        pipeline: "Pipeline | str | None" = None,
        draft: bool = False,
    ) -> "Experiment":
        """New experiment from this one's config (``draft=True``: editable until it runs).

        ``params`` overrides by ``"node.arg"`` or ``{"node": {...}}``. ``pin`` freezes node
        outputs: ``{"colmap": other_experiment}``, ``{"colmap.cameras": ref_or_hash}`` or
        ``{"colmap": {"cameras": ref_or_hash}}``; ``{"colmap": None}`` removes an inherited
        pin. Pinned nodes are skipped at run time.
        """
        new_params, pins, new_inputs = self._derive(params, pin, inputs)
        return self.lab.create(
            pipeline or self.pipeline_name,
            params=new_params,
            inputs={k: self.lab.artifact(h) for k, h in new_inputs.items()},
            name=name,
            draft=draft,
            _parent=self.id,
            _pins=pins,
        )

    def _derive(self, params, pin, inputs) -> tuple[dict, dict, dict]:
        """This config with fork-style overrides applied: (params, pins, input hashes)."""
        new_params = _apply_overrides(self.params, params or {})
        pins = copy.deepcopy(self.pins)
        for key, val in (pin or {}).items():
            node, _, out = key.partition(".")
            if val is None:  # unpin: let the node resolve normally again
                pins.pop(node, None)
                continue
            pins.setdefault(node, {}).update(self._resolve_pin(node, out, val))
        new_inputs = dict(self.inputs)
        for k, v in (inputs or {}).items():
            new_inputs[k] = self.lab.ingest(v).hash
        return new_params, pins, new_inputs

    # -- drafts --------------------------------------------------------------------
    def update(
        self,
        params: Mapping[str, Any] | None = None,
        *,
        pin: Mapping[str, Any] | None = None,
        inputs: Mapping[str, Any] | None = None,
        name: str | None = None,
        reset: Iterable[str] = (),
    ) -> "Experiment":
        """Edit a draft in place, with the same override rules as :meth:`fork`.

        ``reset`` takes ``"node.arg"`` keys and returns them to the parent's value (or drops
        the override when the parent has none).
        """
        if self.refresh().status != "draft":
            raise NotADraft(f"experiment {self.id} is {self.status}; fork it to make changes")
        new_params, pins, new_inputs = self._derive(params, pin, inputs)
        parent = self.parent
        for key in reset:
            node, _, arg = key.partition(".")
            inherited = (parent.params if parent else {}).get(node, {})
            if arg in inherited:
                new_params.setdefault(node, {})[arg] = copy.deepcopy(inherited[arg])
            elif node in new_params:
                new_params[node].pop(arg, None)
                if not new_params[node]:
                    del new_params[node]
        values: dict[str, Any] = dict(params=new_params, pins=pins, inputs=new_inputs)
        if name is not None:
            values["name"] = name or None
        if not self.lab.db.update_experiment_if(self.id, "draft", **values):
            raise NotADraft(f"experiment {self.id} stopped being a draft")
        return self.refresh()

    def discard(self) -> None:
        """Delete a draft (refused for frozen experiments and drafts that have forks)."""
        try:
            self.lab.db.delete_draft(self.id)
        except ValueError as e:
            raise NotADraft(str(e)) from None

    def descendants(self) -> list["Experiment"]:
        """Every fork of this experiment, recursively, children before their parents."""
        rows = self.lab.db.experiments()
        kids: dict[str, list[dict]] = {}
        for r in rows:
            kids.setdefault(r["parent_id"], []).append(r)
        out: list[Experiment] = []

        def walk(eid: str) -> None:
            for r in kids.get(eid, []):
                walk(r["id"])
                out.append(Experiment(self.lab, r))

        walk(self.id)
        return out

    def delete(self, forks: bool = False, *, keep_forks: bool = False, force: bool = False) -> list[str]:
        """Delete this experiment, returning the ids deleted.

        With forks, pass ``forks=True`` to delete them too, or ``keep_forks=True`` to keep
        them as forks of this one's parent. Executions, artifacts, metrics and lineage stay,
        so other experiments keep reusing shared results; ``lab.gc()`` then frees what only
        the deleted experiments reached. Refused for running experiments (``force=True`` for
        one left ``running`` by a crash).
        """
        mode = "delete" if forks else "keep" if keep_forks else "refuse"
        return self.lab.delete([self.id], forks=mode, force=force)["deleted"]

    def mark_stopped(self) -> "Experiment":
        """Mark a ``running`` experiment whose process died as failed, so clean-up can proceed."""
        ok = self.lab.db.update_experiment_if(
            self.id, "running", status="failed", finished_at=time.time(), error="marked as stopped (its run was no longer active)"
        )
        if not ok:
            raise ValueError(f"experiment {self.id} is not running")
        return self.refresh()

    def keep(self, flag: bool = True) -> "Experiment":
        """Protect everything this experiment reaches from age-based ``lab.gc(older_than=…)``."""
        self.lab.db.set_keep(self.id, flag)
        return self.refresh()

    # -- checking ------------------------------------------------------------------
    def check(self) -> list[dict[str, Any]]:
        """Config problems found before running: params/pins naming steps or args that don't exist."""
        shape = self.pipeline.shape()
        steps = shape.pop("_steps")
        severity = "warning" if shape["dynamic"] else "error"  # a dynamic region may hide the step
        found = []
        for kind, config in (("params", self.params), ("pins", self.pins)):
            for node, values in config.items():
                if node not in steps:
                    hint = analysis_suggest(node, list(steps))
                    found.append({"code": "unknown-step", "severity": severity, "step": node,
                                  "message": f"{kind} name step {node!r}, which the pipeline doesn't call{hint}"})
                elif kind == "params":
                    sig = steps[node].signature.parameters
                    for arg in values:
                        if arg not in sig:
                            found.append({"code": "unknown-param", "severity": "error", "step": node,
                                          "message": f"{node} has no parameter {arg!r}{analysis_suggest(arg, list(sig))}"})
        return found

    # -- planning ------------------------------------------------------------------
    def plan(self) -> dict[str, Any]:
        """Predict what running this experiment now would do, without running or writing anything.

        Executes the pipeline body with steps that stop after the cache check. Each step is
        ``reuse`` (a verified result exists), ``run`` (with a ``reason`` and an ``estimate``)
        or ``frozen`` (pinned). Steps fed by a ``run`` step are ``run``/``upstream``: an upper
        bound, since an unchanged upstream output would still let them reuse.
        """
        from .task import env_fingerprint

        error = runtime.refresh_modules()
        pipe = self.pipeline
        db = self.lab.db
        state = runtime.register(self, plan=True)
        ancestor = self.parent
        if ancestor:
            state.parent_params = ancestor.params
        while ancestor is not None:  # nearest ancestor that actually ran each step
            for nr in ancestor.node_runs():
                state.parent_runs.setdefault(nr["node"], nr)
            ancestor = ancestor.parent
        inputs = {n: self.lab.artifact(h) for n, h in self.inputs.items()}
        complete, blocked_by = True, None
        token = runtime._active_run.set(state.run_id)
        try:
            pipe.fn(**inputs)
        except PlanIncomplete as e:
            complete, blocked_by = False, e.node
        except Exception as e:
            complete, error = False, f"{type(e).__name__}: {e}"
        finally:
            runtime._active_run.reset(token)
            runtime.unregister(state.run_id)

        hashes = set(self.inputs.values())
        for p in state.planned:
            hashes.update((p.get("inputs") or {}).values())
            hashes.update((p.get("outputs") or {}).values())
        refs = db.artifacts_by_hash(h for h in hashes if not h.startswith("plan:"))
        refs.update({h: r for h, r in state.refs.items() if r.kind == PENDING})
        producers: dict[str, tuple[str, str]] = {}
        nodes: list[dict[str, Any]] = []
        for name, h in self.inputs.items():
            producers[h] = (f"input:{name}", name)
            nodes.append({"id": f"input:{name}", "kind": "input", "name": name, "status": "input", "artifact": _ref_dict(refs.get(h))})
        for p in state.planned:
            for out, h in (p.get("outputs") or {}).items():
                producers.setdefault(h, (p["node"], out))
        edges = []
        for p in state.planned:
            origin = p.get("reuse_from") or {}
            nodes.append({
                "id": p["node"],
                "kind": "node",
                "name": p["node"],
                "seq": p["seq"],
                "status": "planned",
                "plan": p.get("plan", "run"),
                "reason": p.get("reason"),
                "estimate": p.get("estimate"),
                "reuse_from": p.get("reuse_from"),
                "execution_experiment": origin.get("experiment_id"),
                "execution_id": origin.get("execution_id"),
                "signature": p.get("signature"),
                "params": p.get("params") or {},
                "param_schema": p.get("param_schema") or [],
                "inputs": {k: _ref_dict(refs.get(h)) for k, h in (p.get("inputs") or {}).items()},
                "outputs": {k: _ref_dict(refs.get(h)) for k, h in (p.get("outputs") or {}).items()},
                "duration": None,
            })
            for in_name, h in (p.get("inputs") or {}).items():
                if h in producers:
                    src, out = producers[h]
                    edges.append({"from": src, "to": p["node"], "artifact": h, "label": out, "input": in_name})
        steps = [n for n in nodes if n["kind"] == "node"]
        to_run = [n for n in steps if n["plan"] == "run"]
        totals = {
            "steps": len(steps),
            "run": len(to_run),
            "reuse": sum(n["plan"] == "reuse" for n in steps),
            "frozen": sum(n["plan"] == "frozen" for n in steps),
            "estimate": sum(n["estimate"] or 0 for n in to_run),
            "unestimated": sum(n["estimate"] is None for n in to_run),
        }
        return {
            "experiment": self.summary(),
            "complete": complete,
            "blocked_by": blocked_by,
            "error": error,
            "env": env_fingerprint(()),
            "totals": totals,
            "nodes": nodes,
            "edges": edges,
        }

    def _resolve_pin(self, node: str, out: str, val: Any) -> dict[str, str]:
        def h(v: Any) -> str:
            ref = self.lab.artifact(v)
            if not self.lab.store.verify(ref):
                raise ValueError(f"pinned artifact {ref.short} is missing from the store")
            return ref.hash

        if isinstance(val, Experiment):
            produced = val.node_outputs(node)
            if not produced:
                raise ValueError(f"experiment {val.id} has no outputs for node {node!r}")
            if out:
                return {out: h(produced[out])}
            return {k: h(v) for k, v in produced.items()}
        if isinstance(val, Mapping):
            return {k: h(v) for k, v in val.items()}
        if not out:
            raise ValueError(f"pin {node!r} with a single artifact needs '<node>.<output>' as the key")
        return {out: h(val)}

    # -- results -------------------------------------------------------------------
    def node_runs(self) -> list[dict[str, Any]]:
        return self.lab.db.node_runs(self.id)

    def node_outputs(self, node: str) -> dict[str, str]:
        for nr in self.node_runs():
            if nr["node"] == node:
                return dict(nr["outputs"] or {})
        return {}

    def output(self, node: str, name: str | None = None) -> ArtifactRef:
        outs = self.node_outputs(node)
        if not outs:
            raise KeyError(f"node {node!r} has no outputs in {self.id}")
        if name is None:
            if len(outs) != 1:
                raise KeyError(f"node {node!r} has outputs {sorted(outs)}; pass name=")
            name = next(iter(outs))
        return self.lab.artifact(outs[name])

    def load(self, node: str, name: str | None = None) -> Any:
        return self.lab.load(self.output(node, name))

    def metrics(self) -> dict[str, dict[str, float]]:
        nrs = self.node_runs()
        per_exec = self.lab.db.metrics_for(nr["execution_id"] for nr in nrs)
        return {nr["node"]: per_exec[nr["execution_id"]] for nr in nrs if nr["execution_id"] in per_exec}

    def dag(self) -> dict[str, Any]:
        """Nodes (inputs + pipeline nodes) and artifact edges, with live status."""
        db = self.lab.db
        nrs = self.node_runs()
        producers: dict[str, tuple[str, str]] = {}
        nodes: list[dict[str, Any]] = []
        all_hashes = set(self.inputs.values())
        for nr in nrs:
            all_hashes.update((nr["inputs"] or {}).values())
            all_hashes.update((nr["outputs"] or {}).values())
        refs = db.artifacts_by_hash(all_hashes)
        for name, h in self.inputs.items():
            nid = f"input:{name}"
            producers[h] = (nid, name)
            nodes.append({"id": nid, "kind": "input", "name": name, "status": "input", "artifact": _ref_dict(refs.get(h))})
        per_exec = db.metrics_for(nr["execution_id"] for nr in nrs)
        for nr in nrs:
            for out, h in (nr["outputs"] or {}).items():
                producers.setdefault(h, (nr["node"], out))
        edges = []
        for nr in nrs:
            ex = db.execution(nr["execution_id"]) if nr["execution_id"] else None
            duration = None
            if ex and ex["finished_at"]:
                duration = ex["finished_at"] - ex["started_at"]
            elif nr["status"] in ("running", "waiting") and nr["started_at"]:
                duration = time.time() - nr["started_at"]
            nodes.append(
                {
                    "id": nr["node"],
                    "kind": "node",
                    "name": nr["node"],
                    "seq": nr["seq"],
                    "status": nr["status"],
                    "signature": nr["signature"],
                    "execution_id": nr["execution_id"],
                    "execution_experiment": ex["experiment_id"] if ex else None,
                    "params": nr["params"],
                    "inputs": {k: _ref_dict(refs.get(h)) for k, h in (nr["inputs"] or {}).items()},
                    "outputs": {k: _ref_dict(refs.get(h)) for k, h in (nr["outputs"] or {}).items()},
                    "metrics": per_exec.get(nr["execution_id"], {}),
                    "duration": duration,
                    "queued_at": nr["queued_at"],
                    "started_at": nr["started_at"],
                    "finished_at": nr["finished_at"],
                    "error": nr["error"],
                }
            )
            for in_name, h in (nr["inputs"] or {}).items():
                if h in producers:
                    src, out = producers[h]
                    edges.append({"from": src, "to": nr["node"], "artifact": h, "label": out, "input": in_name})
        return {"experiment": self.summary(), "nodes": nodes, "edges": edges}

    def summary(self) -> dict[str, Any]:
        r = self._row
        return {
            "id": r["id"],
            "name": r["name"],
            "parent_id": r["parent_id"],
            "pipeline": r["pipeline"],
            "status": r["status"],
            "params": r["params"],
            "inputs": r["inputs"],
            "pins": r["pins"],
            "created_at": r["created_at"],
            "started_at": r["started_at"],
            "finished_at": r["finished_at"],
            "flow_run_id": r["flow_run_id"],
            "error": r["error"],
            "keep": bool(r.get("keep")),
        }

    def diff(self, other: "Experiment") -> dict[str, Any]:
        fa, fb = _flatten(self.params), _flatten(other.params)
        params = [
            {"key": k, "a": fa.get(k), "b": fb.get(k), "change": "added" if k not in fa else "removed" if k not in fb else "changed"}
            for k in sorted(set(fa) | set(fb))
            if fa.get(k, _MISSING) != fb.get(k, _MISSING)
        ]
        inputs = [
            {"name": k, "a": self.inputs.get(k), "b": other.inputs.get(k)}
            for k in sorted(set(self.inputs) | set(other.inputs))
            if self.inputs.get(k) != other.inputs.get(k)
        ]
        ra = {nr["node"]: nr for nr in self.node_runs()}
        rb = {nr["node"]: nr for nr in other.node_runs()}
        order = list(ra) + [n for n in rb if n not in ra]
        nodes = []
        for n in order:
            a, b = ra.get(n), rb.get(n)
            outs_a = (a or {}).get("outputs") or {}
            outs_b = (b or {}).get("outputs") or {}
            nodes.append(
                {
                    "node": n,
                    "a_status": a and a["status"],
                    "b_status": b and b["status"],
                    "same_signature": bool(a and b and a["signature"] and a["signature"] == b["signature"]),
                    "same_outputs": bool(outs_a) and outs_a == outs_b,
                    "same_execution": bool(a and b and a["execution_id"] and a["execution_id"] == b["execution_id"]),
                }
            )
        ma, mb = self.metrics(), other.metrics()
        metric_rows = []
        for n in sorted(set(ma) | set(mb)):
            for k in sorted(set(ma.get(n, {})) | set(mb.get(n, {}))):
                metric_rows.append({"node": n, "key": k, "a": ma.get(n, {}).get(k), "b": mb.get(n, {}).get(k)})
        return {
            "a": self.id,
            "b": other.id,
            "params": params,
            "inputs": inputs,
            "pins": {"a": self.pins, "b": other.pins},
            "nodes": nodes,
            "metrics": metric_rows,
        }
