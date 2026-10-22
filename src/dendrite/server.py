"""FastAPI app: JSON lineage API plus the single-page UI."""

from __future__ import annotations

import pickle
from importlib import resources
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from .experiment import Experiment, Lab, _ref_dict
from .store import ArtifactRef
from .viz import lineage_mermaid, mermaid, tree_mermaid

PREVIEW_BYTES = 64 * 1024


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


def create_app(lab: Lab | None = None) -> FastAPI:
    lab = lab or Lab()
    app = FastAPI(title="dendrite", version="0.1.0")
    app.state.lab = lab

    @app.get("/api/info")
    def info() -> dict[str, Any]:
        return {"db": lab.db.engine.url.render_as_string(hide_password=True), "store": lab.store.url}

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
        return ex

    index = resources.files("dendrite") / "ui" / "index.html"

    @app.get("/", response_class=HTMLResponse)
    def spa() -> str:
        return index.read_text(encoding="utf-8")

    return app
