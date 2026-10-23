import pytest
from fastapi.testclient import TestClient

from dendrite.cli import main
from dendrite.server import create_app

from . import toy
from .test_api import legacy_only


@pytest.fixture(autouse=True)
def no_grace(monkeypatch):
    monkeypatch.setenv("DENDRITE_GC_GRACE", "0")


def client(lab, tmp_path, edit=True):
    from dendrite import Lab

    lab = Lab(lab.home, db_url=lab.settings.db_url)  # picks up DENDRITE_GC_GRACE
    return TestClient(create_app(lab, ui_dir=legacy_only(tmp_path), allow_edit=edit))


def test_storage_endpoints(lab, source, tmp_path):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    b = a.fork({"train.iterations": 20}).run()
    c = client(lab, tmp_path)
    inv = c.get("/api/storage").json()
    assert {e["id"] for e in inv["experiments"]} == {a.id, b.id}
    assert inv["store"]["collectable"]["objects"] == 0

    assert c.post(f"/api/experiments/{a.id}/keep", json={"keep": True}).json()["keep"] is True
    assert {e["id"]: e["keep"] for e in c.get("/api/storage").json()["experiments"]}[a.id] is True

    assert c.delete(f"/api/experiments/{a.id}").status_code == 409  # has a fork
    gone = c.delete(f"/api/experiments/{b.id}").json()
    assert gone == {"deleted": b.id, "ids": [b.id], "reparented": {}, "parent_id": a.id}

    preview = c.post("/api/gc", json={}).json()
    assert preview["dry_run"] and preview["groups"]["unreferenced"]["count"] == len(preview["deleted"]) > 0
    assert all(lab.store.has(d["hash"]) for d in preview["deleted"])  # a preview deletes nothing
    assert c.get("/api/storage").json()["store"]["collectable"]["objects"] == len(preview["deleted"])
    applied = c.post("/api/gc", json={"apply": True, "hashes": [d["hash"] for d in preview["deleted"]]}).json()
    assert not applied["dry_run"] and applied["bytes"] == preview["bytes"]
    assert c.get("/api/storage").json()["store"]["collectable"]["objects"] == 0

    exec_id = {n["node"]: n["execution_id"] for n in a.node_runs()}["train"]
    assert c.post(f"/api/executions/{exec_id}/evict", json={}).json()["dry_run"] is True
    assert c.post(f"/api/executions/{exec_id}/evict", json={"apply": True}).json()["deleted"]
    assert c.post("/api/executions/nope/evict", json={}).status_code == 404
    assert c.post("/api/storage/clean").json()["removed"] >= 0


def test_bulk_delete_estimate_and_stop(lab, source, tmp_path):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    b = a.fork({"train.iterations": 20}).run()
    c = client(lab, tmp_path)
    est = c.post("/api/storage/estimate", json={"ids": [a.id]}).json()
    assert est["ids"] == [a.id] and est["bytes"] > 0
    assert c.post("/api/experiments/delete", json={"ids": [a.id]}).status_code == 409  # has a fork
    out = c.post("/api/experiments/delete", json={"ids": [a.id], "forks": "keep"}).json()
    assert out == {"deleted": [a.id], "reparented": {b.id: None}}
    assert c.post("/api/experiments/delete", json={"ids": ["e-nope"]}).status_code == 404

    lab.db.update_experiment(b.id, status="running")
    assert c.post("/api/gc", json={"apply": True}).status_code == 409
    assert c.post(f"/api/experiments/{b.id}/stop").json()["status"] == "failed"
    assert c.post(f"/api/experiments/{b.id}/stop").status_code == 409
    assert c.post("/api/gc", json={"apply": True}).json()["bytes"] == est["bytes"]

    big = b.node_outputs("extract")["rows"]
    assert c.post(f"/api/artifacts/{big}/evict", json={}).json()["dry_run"] is True
    assert c.post(f"/api/artifacts/{big}/evict", json={"apply": True}).json()["deleted"]
    assert not lab.store.verify(lab.artifact(big))
    assert c.post("/api/storage/clean", json={"force": True}).status_code == 200


