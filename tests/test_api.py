import shutil

from fastapi.testclient import TestClient

from dendrite.server import UI_DIR, create_app

from . import toy


def legacy_only(tmp_path):
    ui = tmp_path / "ui"
    ui.mkdir()
    shutil.copy(UI_DIR / "legacy.html", ui / "legacy.html")
    return ui


def test_api_endpoints(lab, source, tmp_path, monkeypatch):
    a = lab.create(toy.toy, inputs={"src": source}, name="base").run()
    b = a.fork({"train.iterations": 4}, name="fork").run()
    client = TestClient(create_app(lab, ui_dir=legacy_only(tmp_path)))

    assert "<title>Dendrite Lineage</title>" in client.get("/").text
    assert client.get("/api/info").json()["ui"] == "legacy"
    tree = client.get("/api/experiments").json()
    assert [e["id"] for e in tree] == [a.id, b.id]
    assert tree[1]["parent_id"] == a.id and tree[1]["counts"] == {"cached": 2, "computed": 1}

    dag = client.get(f"/api/experiments/{b.id}/dag").json()
    by_id = {n["id"]: n for n in dag["nodes"]}
    assert by_id["extract"]["status"] == "cached" and by_id["extract"]["execution_experiment"] == a.id
    assert {e["label"] for e in dag["edges"]} == {"src", "rows", "features"}
    assert dag["mermaid"].startswith("flowchart LR")

    detail = client.get(f"/api/experiments/{a.id}").json()
    assert detail["children"] == [b.id] and detail["metrics"]["train"]["score"] == 360

    diff = client.get("/api/diff", params={"a": a.id, "b": b.id}).json()
    assert diff["params"][0]["key"] == "train.iterations"

    model = b.output("train", "model")
    art = client.get(f"/api/artifacts/{model.hash[:12]}").json()
    assert art["present"] and art["preview"]["type"] == "text" and art["producer"]["node"] == "train"
    rows = client.get(f"/api/artifacts/{a.output('extract').hash}").json()
    assert rows["preview"]["type"] == "manifest" and rows["preview"]["count"] == 4
    feats = client.get(f"/api/artifacts/{a.output('featurize').hash}").json()
    assert feats["preview"] == {"type": "value", "repr": "[3, 7, 11, 15]"}

    lin = client.get(f"/api/artifacts/{model.hash}/lineage").json()
    assert len(lin["ancestors"]) == 3

    # Lineage artifacts are labelled by what they are in the pipeline, not their storage kind.
    report = b.output("train", "report")
    lin = client.get(f"/api/artifacts/{report.hash}/lineage").json()
    assert lin["artifact"]["label"] == "report"
    assert (lin["artifact"]["producer_node"], lin["artifact"]["role"], lin["artifact"]["kind"]) == ("train", "focus", "value")
    arts = {x["label"]: x for x in lin["artifacts"]}
    assert arts["src"]["role"] == "ancestor" and arts["src"]["producer_node"] is None  # source input name
    assert arts["rows"]["producer_node"] == "extract" and arts["features"]["producer_node"] == "featurize"
    down = client.get(f"/api/artifacts/{a.output('featurize').hash}/lineage").json()
    assert {x["label"]: x["role"] for x in down["artifacts"]}["model"] == "descendant"

    # Usage roles are structured; the compact strings remain for the CLI and legacy page.
    roles = {e["id"]: e for e in rows["experiments"]}
    assert set(roles) == {a.id, b.id}
    assert {"kind": "produced", "node": "extract", "name": "rows"} in roles[b.id]["roles"]
    assert {"kind": "read", "node": "featurize", "name": "rows"} in roles[b.id]["roles"]
    assert roles[a.id]["role_labels"] == ["consumed:featurize", "output:extract"]

    def no_blob_access(*args, **kwargs):
        raise AssertionError("execution icon metadata must not access artifact storage")

    with monkeypatch.context() as patch:
        patch.setattr(lab.store, "verify", no_blob_access)
        patch.setattr(lab.store, "has", no_blob_access)
        patch.setattr(lab.store, "load", no_blob_access)
        ex = client.get(f"/api/executions/{by_id['extract']['execution_id']}").json()
    assert {u["experiment_id"] for u in ex["used_by"]} == {a.id, b.id}
    assert ex["spec"]["op"] == "extract"
    assert ex["artifacts"][a.output("extract").hash]["kind"] == "dir"
    assert set(ex["artifacts"]) == set(ex["inputs"].values()) | set(ex["outputs"].values())

    assert client.get(f"/api/artifacts/{model.hash}/download").text.startswith("[3, 7, 11, 15]")
    assert client.get("/api/experiments/nope").status_code == 404
