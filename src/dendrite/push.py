"""``dendrite push``: copy experiments from a local lab to a team server.

Runs made before a repo was pointed at a server live only in its local ``.dendrite``. Pushing
uploads the blobs the server's project lacks (deduplicated as usual), then recreates the
experiments, their node runs, executions and lineage there, attributed to you. Afterwards they
are cache hits for everyone: an execution the project already has for the same signature is
reused rather than copied.
"""

from __future__ import annotations

import pickle
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from .experiment import Lab


def push(
    source: Lab,
    target: Lab,
    keys: Iterable[str] | None = None,
    *,
    parents: bool = True,
    report: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Push the experiments named by ``keys`` (ids or names; all when ``None``) to ``target``."""
    if not target.hosted:
        raise ValueError("the target isn't a dendrite server: run `dendrite init`, or set DENDRITE_SERVER")
    rows = {r["id"]: r for r in source.db.experiments()}
    if keys is None:
        ids = list(rows)
    else:
        ids = [source.get(k).id for k in keys]
    if parents:  # bring the fork's history so its parent link survives
        for i in list(ids):
            up = rows[i]["parent_id"]
            while up in rows and up not in ids:
                ids.append(up)
                up = rows[up]["parent_id"]
    running = [i for i in ids if rows[i]["status"] == "running"]
    if running:
        report(f"skipping {len(running)} experiment(s) still running: {', '.join(running)}")
        ids = [i for i in ids if i not in running]
    if not ids:
        return {"created": [], "skipped": [], "executions": [], "reused": {}, "uploaded": 0}

    bundle = source.db.export_bundle(ids)
    bundle, files, previews = _blobs(source, bundle, report)
    size = sum(p.stat().st_size for _, p in files)
    report(f"uploading what the server lacks of {len(files)} blob(s), {size / 1e6:.1f} MB")
    target.store._put_missing(files, previews)
    out = target.db.import_bundle(bundle)
    report(
        f"pushed {len(out['created'])} experiment(s)"
        + (f", {len(out['skipped'])} already there" if out["skipped"] else "")
        + f"; {len(out['executions'])} execution(s) copied, {len(out['reused'])} matched ones the project already had"
    )
    return {**out, "uploaded": len(files)}


def _blobs(source: Lab, bundle: dict[str, Any], report) -> tuple[dict, list[tuple[str, Path]], dict[str, str]]:
    """Every blob the bundle needs, as local files. Executions whose outputs were evicted
    locally are dropped (their node runs stay, without an execution), since the server must
    never record a result it can't serve."""
    from .store import ArtifactRef

    store = source.store
    refs = {a["hash"]: ArtifactRef(a["hash"], a["kind"], a["size"], a["filename"]) for a in bundle["artifacts"]}
    needed: dict[str, set[str]] = {}  # artifact -> blobs it consists of
    for h, ref in refs.items():
        blobs = {h}
        if ref.kind == "dir" and store.has(h):
            blobs |= {m for _, m, _ in store.manifest(ref)}
        needed[h] = blobs
    every = set().union(*needed.values()) if needed else set()
    present = store.has_many(every)
    missing_art = {h for h, blobs in needed.items() if not blobs <= present}
    if missing_art:
        report(f"{len(missing_art)} artifact(s) are no longer stored locally; leaving out what depends on them")
        gone = {
            r["execution_id"]
            for r in bundle["execution_inputs"] + bundle["execution_outputs"]
            if r["artifact_hash"] in missing_art
        }
        bundle["executions"] = [e for e in bundle["executions"] if e["id"] not in gone]
        for key in ("execution_inputs", "execution_outputs", "metrics", "execution_sources"):
            bundle[key] = [r for r in bundle[key] if r["execution_id"] not in gone]
        for r in bundle["node_runs"]:
            if r["execution_id"] in gone:
                r["execution_id"] = None
        bundle["artifacts"] = [a for a in bundle["artifacts"] if a["hash"] not in missing_art]
        bundle["pipeline_reads"] = [r for r in bundle["pipeline_reads"] if r["artifact_hash"] not in missing_art]
    files = []
    for h in sorted(every & present):
        path = store.local_path(h) or store.materialize_file(h)
        files.append((h, path))
    previews = {}
    for a in bundle["artifacts"]:
        if a["kind"] == "value":  # this lab wrote these, so it may unpickle them; the server never does
            try:
                previews[a["hash"]] = repr(pickle.loads(store.read_bytes(a["hash"])))[:65536]
            except Exception:  # noqa: BLE001 - no preview, then
                pass
    return bundle, files, previews
