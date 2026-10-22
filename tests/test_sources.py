"""Source snapshots: capture, disk status and guarded write-back."""

import importlib.util
import uuid

from fastapi.testclient import TestClient

from dendrite.server import create_app

from . import toy

MODULE = '''\
from dendrite import artifact_task, pipeline

HEADER = "kept"


@artifact_task(outputs=["out"], name="{node}")
def step(src, k: int = 1):
    return src.read_text() * k


@pipeline(name="{pipe}")
def pipe(src):
    return step(src)


FOOTER = "kept"
'''


def load_pipeline(tmp_path):
    tag = uuid.uuid4().hex[:8]
    path = tmp_path / f"mod_{tag}.py"
    path.write_text(MODULE.format(node=f"step_{tag}", pipe=f"pipe_{tag}"))
    spec = importlib.util.spec_from_file_location(f"mod_{tag}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, path, f"step_{tag}"


def run_and_source(lab, client, pipe, node, source):
    exp = lab.create(pipe, inputs={"src": source}).run()
    exec_id = {n["node"]: n["execution_id"] for n in exp.node_runs()}[node]
    return exp, client.get(f"/api/executions/{exec_id}").json()["source"]


def test_snapshot_recorded_with_location(lab, source):
    client = TestClient(create_app(lab))
    exp = lab.create(toy.toy, inputs={"src": source}).run()
    exec_id = {n["node"]: n["execution_id"] for n in exp.node_runs()}["train"]
    src = client.get(f"/api/executions/{exec_id}").json()["source"]
    assert src["file"].endswith("tests/toy.py") and src["qualname"] == "train"
    assert src["display"].endswith("tests/toy.py")
    snap = client.get(f"/api/sources/{src['hash']}").json()
    lines = open(src["file"]).read().splitlines()
    assert lines[snap["line"] - 1].startswith("@artifact_task")  # first line is the decorator
    assert "def train(features: list" in snap["source"]
    assert snap["status"] == "same" and snap["editable"] is False  # editing off by default


def test_snapshots_do_not_change_signatures(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    toy.CALLS.clear()
    b = lab.create(toy.toy, inputs={"src": source}).run()
    assert sum(toy.CALLS.values()) == 0
    assert [n["signature"] for n in a.node_runs()] == [n["signature"] for n in b.node_runs()]


def test_save_rewrites_only_the_function(lab, source, tmp_path):
    mod, path, node = load_pipeline(tmp_path)
    client = TestClient(create_app(lab, allow_edit=True))
    _, src = run_and_source(lab, client, mod.pipe, node, source)
    snap = client.get(f"/api/sources/{src['hash']}").json()
    assert snap["status"] == "same" and snap["editable"] is True

    new = snap["source"].replace("* k", "* (k + 1)")
    res = client.put(f"/api/sources/{src['hash']}", json={"source": new, "file_sha": snap["file_sha"]})
    assert res.status_code == 200, res.text
    text = path.read_text()
    assert "src.read_text() * (k + 1)" in text and "* k\n" not in text
    assert text.startswith("from dendrite import") and 'FOOTER = "kept"' in text and 'HEADER = "kept"' in text

    # The snapshot no longer matches the file, so it is now view-only.
    after = client.get(f"/api/sources/{src['hash']}").json()
    assert after["status"] == "changed" and after["editable"] is False


def test_stale_editor_gets_conflict(lab, source, tmp_path):
    mod, path, node = load_pipeline(tmp_path)
    client = TestClient(create_app(lab, allow_edit=True))
    _, src = run_and_source(lab, client, mod.pipe, node, source)
    snap = client.get(f"/api/sources/{src['hash']}").json()
    path.write_text(path.read_text() + "\n# edited elsewhere\n")
    res = client.put(f"/api/sources/{src['hash']}", json={"source": "x", "file_sha": snap["file_sha"]})
    assert res.status_code == 409
    assert "# edited elsewhere" in path.read_text() and "x\n" not in path.read_text().splitlines()


def test_missing_file_and_disabled_editing(lab, source, tmp_path):
    mod, path, node = load_pipeline(tmp_path)
    client = TestClient(create_app(lab))  # allow_edit defaults to False
    _, src = run_and_source(lab, client, mod.pipe, node, source)
    snap = client.get(f"/api/sources/{src['hash']}").json()
    res = client.put(f"/api/sources/{src['hash']}", json={"source": snap["source"], "file_sha": snap["file_sha"]})
    assert res.status_code == 403
    assert client.get("/api/info").json()["edit"] is False
    path.unlink()
    assert client.get(f"/api/sources/{src['hash']}").json()["status"] == "missing"
