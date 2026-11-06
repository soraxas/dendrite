"""The remote metadata protocol: which ``Metadata`` methods a client may call, and the JSON codec.

Python's ``json`` keeps ``1`` and ``1.0`` distinct, so params round-trip exactly. Values JSON
can't express are tagged: ``ArtifactRef``, ``bytes``, tuples and sets.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterable
from typing import Any

from ..store import ArtifactRef

API_VERSION = "1"
TAG = "$dendrite"

# Methods a client may call, and the role each needs. ``ServerMetadata`` adds the per-caller
# checks (ownership, lease binding, committed blobs); anything not listed is refused.
READS = {
    "artifact",
    "resolve_artifact_prefix",
    "artifacts_by_hash",
    "experiment",
    "experiments",
    "node_stages",
    "pipeline_view",
    "reads",
    "progress_for",
    "change_tokens",
    "node_runs",
    "node_runs_for_execution",
    "experiments_using_artifact",
    "all_node_runs",
    "all_artifacts",
    "successful_executions",
    "running_executions",
    "active_leases",
    "latest_success_for_node",
    "find_success",
    "snapshot",
    "source_for_execution",
    "execution",
    "executions_brief",
    "metrics_for",
    "producer_of",
    "ancestor_executions",
    "descendant_executions",
    "logs",
    "scalars",
    "samples",
    "sample_hashes",
    "environment",
}
WRITES = {
    "register_artifacts",
    "create_experiment",
    "update_experiment",
    "update_experiment_if",
    "delete_experiments",
    "delete_draft",
    "clear_node_runs",
    "set_node_stage",
    "set_pipeline_view",
    "record_read",
    "set_progress",
    "upsert_node_run",
    "start_execution",
    "publish",
    "record_source",
    "save_snapshot",
    "fail_execution",
    "acquire_lease",
    "renew_lease",
    "release_lease",
    "import_bundle",
    "write_telemetry",
    "record_sample",
    "record_environment",
}
ADMIN = {"set_keep"}
REMOTE = {**dict.fromkeys(READS, "viewer"), **dict.fromkeys(WRITES, "runner"), **dict.fromkeys(ADMIN, "admin")}


def _default(o: Any) -> Any:
    if isinstance(o, ArtifactRef):
        return {TAG: "ref", "v": [o.hash, o.kind, o.size, o.filename]}
    if isinstance(o, (bytes, bytearray)):
        return {TAG: "bytes", "v": base64.b64encode(bytes(o)).decode()}
    if isinstance(o, (set, frozenset)):
        return {TAG: "set", "v": sorted(o, key=repr)}
    if isinstance(o, Iterable) and not isinstance(o, (str, dict)):
        return list(o)  # generators, dict views, …: callers pass these as "any iterable"
    raise TypeError(f"can't send {type(o).__name__} to the server")


def _tag_tuples(o: Any) -> Any:
    # json turns tuples into lists silently; keep them, since callers unpack and compare them.
    if isinstance(o, tuple):
        return {TAG: "tuple", "v": [_tag_tuples(x) for x in o]}
    if isinstance(o, list):
        return [_tag_tuples(x) for x in o]
    if isinstance(o, dict):
        return {k: _tag_tuples(v) for k, v in o.items()}
    return o


def _hook(d: dict) -> Any:
    kind = d.get(TAG)
    if kind is None:
        return d
    v = d["v"]
    if kind == "ref":
        return ArtifactRef(*v)
    if kind == "bytes":
        return base64.b64decode(v)
    if kind == "set":
        return set(v)
    if kind == "tuple":
        return tuple(v)
    raise ValueError(f"unknown tag {kind!r}")


def dumps(obj: Any) -> str:
    return json.dumps(_tag_tuples(obj), default=_default)


def loads(text: str | bytes) -> Any:
    return json.loads(text, object_hook=_hook)
