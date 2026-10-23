from fastapi.testclient import TestClient

from dendrite.server import create_app

from . import toy
from .test_api import legacy_only


def paths(dag):
    return {n["id"]: [s["name"] if isinstance(s, dict) else s for s in n["stage"]] for n in dag["nodes"] if n["kind"] == "node"}


def test_stages_are_recorded_nested_and_defaulted(lab, source):
    exp = lab.create(toy.toy_stages, inputs={"src": source}).run()
    assert paths(exp.dag()) == {"extract": ["Prepare"], "featurize": ["Prepare"], "train": ["Model", "Train"], "tag": ["Fallback"]}
    info = {n["id"]: n["stage"] for n in exp.dag()["nodes"] if n["kind"] == "node"}["extract"][0]
    assert info == {"name": "Prepare", "description": "Rows and features", "color": "teal"}
    exp.run()  # a rerun replaces, not appends
    assert paths(exp.dag())["train"] == ["Model", "Train"]


def test_stages_never_change_signatures(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    toy.CALLS.clear()
    b = lab.create(toy.toy_stages, inputs={"src": source}).run()
    assert toy.CALLS["extract"] == toy.CALLS["featurize"] == toy.CALLS["train"] == 0  # all reused from toy
    sig = {n["node"]: n["signature"] for n in a.node_runs()}
    assert all(sig[n["node"]] == n["signature"] for n in b.node_runs() if n["node"] in sig)


def test_plans_and_shapes_know_stages_before_running(lab, source):
    d = lab.create(toy.toy_stages, inputs={"src": source}, draft=True)
    assert paths(d.plan())["train"] == ["Model", "Train"]
    assert lab.db.node_stages(d.id) == {}  # plans write nothing
    shape = toy.toy_stages.shape()
    by = {n["id"]: n.get("stage") for n in shape["nodes"] if n["kind"] == "node"}
    assert by["extract"] == ["Prepare"] and by["train"] == ["Model", "Train"] and by["tag"] == []


def test_pipeline_view_api(lab, tmp_path):
    ui = legacy_only(tmp_path)
    c = TestClient(create_app(lab, ui_dir=ui, allow_edit=True))
    assert c.get("/api/pipelines/toy/view").json() == {"assign": {}, "styles": {}}
    view = {"assign": {"tag": "Audio"}, "styles": {"Audio": {"label": "Audio", "description": "Transcription", "color": "purple"}}}
    assert c.put("/api/pipelines/toy/view", json=view).status_code == 200
    assert c.get("/api/pipelines/toy/view").json() == view
    assert c.put("/api/pipelines/toy/view", json={"styles": {"X": {"color": "chartreuse"}}}).status_code == 400
    ro = TestClient(create_app(lab, ui_dir=ui, allow_edit=False))
    assert ro.put("/api/pipelines/toy/view", json=view).status_code == 403
