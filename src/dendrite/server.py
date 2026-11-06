"""FastAPI app: JSON lineage API plus the single-page UI."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import pickle
import re
import threading
import time
from importlib import resources
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import runtime
from .db import NameConflict
from .experiment import _PIPELINES, Experiment, Lab, NotADraft, _ref_dict
from .sources import SourceConflict, disk_status, write_source
from .storage import EvictRefused, StorageBusy
from .store import ArtifactRef, ArtifactStore, content_disposition
from .viz import lineage_mermaid, mermaid, tree_mermaid

PREVIEW_BYTES = 64 * 1024
# Remote stores: files above this wait for "Load preview", because previewing downloads them.
AUTO_PREVIEW_BYTES = 8 * 1024 * 1024
SIGNED_URL_SECONDS = 15 * 60
STREAM_CHUNK = 1024 * 1024
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


LOG_TAIL_BYTES = 256 * 1024  # how much of a stored log the console shows


def _read_tail(lab: Lab, h: str, size: int, n: int) -> str:
    """The last ``n`` bytes of a stored text blob, starting at a line boundary."""
    start = max(0, size - n)
    local = lab.store.local_path(h)
    if local is not None:
        with open(local, "rb") as f:
            f.seek(start)
            data = f.read()
    else:
        with lab.store.open_object(h) as f:
            if start:
                f.seek(start)
            data = f.read()
    text = data.decode("utf-8", "replace")
    if start and "\n" in text:
        text = f"… {start} earlier bytes: download the log for all of it …\n" + text.split("\n", 1)[1]
    return text


def _hashes_in(obj: Any, out: set[str] | None = None) -> set[str]:
    """Every artifact hash mentioned in a response (``hash``, or a lineage node's ``id``)."""
    out = set() if out is None else out
    if isinstance(obj, dict):
        for k in ("hash", "id"):
            h = obj.get(k)
            if isinstance(h, str) and len(h) == 64:
                out.add(h)
        for v in obj.values():
            _hashes_in(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _hashes_in(v, out)
    return out


def _with_names(lab: Lab, payload: dict[str, Any]) -> dict[str, Any]:
    payload["names"] = lab.db.names_for(_hashes_in(payload))
    return payload


def _artifact(lab: Lab, key: str) -> ArtifactRef:
    try:
        return lab.artifact(key)
    except KeyError:
        raise HTTPException(404, f"no artifact {key!r}") from None


def _preview(lab: Lab, ref: ArtifactRef, present: bool, hosted: Any = None) -> dict[str, Any]:
    store = lab.store
    if not present:
        return {"type": "missing"}
    if ref.kind == "value" and hosted is not None:
        # A hosted server never unpickles: values carry the text their producer recorded.
        text = hosted.value_preview(ref.hash)
        return {"type": "value", "repr": text if text is not None else "(no preview was recorded for this value)"}
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


def _stream(store: ArtifactStore, h: str, size: int, name: str, media_type: str, range_header: str | None):
    """Stream object ``h`` from the store, or the single byte range a media player asked for."""
    start, end, status = 0, size - 1, 200
    if m := re.fullmatch(r"bytes=(\d*)-(\d*)", (range_header or "").strip()):
        first, last = m.groups()
        if first:
            start, end = int(first), min(int(last), size - 1) if last else size - 1
        elif last:
            start = max(0, size - int(last))
        if (first or last) and start <= end:
            status = 206
        elif first or last:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})

    def body():
        with store.open_object(h) as f:
            f.seek(start)
            left = end - start + 1
            while left > 0 and (chunk := f.read(min(STREAM_CHUNK, left))):
                left -= len(chunk)
                yield chunk

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(max(0, end - start + 1)),
        "Content-Disposition": content_disposition(name),
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(body(), status_code=status, headers=headers, media_type=media_type)


class SourceEdit(BaseModel):
    source: str
    file_sha: str


class ShapeRequest(BaseModel):
    source: str


class ForkRequest(BaseModel):
    name: str | None = None


class NameRequest(BaseModel):
    """What to name: an experiment's step output or input, a hash, or a CLI-style ``target``."""

    experiment: str | None = None
    node: str | None = None
    output: str | None = None
    hash: str | None = None
    target: str | None = None
    note: str | None = None
    tags: list[str] | None = None
    move: bool = False
    expect: str | None = None  # compare-and-set on the name's current target


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


