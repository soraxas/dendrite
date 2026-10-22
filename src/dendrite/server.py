"""FastAPI app: JSON lineage API plus the single-page UI."""

from __future__ import annotations

import pickle
from importlib import resources
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .experiment import Experiment, Lab, _ref_dict
from .sources import SourceConflict, disk_status, write_source
from .store import ArtifactRef
from .viz import lineage_mermaid, mermaid, tree_mermaid

PREVIEW_BYTES = 64 * 1024
UI_DIR = Path(str(resources.files("dendrite") / "ui"))


def ui_mode(ui_dir: Path = UI_DIR) -> str:
    """``react`` when the built bundle is present, otherwise the ``legacy`` single-file page."""
    return "react" if (ui_dir / "dist" / "index.html").is_file() else "legacy"


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

    @app.get("/api/info")
    def info() -> dict[str, Any]:
        return {"db": lab.db.engine.url.render_as_string(hide_password=True), "store": lab.store.url, "ui": mode, "edit": allow_edit}

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
    def download(key: str):
        ref = _artifact(lab, key)
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
        if not allow_edit:
            raise HTTPException(403, "editing is disabled; start `dendrite ui` on localhost or with --allow-edit")
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
        legacy = ui_dir / "legacy.html"

        @app.get("/", response_class=HTMLResponse)
        def spa() -> str:
            return legacy.read_text(encoding="utf-8")

    return app
