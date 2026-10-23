"""FastAPI app: JSON lineage API plus the single-page UI."""

from __future__ import annotations

import asyncio
import json
import pickle
import threading
import time
from importlib import resources
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import runtime
from .experiment import _PIPELINES, Experiment, Lab, NotADraft, _ref_dict
from .sources import SourceConflict, disk_status, write_source
from .storage import EvictRefused, StorageBusy
from .store import ArtifactRef
from .viz import lineage_mermaid, mermaid, tree_mermaid

PREVIEW_BYTES = 64 * 1024
UI_DIR = Path(str(resources.files("dendrite") / "ui"))


def ui_mode(ui_dir: Path = UI_DIR) -> str:
    """``react`` when the built UI bundle is present, otherwise ``missing`` (API only)."""
    return "react" if (ui_dir / "dist" / "index.html").is_file() else "missing"


UI_MISSING = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>dendrite</title></head>
<body style="font: 15px system-ui, sans-serif; max-width: 40rem; margin: 4rem auto; line-height: 1.5">
<h1>The dendrite UI isn't built</h1>
<p>This install has no UI bundle (for example a fresh clone). Build it with
<code>scripts/build-ui.sh</code>, or run <code>cd ui &amp;&amp; npm install &amp;&amp; npm run dev</code>
for the hot-reloading UI. The JSON API at <a href="/api/info"><code>/api</code></a> works either way.</p>
</body></html>"""


def _experiment(lab: Lab, key: str) -> Experiment:
    try:
        return lab.get(key)
    except KeyError:
        raise HTTPException(404, f"no experiment {key!r}") from None


def _artifact(lab: Lab, key: str) -> ArtifactRef:
    try:
        return lab.artifact(key)
    except KeyError:
        raise HTTPException(404, f"no artifact {key!r}") from None


def _preview(lab: Lab, ref: ArtifactRef) -> dict[str, Any]:
    store = lab.store
    if not store.verify(ref):
        return {"type": "missing"}
    if ref.kind == "dir":
        entries = store.manifest(ref)
        return {
            "type": "manifest",
            "count": len(entries),
            "entries": [{"path": p, "hash": h, "size": s} for p, h, s in entries[:500]],
        }
    if ref.size > PREVIEW_BYTES:
        return {"type": "too_large"}
    data = store.read_bytes(ref.hash)
    if ref.kind == "value":
        # Only values this lab produced end up in its store; never point the UI at an untrusted one.
        return {"type": "value", "repr": repr(pickle.loads(data))[:PREVIEW_BYTES]}
    try:
        return {"type": "text", "text": data.decode("utf-8")}
    except UnicodeDecodeError:
        return {"type": "binary"}


class SourceEdit(BaseModel):
    source: str
    file_sha: str


class ShapeRequest(BaseModel):
    source: str


class ForkRequest(BaseModel):
    name: str | None = None


class DraftEdit(BaseModel):
    """Values are typed JSON and hashed exactly, so 30000 and 30000.0 are different params."""

    params: dict[str, Any] | None = None  # "node.arg" -> value
    reset: list[str] = []  # "node.arg" keys to return to the parent's value
    pins: dict[str, str | None] | None = None  # node -> "parent" (freeze to parent's result) | None
    inputs: dict[str, str] | None = None  # input name -> artifact hash or prefix
    name: str | None = None
    # JSON (and JavaScript) can't tell 1 from 1.0; keys listed here are float params, so whole
    # numbers sent for them are stored as floats and keep the signature a run would compute.
    floats: list[str] = []


class BulkDelete(BaseModel):
    ids: list[str]
    forks: str = "refuse"  # refuse | delete | keep (forks become forks of the nearest kept ancestor)
    force: bool = False  # also experiments stuck as running


class EstimateRequest(BaseModel):
    ids: list[str]
    forks: str = "keep"


class CleanRequest(BaseModel):
    force: bool = False


STAGE_COLORS = ("teal", "blue", "purple", "orange", "green", "pink", "red", "gray")


class StageStyle(BaseModel):
    label: str | None = None
    description: str | None = None
    color: str | None = None


class PipelineView(BaseModel):
    """A pipeline's UI view, overlaying its code: never changes code or signatures."""

    assign: dict[str, str] = {}  # step name -> stage, for steps the code leaves ungrouped
    styles: dict[str, StageStyle] = {}  # stage -> label/description/colour