def _db_label(lab: Lab) -> str:
    engine = getattr(lab.db, "engine", None)
    if engine is None:  # a hosted project, reached through its server
        return f"{lab.settings.server} (project {lab.settings.project})"
    return engine.url.render_as_string(hide_password=True)


def create_app(
    lab: Lab | None = None,
    ui_dir: Path = UI_DIR,
    allow_edit: bool = False,
    presign: bool | None = None,
    *,
    source_edit: bool | None = None,
    hosted: Any = None,
) -> FastAPI:
    """``allow_edit`` lets the UI change things: drafts, deletions, storage and step source.
    ``source_edit`` (default: ``allow_edit``) separately allows writing step source back to
    its file (see sources.py).

    ``hosted`` is set when a dendrite server mounts this app for a project: it supplies the
    signed-in user (``whoami(request)``) and producer-recorded value previews, and the
    project's metadata enforces roles and ownership itself.

    ``presign`` (default: ``DENDRITE_PRESIGN``, on unless ``0``) lets downloads from a remote
    store redirect to a signed URL. Turn it off when browsers can't reach the store's endpoint.
    """
    lab = lab or Lab()
    if source_edit is None:
        source_edit = allow_edit
    if presign is None:
        presign = os.environ.get("DENDRITE_PRESIGN", "1") != "0"
    mode = ui_mode(ui_dir)
    app = FastAPI(title="dendrite", version="0.1.0")
    app.state.lab = lab
    app.state.closing = threading.Event()  # set on shutdown; ends live-update streams

    @app.exception_handler(PermissionError)
    async def forbidden(_: Request, e: PermissionError):
        return JSONResponse({"detail": str(e)}, status_code=403)

    @app.get("/api/info")
    def info(request: Request) -> dict[str, Any]:
        out = {
            "db": "server" if hosted else _db_label(lab),
            "store": "server" if hosted else lab.store.url,
            "ui": mode,
            "edit": allow_edit,
            "source_edit": source_edit,
            # A hosted server shares no pipeline code with its clients, and never runs any.
            "plan": bool(_PIPELINES) and hosted is None,
            "pipelines": [] if hosted is not None else sorted(_PIPELINES),
        }
        if hosted is not None:
            out["hosted"] = hosted.whoami(request)
        return out

    def require_edit() -> None:
        if not allow_edit:
            raise HTTPException(403, EDIT_DISABLED)

    # -- artifact names (metadata only) -------------------------------------------------
    @app.get("/api/names")
    def names() -> dict[str, Any]:
        return {"names": lab.names()}

    @app.put("/api/names/{name}")
    def set_name(name: str, req: NameRequest) -> dict[str, Any]:
        require_edit()
        if req.hash:
            target = req.hash
        elif req.experiment and req.node:
            target = f"{req.experiment}:{req.node}" + (f".{req.output}" if req.output else "")
        elif req.experiment and req.output:
            target = f"{req.experiment}:{req.output}"
        elif req.target:
            target = req.target
        else:
            raise HTTPException(422, "say what to name: experiment and node/output, hash, or target")
        try:
            return lab.name(name, target, note=req.note, tags=req.tags, move=req.move, expect=req.expect)
        except NameConflict as e:
            raise HTTPException(409, {"message": str(e).split(": ", 1)[-1], "code": e.code}) from None
        except KeyError as e:
            raise HTTPException(404, str(e.args[0] if e.args else e)) from None
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

    @app.delete("/api/names/{name}")
    def remove_name(name: str, expect: str | None = None) -> dict[str, Any]:
        require_edit()
        try:
            removed = lab.unname(name, expect=expect)
        except NameConflict as e:
            raise HTTPException(409, {"message": str(e).split(": ", 1)[-1], "code": e.code}) from None
        if not removed:
            raise HTTPException(404, f"no name {name!r}")
        return {"removed": name}

    @app.get("/api/names/{name}/history")
    def name_history(name: str) -> dict[str, Any]:
        return {"name": lab.db.name(name), "history": lab.db.name_history(name)}

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
        if hosted is not None:
            raise HTTPException(
                409,
                {
                    "code": "pipeline_not_imported",
                    "message": "a hosted server never runs pipeline code: plan and check drafts where the code lives "
                    "(Experiment.plan()), or with `dendrite ui --import …` on your machine",
                },
            )
        runtime.refresh_modules()  # a saved edit (here or on disk) reloads before it is shown
        pipe = _PIPELINES.get(name)
        if pipe is None:
            raise HTTPException(
                404,
                {
                    "code": "pipeline_not_imported",
                    "message": f"pipeline {name!r} is not loaded; restart with `dendrite ui --import <module that defines it>`",
                },
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
        if hosted is not None:
            raise HTTPException(
                409,
                {
                    "code": "pipeline_not_imported",
                    "message": "a hosted server never runs pipeline code: plan and check drafts where the code lives "
                    "(Experiment.plan()), or with `dendrite ui --import …` on your machine",
                },
            )
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
        return _with_names(lab, d)

    @app.get("/api/compare")
    def compare(base: str, node: str, output: str, exps: str = "") -> dict[str, Any]:
        """One step output across experiments: each one's artifact and its parameter changes from
        ``base``. Cheaper than a DAG per experiment: node runs and params only."""
        b = _experiment(lab, base)
        ids = [i for i in dict.fromkeys([b.id, *exps.split(",")]) if i][:16]
        columns = []
        hashes = {}
        for eid in ids:
            if eid == b.id:
                e = b
            else:
                row = lab.db.experiment(eid)
                if row is None:
                    continue
                e = Experiment(lab, row)
            h = e.node_outputs(node).get(output)
            hashes[eid] = h
            if e.id == b.id:
                relation = "self"
            elif e.id == b.parent_id:
                relation = "parent"
            else:
                relation = "fork" if e.parent_id == b.id else "other"
            columns.append(
                {
                    "experiment": e.id,
                    "name": e.name,
                    "relation": relation,
                    "hash": h,
                    "params": [] if e.id == b.id else b.diff_params(e),
                }
            )
        refs = lab.db.artifacts_by_hash(h for h in hashes.values() if h)
        for c in columns:
            c["artifact"] = _ref_dict(refs[c["hash"]]) if c["hash"] in refs else None
        return {"node": node, "output": output, "columns": columns}

    @app.get("/api/diff")
    def diff(a: str, b: str) -> dict[str, Any]:
        return _experiment(lab, a).diff(_experiment(lab, b))

    @app.get("/api/artifacts/{key}")
    def artifact(key: str) -> dict[str, Any]:
        ref = _artifact(lab, key)
        producer = lab.db.producer_of(ref.hash)
        # One request per member is too slow on S3 for a page view; downloads still 410 a missing one.
        present = lab.store.verify(ref, deep=not lab.store.remote)
        return {
            **_ref_dict(ref),
            "present": present,
            "producer": lab.db.execution(producer) if producer else None,
            "experiments": lab.db.experiments_using_artifact(ref.hash),
            "preview": _preview(lab, ref, present, hosted),
            "auto_preview_bytes": AUTO_PREVIEW_BYTES if lab.store.remote else None,
            "names": lab.db.names_for([ref.hash]),
        }

    @app.get("/api/artifacts/{key}/lineage")
    def lineage(key: str) -> dict[str, Any]:
        g = lab.lineage(_artifact(lab, key))
        g["mermaid"] = lineage_mermaid(g)
        return _with_names(lab, g)

    @app.get("/api/artifacts/{key}/download")
    def download(request: Request, key: str, path: str | None = None, redirect: bool = False):
        """Serve a file artifact or a directory member.

        Local blobs are served from disk. Remote ones stream as they're read, honouring byte
        ranges, so nothing is downloaded whole first. With ``redirect`` (links and media, which
        don't need CORS) a store that can sign sends the browser straight to the object.
        """
        ref = _artifact(lab, key)
        if ref.kind == "dir" and path is not None:
            if not lab.store.has(ref.hash):
                raise HTTPException(410, "artifact blob is missing from the store")
            # Match the manifest before touching the filesystem; arbitrary paths are never served.
            member = next(((h, s) for p, h, s in lab.store.manifest(ref) if p == path), None)
            if member is None:
                raise HTTPException(404, "no such file in the directory artifact")
            h, size, name = member[0], member[1], Path(path).name
        elif ref.kind == "file":
            h, size, name = ref.hash, ref.size, ref.filename or ref.hash
        else:
            raise HTTPException(400, "only file artifacts can be downloaded")
        if not lab.store.has(h):
            raise HTTPException(410, "artifact blob is missing from the store")
        media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        local = lab.store.local_path(h)
        if local is not None:
            return FileResponse(local, filename=name, media_type=media_type)
        if redirect and presign and (url := lab.store.signed_url(h, name, SIGNED_URL_SECONDS)):
            return RedirectResponse(url, status_code=307)
        return _stream(lab.store, h, size, name, media_type, request.headers.get("range"))

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

    # -- run telemetry (display only) -------------------------------------------------
    def _exec_or_404(exec_id: str) -> dict[str, Any]:
        ex = lab.db.execution(exec_id)
        if ex is None:
            raise HTTPException(404, f"no execution {exec_id!r}")
        return ex

    @app.get("/api/executions/{exec_id}/logs")
    def execution_logs(exec_id: str, after: int = -1, limit: int = 2000) -> dict[str, Any]:
        ex = _exec_or_404(exec_id)
        out: dict[str, Any] = {"running": ex["status"] == "running", "status": ex["status"], "stored": None}
        blob = lab.db.log_blob(exec_id)
        if blob is None:
            out["chunks"] = lab.db.logs(exec_id, after=after, limit=max(1, min(limit, 5000)))
            return out
        # A big finished log lives in the store: send its end; the whole file is a download away.
        out["stored"] = {"hash": blob["hash"], "size": blob["size"], "shown": min(blob["size"], LOG_TAIL_BYTES)}
        out["chunks"] = []
        if after < 0:
            text = _read_tail(lab, blob["hash"], blob["size"], LOG_TAIL_BYTES)
            out["chunks"] = [{"seq": 0, "stream": "stdout", "t": ex["finished_at"] or 0, "text": text}]
        return out

    @app.get("/api/executions/{exec_id}/scalars")
    def execution_scalars(exec_id: str) -> dict[str, Any]:
        _exec_or_404(exec_id)
        return {"points": lab.db.scalars([exec_id]).get(exec_id, [])}

    @app.get("/api/executions/{exec_id}/samples")
    def execution_samples(exec_id: str) -> dict[str, Any]:
        _exec_or_404(exec_id)
        return {"samples": lab.db.samples(exec_id)}

    @app.get("/api/executions/{exec_id}/environment")
    def execution_environment(exec_id: str) -> dict[str, Any]:
        _exec_or_404(exec_id)
        return {"environment": lab.db.environment(exec_id)}

    @app.get("/api/experiments/{exp_id}/scalars")
    def experiment_scalars(exp_id: str) -> dict[str, Any]:
        exp = lab.db.experiment(exp_id)
        if exp is None:
            raise HTTPException(404, f"no experiment {exp_id!r}")
        runs = [nr for nr in lab.db.node_runs(exp["id"]) if nr.get("execution_id")]
        points = lab.db.scalars(nr["execution_id"] for nr in runs)
        return {
            "nodes": [
                {"node": nr["node"], "execution_id": nr["execution_id"], "points": points.get(nr["execution_id"], [])}
                for nr in runs
                if points.get(nr["execution_id"])
            ]
        }

    @app.get("/api/sources/{source_hash}")
    def source(source_hash: str) -> dict[str, Any]:
        snap = lab.db.snapshot(source_hash)
        if snap is None:
            raise HTTPException(404, f"no source snapshot {source_hash!r}")
        # A hosted server never opens a path a client recorded: the file is on the runner's machine.
        disk = {"status": "elsewhere", "file_sha": None} if hosted is not None else disk_status(snap)
        return {
            **{k: snap[k] for k in ("hash", "source", "file", "line", "module", "qualname")},
            "display": _display_path(snap["file"]),
            **disk,
            "editable": source_edit and disk["status"] == "same",
        }

    @app.put("/api/sources/{source_hash}")
    def save_source(source_hash: str, edit: SourceEdit) -> dict[str, Any]:
        if not source_edit:
            raise HTTPException(403, EDIT_DISABLED)
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
