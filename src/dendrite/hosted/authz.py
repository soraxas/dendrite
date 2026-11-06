"""Permission checks and attribution for one project's metadata on the hosted server.

``ServerMetadata`` is the SQL ``Metadata`` with every write checked against the current
caller (``CALLER``), which the request handlers set. The remote API and the web UI both reach
project metadata through it, so they share one set of rules:

* reads need membership (the handlers check that before calling in);
* writes need ``runner``, and writes to an experiment or execution need its owner, unless
  the caller is an ``admin``;
* lease owners are bound to the caller, so only the holder renews or releases a live lease;
* publish and artifact registration need every blob already committed to the store.

With no caller set (server-side maintenance) nothing is checked.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterable
from typing import Any

from ..db import Metadata
from ..store import ArtifactRef, ArtifactStore
from .control import Caller, Control, role_at_least

PROJECT_POOL, PROJECT_OVERFLOW = 2, 3  # per open project: 2 idle, at most 5 connections

CALLER: contextvars.ContextVar[Caller | None] = contextvars.ContextVar("dendrite_caller", default=None)


class Forbidden(PermissionError):
    status = 403


class Conflict(RuntimeError):
    status = 409


def _caller() -> Caller | None:
    return CALLER.get()


def need(role: str) -> Caller | None:
    c = _caller()
    if c is not None and not (c.is_admin or role_at_least(c.role, role)):
        raise Forbidden(f"needs the {role} role in this project")
    return c


class ServerMetadata(Metadata):
    def __init__(self, url: str, *, project: str, control: Control, store: ArtifactStore):
        # A server holds an engine per open project; keep each pool small.
        super().__init__(url, pool_size=2, max_overflow=PROJECT_OVERFLOW)
        self.project, self.control, self.store = project, control, store
        self._owners: dict[tuple[str, str], str] = {}

    # -- helpers -----------------------------------------------------------------
    def _owner(self, kind: str, ids: Iterable[str]) -> dict[str, str]:
        # Who created something never changes, so a lookup is cached for good.
        ids = list(ids)
        missing = [i for i in ids if (kind, i) not in self._owners]
        if missing:
            for k, v in self.control.attributed(self.project, kind, missing).items():
                self._owners[(kind, k)] = v["user_id"]
        return {i: self._owners[(kind, i)] for i in ids if (kind, i) in self._owners}

    def _own(self, kind: str, ids: Iterable[str], role: str = "runner") -> Caller | None:
        c = need(role)
        if c is None or c.is_admin or c.role == "admin":
            return c
        ids = list(ids)
        owners = self._owner(kind, ids)
        theirs = [i for i in ids if owners.get(i) != c.user_id]
        if theirs:
            raise Forbidden(f"{kind} {theirs[0]} belongs to someone else")
        return c

    def _exp_of(self, exec_id: str) -> str | None:
        row = super().execution(exec_id)
        return row["experiment_id"] if row else None

    def _committed(self, refs: Iterable[ArtifactRef]) -> None:
        refs = [r for r in refs if r is not None]
        hashes = {r.hash for r in refs}
        for r in refs:
            if r.kind == "dir" and self.store.has(r.hash):
                hashes |= {h for _, h, _ in self.store.manifest(r)}
        missing = hashes - self.store.has_many(hashes)
        if missing:
            raise Conflict(f"blob {sorted(missing)[0][:12]} is not committed; upload it before publishing")

    def _tag(self, owner: str) -> str:
        c = _caller()
        return owner if c is None or owner.startswith(c.tag) else c.tag + owner

    # -- cache lookups --------------------------------------------------------------
    def cache_scopes(self) -> list[ServerMetadata]:
        """Where a cache lookup searches, in order. Only this project for now; a later change
        can append granted projects (their hits verified against their own store prefix)."""
        return [self]

    def find_success(self, signature: str) -> list:
        out: list = []
        for scope in self.cache_scopes():
            out += Metadata.find_success(scope, signature)
        return out

    # -- annotated reads ----------------------------------------------------------
    def _with_owners(self, rows: list[dict]) -> list[dict]:
        who = self.control.attributed(self.project, "experiment", [r["id"] for r in rows])
        for r in rows:
            r["created_by"] = who.get(r["id"], {}).get("label")
        return rows

    def experiment(self, key: str) -> dict | None:
        row = super().experiment(key)
        return self._with_owners([row])[0] if row else None

    def experiments(self) -> list[dict]:
        return self._with_owners(super().experiments())

    def execution(self, exec_id: str) -> dict | None:
        row = super().execution(exec_id)
        if row:
            row["run_by"] = self.control.attributed(self.project, "execution", [exec_id]).get(exec_id, {}).get("label")
        return row

    # -- experiments -------------------------------------------------------------
    def create_experiment(self, **values: Any) -> None:
        c = need("runner")
        super().create_experiment(**values)
        if c is not None:
            self.control.attribute(self.project, "experiment", [values["id"]], c)

    def update_experiment(self, exp_id: str, **values: Any) -> None:
        self._own("experiment", [exp_id])
        super().update_experiment(exp_id, **values)

    def update_experiment_if(self, exp_id: str, when: str | Iterable[str], **values: Any) -> bool:
        self._own("experiment", [exp_id])
        return super().update_experiment_if(exp_id, when, **values)

    # -- artifact names: anyone who runs may name; only the setter or an admin may change one --
    def _may_change_name(self, c: Caller | None, cur: dict | None) -> None:
        if c is None or cur is None or c.is_admin or c.role == "admin" or cur.get("set_by") == c.label:
            return
        raise Forbidden(
            f"name {cur['name']!r} was set by {cur.get('set_by') or 'someone else'}; an admin can change it"
        )

    def set_name(self, name: str, hash: str, **kw: Any) -> dict:
        c = need("runner")
        if c is not None:
            kw["set_by"] = c.label  # never what the client says
            cur = super().name(name)
            if cur is not None and (cur["hash"] != hash or kw.get("note") is not None):
                self._may_change_name(c, cur)
        return super().set_name(name, hash, **kw)

    def remove_name(self, name: str, *, expect: str | None = None, by: str | None = None) -> bool:
        c = need("runner")
        if c is not None:
            by = c.label
            self._may_change_name(c, super().name(name))
        return super().remove_name(name, expect=expect, by=by)

    def set_keep(self, exp_id: str, keep: bool) -> None:
        need("admin")
        super().set_keep(exp_id, keep)

    def delete_experiments(
        self, ids: list[str], refuse_status: Iterable[str] = ("running",), reparent: dict[str, str | None] | None = None
    ) -> None:
        self._own("experiment", ids)
        if reparent:  # re-parenting someone else's fork changes their experiment
            self._own("experiment", list(reparent))
        super().delete_experiments(ids, refuse_status=refuse_status, reparent=reparent)

    def delete_draft(self, exp_id: str) -> None:
        self._own("experiment", [exp_id])
        super().delete_draft(exp_id)

    def clear_node_runs(self, exp_id: str) -> None:
        self._own("experiment", [exp_id])
        super().clear_node_runs(exp_id)

    def set_node_stage(self, exp_id: str, node: str, path: list[dict]) -> None:
        self._own("experiment", [exp_id])
        super().set_node_stage(exp_id, node, path)

    def set_pipeline_view(self, pipeline: str, view: dict) -> None:
        need("runner")
        super().set_pipeline_view(pipeline, view)

    def record_read(self, exp_id: str, seq: int, artifact_hash: str, after: int) -> None:
        self._own("experiment", [exp_id])
        super().record_read(exp_id, seq, artifact_hash, after)

    def upsert_node_run(self, exp_id: str, node: str, **values: Any) -> None:
        self._own("experiment", [exp_id])
        super().upsert_node_run(exp_id, node, **values)

    # -- artifacts ---------------------------------------------------------------
    def register_artifacts(self, refs: Iterable[ArtifactRef]) -> None:
        refs = list(refs)
        if need("runner") is not None:
            self._committed(refs)
        super().register_artifacts(refs)

    def save_snapshot(self, snap: dict[str, Any]) -> None:
        need("runner")
        super().save_snapshot(snap)

    # -- executions --------------------------------------------------------------
    def start_execution(
        self, *, signature: str, node: str, spec: dict, cacheable: bool, experiment_id: str, task_run_id: str | None
    ) -> str:
        c = self._own("experiment", [experiment_id])
        exec_id = super().start_execution(
            signature=signature,
            node=node,
            spec=spec,
            cacheable=cacheable,
            experiment_id=experiment_id,
            task_run_id=task_run_id,
        )
        if c is not None:
            self.control.attribute(self.project, "execution", [exec_id], c)
        return exec_id

    def _own_execution(self, exec_id: str) -> None:
        self._own("execution", [exec_id])

    def set_progress(self, exec_id: str, done: float, total: float | None, unit: str | None) -> None:
        self._own_execution(exec_id)
        super().set_progress(exec_id, done, total, unit)

    def record_source(self, exec_id: str, snap: dict[str, Any]) -> None:
        self._own_execution(exec_id)
        super().record_source(exec_id, snap)

    def write_telemetry(self, exec_id: str, **kw: Any) -> None:
        self._own_execution(exec_id)
        super().write_telemetry(exec_id, **kw)

    def record_sample(self, exec_id: str, *, ref: ArtifactRef, **kw: Any) -> None:
        if self._own("execution", [exec_id]) is not None:
            self._committed([ref])
        super().record_sample(exec_id, ref=ref, **kw)

    def attach_log(self, exec_id: str, ref: ArtifactRef) -> None:
        if self._own("execution", [exec_id]) is not None:
            self._committed([ref])
        super().attach_log(exec_id, ref)

    def record_environment(self, exec_id: str, env_hash: str, data: dict[str, Any] | None) -> None:
        self._own_execution(exec_id)
        super().record_environment(exec_id, env_hash, data)

    def fail_execution(self, exec_id: str, error: str) -> None:
        self._own_execution(exec_id)
        super().fail_execution(exec_id, error)

    def publish(
        self,
        exec_id: str,
        *,
        experiment_id: str,
        node: str,
        inputs: dict[str, ArtifactRef],
        outputs: dict[str, ArtifactRef],
        metric_values: list[tuple[str, int, float]],
        node_run: dict[str, Any],
        lease: tuple[str, str] | None,
    ) -> None:
        c = self._own("experiment", [experiment_id])
        if c is not None:
            self._own_execution(exec_id)
            self._committed([*inputs.values(), *outputs.values()])
            if lease:
                lease = (lease[0], self._tag(lease[1]))
                held = [x for x in self.active_leases() if x["signature"] == lease[0]]
                if held and held[0]["owner"] != lease[1]:
                    raise Conflict("another worker holds the lease for this signature")
        super().publish(
            exec_id,
            experiment_id=experiment_id,
            node=node,
            inputs=inputs,
            outputs=outputs,
            metric_values=metric_values,
            node_run=node_run,
            lease=lease,
        )

    # -- pushed experiments (``dendrite push``) -------------------------------------------
    def import_bundle(self, bundle: dict[str, Any]) -> dict[str, Any]:
        c = need("runner")
        if c is not None:
            refs = [ArtifactRef(a["hash"], a["kind"], a["size"], a["filename"]) for a in bundle["artifacts"]]
            self._committed(refs)
            bundle = {**bundle, "names": [{**n, "set_by": c.label} for n in bundle.get("names", [])]}
        out = super().import_bundle(bundle)
        if c is not None:
            self.control.attribute(self.project, "experiment", out["created"], c)
            self.control.attribute(self.project, "execution", out["executions"], c)
        return out

    # -- leases ------------------------------------------------------------------
    def acquire_lease(self, signature: str, owner: str, ttl: float) -> bool:
        need("runner")
        return super().acquire_lease(signature, self._tag(owner), ttl)

    def renew_lease(self, signature: str, owner: str, ttl: float) -> bool:
        need("runner")
        return super().renew_lease(signature, self._tag(owner), ttl)

    def release_lease(self, signature: str, owner: str) -> None:
        need("runner")
        super().release_lease(signature, self._tag(owner))