def test_storage_mutations_need_edit(lab, source, tmp_path):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    orphan = lab.store.put_value("orphan").hash
    c = client(lab, tmp_path, edit=False)
    assert c.get("/api/storage").status_code == 200
    assert c.post("/api/gc", json={}).status_code == 200  # previews are read-only
    assert c.post("/api/gc", json={"apply": True}).status_code == 403
    assert lab.store.has(orphan)
    exec_id = a.node_runs()[0]["execution_id"]
    assert c.post(f"/api/executions/{exec_id}/evict", json={"apply": True}).status_code == 403
    assert c.post(f"/api/experiments/{a.id}/keep", json={"keep": True}).status_code == 403
    assert c.delete(f"/api/experiments/{a.id}").status_code == 403
    assert c.post("/api/storage/clean").status_code == 403
    assert c.post("/api/experiments/delete", json={"ids": [a.id]}).status_code == 403
    assert c.post(f"/api/experiments/{a.id}/stop").status_code == 403
    assert c.post(f"/api/artifacts/{a.inputs['src']}/evict", json={"apply": True}).status_code == 403
    assert lab.get(a.id).status == "completed"


def test_gc_include_recent(lab, source, tmp_path, monkeypatch):
    monkeypatch.setenv("DENDRITE_GC_GRACE", "3600")
    lab.create(toy.toy, inputs={"src": source}).run()
    orphan = lab.store.put_value("just written").hash
    c = client(lab, tmp_path)
    assert c.post("/api/gc", json={}).json()["skipped"][0]["why"] == "grace"
    preview = c.post("/api/gc", json={"include_recent": True}).json()
    assert [d["hash"] for d in preview["deleted"]] == [orphan]
    c.post("/api/gc", json={"include_recent": True, "apply": True, "hashes": [orphan]})
    assert not lab.store.has(orphan)


def test_gc_busy_is_409(lab, source, tmp_path):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    lab.db.update_experiment(a.id, status="running")
    r = client(lab, tmp_path).post("/api/gc", json={"apply": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "busy"


def test_cli_storage_commands(lab, source, capsys):
    a = lab.create(toy.toy, inputs={"src": source}, name="base").run()
    b = a.fork({"train.iterations": 20}).run()
    home = ["--home", str(lab.home), "--db-url", lab.settings.db_url]

    main([*home, "du"])
    out = capsys.readouterr().out
    assert a.id in out and "only here" in out

    with pytest.raises(SystemExit, match="--keep-forks"):
        main([*home, "rm", a.id])
    main([*home, "rm", a.id, "--forks"])
    assert "re-run with --yes" in capsys.readouterr().out
    assert len(lab.experiments()) == 2  # previewed only

    main([*home, "rm", b.id, "--yes"])
    assert [e.id for e in lab.experiments()] == [a.id]

    main([*home, "gc"])
    out = capsys.readouterr().out
    assert "would delete" in out and "unreferenced" in out
    train_b = set(b.node_outputs("train").values())
    assert all(lab.store.has(h) for h in train_b)
    main([*home, "gc", "--yes"])
    assert not any(lab.store.has(h) for h in train_b)
    capsys.readouterr()

    main([*home, "keep", a.id])
    assert "keep on" in capsys.readouterr().out and lab.get(a.id).kept
    main([*home, "gc", "--older-than", "0", "--yes"])
    assert "deleted 0 object(s)" in capsys.readouterr().out  # all kept

    exec_id = {n["node"]: n["execution_id"] for n in a.node_runs()}["train"]
    main([*home, "evict", exec_id])
    assert "would evict" in capsys.readouterr().out
    main([*home, "evict", exec_id, "--yes"])
    assert "evicted" in capsys.readouterr().out
    assert not lab.store.verify(lab.artifact(a.node_outputs("train")["model"]))
    main([*home, "clean"])
    assert "removed" in capsys.readouterr().out