class KeepRequest(BaseModel):
    keep: bool = True


class EvictRequest(BaseModel):
    force: bool = False
    apply: bool = False  # otherwise a dry run


class GcRequest(BaseModel):
    older_than_days: float | None = None
    force: bool = False
    apply: bool = False  # otherwise a dry run
    hashes: list[str] | None = None  # apply only these: the hashes of the previewed report
    # Skip the grace period. Safe when nothing runs: applying still refuses while work is in progress.
    include_recent: bool = False


EVENT_INTERVAL = 0.25  # seconds between change checks for /api/events
EVENT_HEARTBEAT = 15.0  # seconds between keep-alive comments when nothing changes
STORAGE_TTL = 30.0  # seconds the inventory is cached (listing a large bucket is slow)

EDIT_DISABLED = "editing is disabled; start `dendrite ui` on localhost or with --allow-edit"


def _display_path(file: str | None) -> str | None:
    """Path relative to the server's working directory when it lives under it."""
    if not file:
        return None
    try:
        return str(Path(file).relative_to(Path.cwd()))
    except ValueError:
        return file


def create_app(lab: Lab | None = None, ui_dir: Path = UI_DIR, allow_edit: bool = False) -> FastAPI:
    """``allow_edit`` lets the UI write edited step source back to its file (see sources.py)."""
    lab = lab or Lab()
    mode = ui_mode(ui_dir)
    app = FastAPI(title="dendrite", version="0.1.0")
    app.state.lab = lab
    app.state.closing = threading.Event()  # set on shutdown; ends live-update streams

    @app.get("/api/info")
    def info() -> dict[str, Any]:
        return {
            "db": lab.db.engine.url.render_as_string(hide_password=True),
            "store": lab.store.url,
            "ui": mode,
            "edit": allow_edit,
            "plan": bool(_PIPELINES),
            "pipelines": sorted(_PIPELINES),
        }

    def require_edit() -> None:
        if not allow_edit:
            raise HTTPException(403, EDIT_DISABLED)

    @app.post("/api/experiments/{exp_id}/fork")
    def fork(exp_id: str, req: ForkRequest) -> dict[str, Any]:
        require_edit()
        return _experiment(lab, exp_id).fork(draft=True, name=req.name or None).summary()

    @app.patch("/api/experiments/{exp_id}")
    def edit_draft(exp_id: str, req: DraftEdit) -> dict[str, Any]:
        require_edit()
        exp = _experiment(lab, exp_id)
        pin: dict[str, Any] = {}
        for node, how in (req.pins or {}).items():
            if how is None:
                pin[node] = None
            elif how == "parent" and exp.parent is not None:
                pin[node] = exp.parent
            else:
                raise HTTPException(400, f"pin for {node!r} must be 'parent' (with a parent experiment) or null")
        params = dict(req.params or {})
        for key in req.floats:
            if isinstance(params.get(key), int) and not isinstance(params[key], bool):
                params[key] = float(params[key])
        try:
            inputs = {k: _artifact(lab, v) for k, v in (req.inputs or {}).items()}
            return exp.update(params, pin=pin, inputs=inputs, name=req.name, reset=req.reset).summary()
        except NotADraft as e:
            raise HTTPException(409, str(e)) from None
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    @app.delete("/api/experiments/{exp_id}")
    def delete_experiment(exp_id: str, forks: str = "refuse") -> dict[str, Any]:
        """Delete an experiment that isn't running. ``?forks=delete`` (or ``1``) deletes its
        forks too; ``?forks=keep`` keeps them as forks of its parent."""
        require_edit()
        exp = _experiment(lab, exp_id)
        try:
            out = lab.delete([exp_id], forks=forks)
        except ValueError as e:
            raise HTTPException(409, str(e)) from None
        storage_cache.clear()
        return {"deleted": exp_id, "ids": out["deleted"], "reparented": out["reparented"], "parent_id": exp.parent_id}

    @app.post("/api/experiments/delete")
    def delete_many(req: BulkDelete) -> dict[str, Any]:
        """Delete several experiments in one transaction."""
        require_edit()
        try:
            out = lab.delete(req.ids, forks=req.forks, force=req.force)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0])) from None
        except ValueError as e:
            raise HTTPException(409, str(e)) from None
        finally:
            storage_cache.clear()
        return out

    @app.post("/api/storage/estimate")
    def estimate(req: EstimateRequest) -> dict[str, Any]:
        """What deleting these experiments would free after clean-up (read-only)."""
        return lab.deletion_estimate(req.ids, forks=req.forks)

    @app.post("/api/experiments/{exp_id}/stop")
    def mark_stopped(exp_id: str) -> dict[str, Any]:
        """Mark an experiment left ``running`` by a crashed process as failed."""
        require_edit()
        try:
            out = _experiment(lab, exp_id).mark_stopped().summary()
        except ValueError as e:
            raise HTTPException(409, str(e)) from None
        storage_cache.clear()
        return out

    storage_cache: dict[str, Any] = {}

    def busy_or_refused(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except StorageBusy as e:
            raise HTTPException(409, {"code": "busy", "message": str(e)}) from None
        except EvictRefused as e:
            raise HTTPException(409, {"code": "pinned", "message": str(e)}) from None
        finally:
            storage_cache.clear()

    @app.get("/api/storage")
    def storage(refresh: bool = False) -> dict[str, Any]:
        hit = storage_cache.get("inventory")
        if refresh or hit is None or time.time() - hit["computed_at"] > STORAGE_TTL:
            hit = storage_cache["inventory"] = lab.storage()
        return hit

    @app.post("/api/experiments/{exp_id}/keep")
    def keep(exp_id: str, req: KeepRequest) -> dict[str, Any]:
        require_edit()
        storage_cache.clear()
        return _experiment(lab, exp_id).keep(req.keep).summary()

    @app.post("/api/executions/{exec_id}/evict")
    def evict(exec_id: str, req: EvictRequest) -> dict[str, Any]:
        if lab.db.execution(exec_id) is None:
            raise HTTPException(404, f"no execution {exec_id!r}")
        if req.apply:
            require_edit()
        return busy_or_refused(lab.evict, exec_id, dry_run=not req.apply, force=req.force)

    @app.post("/api/artifacts/{key}/evict")
    def evict_artifact(key: str, req: EvictRequest) -> dict[str, Any]:
        ref = _artifact(lab, key)
        if req.apply:
            require_edit()
        return busy_or_refused(lab.evict, ref.hash, dry_run=not req.apply, force=req.force)

    @app.post("/api/gc")
    def gc(req: GcRequest) -> dict[str, Any]:
        if req.apply:
            require_edit()
        older = req.older_than_days * 86400 if req.older_than_days is not None else None
        grace = 0.0 if req.include_recent else None
        return busy_or_refused(lab.gc, older, dry_run=not req.apply, force=req.force, hashes=req.hashes, grace=grace)

    @app.post("/api/storage/clean")
    def clean(req: CleanRequest | None = None) -> dict[str, Any]:
        require_edit()
        return busy_or_refused(lab.clean, force=bool(req and req.force))

    def pipeline(name: str):
        runtime.refresh_modules()  # a saved edit (here or on disk) reloads before it is shown
        pipe = _PIPELINES.get(name)
        if pipe is None:
            raise HTTPException(
                404,
                {"code": "pipeline_not_imported", "message": f"pipeline {name!r} is not loaded; restart with `dendrite ui --import <module that defines it>`"},
            )
        return pipe

    def shape_response(pipe, source: str | None = None) -> dict[str, Any]:
        out = pipe.shape(source)
        steps = out.pop("_steps")
        # Step-definition findings for the steps this pipeline calls, located in their files.
        out["step_diagnostics"] = [d.as_dict() for s in steps.values() for d in getattr(s, "diagnostics", [])]
        out["display"] = _display_path(out["file"])
        return out

    @app.get("/api/pipelines/{name}/view")
    def get_view(name: str) -> dict[str, Any]:
        return lab.db.pipeline_view(name)

    @app.put("/api/pipelines/{name}/view")
    def put_view(name: str, view: PipelineView) -> dict[str, Any]:
        require_edit()
        bad = [s.color for s in view.styles.values() if s.color and s.color not in STAGE_COLORS]
        if bad:
            raise HTTPException(400, f"unknown stage colour {bad[0]!r}; use one of {', '.join(STAGE_COLORS)}")
        if any(not k.strip() or not v.strip() for k, v in view.assign.items()):
            raise HTTPException(400, "stage and step names must not be empty")
        doc = view.model_dump(exclude_none=True)
        lab.db.set_pipeline_view(name, doc)
        return doc

    @app.get("/api/pipelines/{name}/shape")
    def shape(name: str) -> dict[str, Any]:
        pipe = pipeline(name)
        out = shape_response(pipe)
        snap = pipe.source_snapshot()
        if snap:
            lab.db.save_snapshot(snap)  # so /api/sources/{hash} can show and (if allowed) save it
        out["source_hash"] = snap["hash"] if snap else None
        return out

    @app.post("/api/pipelines/{name}/shape")
    def shape_of_text(name: str, req: ShapeRequest) -> dict[str, Any]:
        """Shape and diagnostics for edited source; parsed only, never executed or saved."""
        return shape_response(pipeline(name), req.source)

    @app.get("/api/experiments/{exp_id}/check")
    def check(exp_id: str) -> list[dict[str, Any]]:
        exp = _experiment(lab, exp_id)
        pipeline(exp.pipeline_name)
        return exp.check()

    @app.get("/api/experiments/{exp_id}/plan")
    def plan(exp_id: str) -> dict[str, Any]:
        exp = _experiment(lab, exp_id)
        if exp.pipeline_name not in _PIPELINES:
            raise HTTPException(
                409,
                {
                    "code": "pipeline_not_imported",
                    "message": f"pipeline {exp.pipeline_name!r} is not loaded; restart with "
                    f"`dendrite ui --import <module that defines it>` to plan and edit",
                },
            )
        out = exp.plan()
        module = _PIPELINES[exp.pipeline_name].fn.__module__
        out["module"] = None if module == "__main__" else module
        return out

    @app.get("/api/events")
    async def events(request: Request, limit: int | None = None) -> StreamingResponse:
        """Server-Sent Events: ``{"tree": bool, "experiments": [ids]}`` whenever something changed.

        Runs usually happen in another process, so changes are detected by fingerprinting the
        database (``Metadata.change_tokens``) every ``EVENT_INTERVAL``. Clients refetch only what
        an event names. ``limit`` ends the stream after that many events (for tests and curl).
        """

        async def stream():
            tree, per = await run_in_threadpool(lab.db.change_tokens)
            yield "retry: 2000\ndata: " + json.dumps({"hello": True}) + "\n\n"
            sent, quiet = 0, 0.0
            while limit is None or sent < limit:
                await asyncio.sleep(EVENT_INTERVAL)
                # End when the client leaves or the server is stopping (so Ctrl+C doesn't wait on us).
                if app.state.closing.is_set() or await request.is_disconnected():
                    return
                new_tree, new_per = await run_in_threadpool(lab.db.change_tokens)
                changed = sorted(k for k in set(per) | set(new_per) if per.get(k) != new_per.get(k))
                if changed or new_tree != tree:
                    tree, per = new_tree, new_per
                    sent += 1
                    quiet = 0.0
                    yield "data: " + json.dumps({"tree": True, "experiments": changed}) + "\n\n"
                else:
                    quiet += EVENT_INTERVAL
                    if quiet >= EVENT_HEARTBEAT:
                        quiet = 0.0
                        yield ": keep-alive\n\n"

        headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)

    @app.get("/api/experiments")
    def experiments() -> list[dict[str, Any]]:
        return lab.tree()

    @app.get("/api/tree/mermaid")
    def tree_text() -> dict[str, str]:
        return {"mermaid": tree_mermaid(lab.tree())}

    @app.get("/api/experiments/{exp_id}")
    def experiment(exp_id: str) -> dict[str, Any]:
        exp = _experiment(lab, exp_id)
        out = exp.summary()
        out["node_runs"] = exp.node_runs()
        out["metrics"] = exp.metrics()
        out["children"] = [e["id"] for e in lab.tree() if e["parent_id"] == exp.id]
        return out

    @app.get("/api/experiments/{exp_id}/dag")
    def dag(exp_id: str) -> dict[str, Any]:
        d = _experiment(lab, exp_id).dag()
        d["mermaid"] = mermaid(d)
        return d

    @app.get("/api/diff")
    def diff(a: str, b: str) -> dict[str, Any]:
        return _experiment(lab, a).diff(_experiment(lab, b))

    @app.get("/api/artifacts/{key}")
    def artifact(key: str) -> dict[str, Any]:
        ref = _artifact(lab, key)
        producer = lab.db.producer_of(ref.hash)
        return {
            **_ref_dict(ref),
            "present": lab.store.verify(ref),
            "producer": lab.db.execution(producer) if producer else None,
            "experiments": lab.db.experiments_using_artifact(ref.hash),
            "preview": _preview(lab, ref),
        }

    @app.get("/api/artifacts/{key}/lineage")
    def lineage(key: str) -> dict[str, Any]:
        g = lab.lineage(_artifact(lab, key))
        g["mermaid"] = lineage_mermaid(g)
        return g

    @app.get("/api/artifacts/{key}/download")
    def download(key: str, path: str | None = None):
        ref = _artifact(lab, key)
        if ref.kind == "dir" and path is not None:
            if not lab.store.has(ref.hash):
                raise HTTPException(410, "artifact blob is missing from the store")
            # Match the manifest before touching the filesystem; arbitrary paths are never served.
            member = next((h for p, h, _ in lab.store.manifest(ref) if p == path), None)
            if member is None:
                raise HTTPException(404, "no such file in the directory artifact")
            if not lab.store.has(member):
                raise HTTPException(410, "artifact blob is missing from the store")
            return FileResponse(lab.store.materialize_file(member), filename=Path(path).name)
        if ref.kind != "file":
            raise HTTPException(400, "only file artifacts can be downloaded")
        if not lab.store.verify(ref):
            raise HTTPException(410, "artifact blob is missing from the store")
        return FileResponse(lab.store.load(ref), filename=ref.filename or ref.hash)

    @app.get("/api/executions/{exec_id}")
    def execution(exec_id: str) -> dict[str, Any]:
        ex = lab.db.execution(exec_id)
        if ex is None:
            raise HTTPException(404, f"no execution {exec_id!r}")
        # Icon metadata comes from one DB query; never materialize or verify blobs for chips.
        hashes = set(ex["inputs"].values()) | set(ex["outputs"].values())
        ex["artifacts"] = {h: _ref_dict(ref) for h, ref in lab.db.artifacts_by_hash(hashes).items()}
        ex["used_by"] = lab.db.node_runs_for_execution(exec_id)
        src = lab.db.source_for_execution(exec_id)
        ex["source"] = {**src, "display": _display_path(src["file"])} if src else None
        return ex

    @app.get("/api/sources/{source_hash}")
    def source(source_hash: str) -> dict[str, Any]:
        snap = lab.db.snapshot(source_hash)
        if snap is None:
            raise HTTPException(404, f"no source snapshot {source_hash!r}")
        disk = disk_status(snap)
        return {
            **{k: snap[k] for k in ("hash", "source", "file", "line", "module", "qualname")},
            "display": _display_path(snap["file"]),
            **disk,
            "editable": allow_edit and disk["status"] == "same",
        }

    @app.put("/api/sources/{source_hash}")
    def save_source(source_hash: str, edit: SourceEdit) -> dict[str, Any]:
        require_edit()
        snap = lab.db.snapshot(source_hash)
        if snap is None:
            raise HTTPException(404, f"no source snapshot {source_hash!r}")
        try:
            return write_source(snap, edit.source, edit.file_sha)
        except SourceConflict as e:
            raise HTTPException(409, str(e)) from None

    if mode == "react":
        # Mounted last so every /api route above takes precedence; the SPA uses hash routing,
        # so only "/" and the hashed assets need serving.
        app.mount("/", StaticFiles(directory=ui_dir / "dist", html=True), name="ui")
    else:

        @app.get("/", response_class=HTMLResponse)
        def ui_missing() -> str:
            return UI_MISSING

    return app
