"""Experiments, pipelines and the ``Lab`` that ties metadata and blob storage together."""

from __future__ import annotations

import copy
import os
import time
import uuid
import warnings
from pathlib import Path
from typing import Any, Callable, Mapping

from prefect import flow

from . import runtime
from .config import Settings
from .db import Metadata
from .store import ArtifactRef, open_store

_PIPELINES: dict[str, "Pipeline"] = {}
_MISSING = object()


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

    def __repr__(self) -> str:
        return f"<pipeline {self.name}>"

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
        return {
            "artifact": _ref_dict(ref),
            "producer": self.db.producer_of(ref.hash),
            "ancestors": anc,
            "descendants": desc,
            "executions": [
                {k: e[k] for k in ("id", "node", "signature", "status", "started_at", "finished_at", "experiment_id")}
                for e in execs.values()
            ],
            "artifacts": [_ref_dict(r) for r in refs.values()],
            "edges": edges,
            "experiments": self.db.experiments_using_artifact(ref.hash),
        }

    # -- experiments ---------------------------------------------------------------
    def create(
        self,
        pipeline: "Pipeline | str",
        *,
        params: Mapping[str, Any] | None = None,
        inputs: Mapping[str, Any] | None = None,
        name: str | None = None,
        _parent: str | None = None,
        _pins: dict[str, dict[str, str]] | None = None,
    ) -> "Experiment":
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
            status="created",
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
                    "created_at": e.created_at,
                    "counts": counts,
                }
            )
        return out


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
        refs = {n: self.lab.artifact(h) for n, h in self.inputs.items()}
        db.clear_node_runs(self.id)
        db.update_experiment(self.id, status="running", started_at=time.time(), finished_at=None, error=None)
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
    ) -> "Experiment":
        """New experiment from this one's config.

        ``params`` overrides by ``"node.arg"`` or ``{"node": {...}}``. ``pin`` freezes node
        outputs: ``{"colmap": other_experiment}``, ``{"colmap.cameras": ref_or_hash}`` or
        ``{"colmap": {"cameras": ref_or_hash}}``; ``{"colmap": None}`` removes an inherited
        pin. Pinned nodes are skipped at run time.
        """
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
        return self.lab.create(
            pipeline or self.pipeline_name,
            params=new_params,
            inputs={k: self.lab.artifact(h) for k, h in new_inputs.items()},
            name=name,
            _parent=self.id,
            _pins=pins,
        )

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
