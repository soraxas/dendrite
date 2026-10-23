from fastapi.testclient import TestClient

from dendrite.server import create_app

from . import toy
from .test_api import no_bundle


def test_draft_endpoints_round_trip(lab, source, tmp_path):
    a = lab.create(toy.toy, inputs={"src": source}, name="base").run()
    c = TestClient(create_app(lab, ui_dir=no_bundle(tmp_path), allow_edit=True))
    assert c.get("/api/info").json()["plan"] is True

    d = c.post(f"/api/experiments/{a.id}/fork", json={"name": "try"}).json()
    assert (d["status"], d["parent_id"], d["name"]) == ("draft", a.id, "try")

    plan = c.get(f"/api/experiments/{d['id']}/plan").json()
    assert plan["totals"] == {"steps": 3, "run": 0, "reuse": 3, "frozen": 0, "estimate": 0, "unestimated": 0}
    assert plan["module"] == toy.__name__

    # every param sent back unchanged, as typed JSON, keeps the plan all-reuse
    same = {f"{n['name']}.{s['name']}": s["value"] for n in plan["nodes"] for s in n.get("param_schema", [])}
    assert c.patch(f"/api/experiments/{d['id']}", json={"params": same}).status_code == 200
    assert c.get(f"/api/experiments/{d['id']}/plan").json()["totals"]["run"] == 0

    r = c.patch(f"/api/experiments/{d['id']}", json={"params": {"train.iterations": 20}, "pins": {"featurize": "parent"}})
    assert r.json()["pins"]["featurize"]
    steps = {n["name"]: n["plan"] for n in c.get(f"/api/experiments/{d['id']}/plan").json()["nodes"] if n["kind"] == "node"}
    assert steps == {"extract": "reuse", "featurize": "frozen", "train": "run"}

    assert c.patch(f"/api/experiments/{d['id']}", json={"reset": ["train.iterations"], "pins": {"featurize": None}}).status_code == 200
    assert c.get(f"/api/experiments/{d['id']}/plan").json()["totals"]["run"] == 0

    assert c.patch(f"/api/experiments/{a.id}", json={"params": {"train.iterations": 2}}).status_code == 409
    assert c.delete(f"/api/experiments/{a.id}").status_code == 409
    assert c.delete(f"/api/experiments/{d['id']}").status_code == 200
    assert [e["id"] for e in c.get("/api/experiments").json()] == [a.id]


def test_draft_endpoints_need_edit(lab, source, tmp_path):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    d = a.fork(draft=True)
    c = TestClient(create_app(lab, ui_dir=no_bundle(tmp_path)))
    assert c.post(f"/api/experiments/{a.id}/fork", json={}).status_code == 403
    assert c.patch(f"/api/experiments/{d.id}", json={"params": {"train.iterations": 2}}).status_code == 403
    assert c.delete(f"/api/experiments/{d.id}").status_code == 403
    assert lab.get(d.id).params == {}
    assert c.get(f"/api/experiments/{d.id}/plan").status_code == 200  # reading is always allowed


def test_plan_without_pipeline_module(lab, source, tmp_path):
    a = lab.create(toy.toy, inputs={"src": source})
    lab.db.update_experiment(a.id, pipeline="not_loaded")
    r = TestClient(create_app(lab, ui_dir=no_bundle(tmp_path))).get(f"/api/experiments/{a.id}/plan")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "pipeline_not_imported"


def test_cli_run_freezes_draft(lab, source, capsys):
    from dendrite.cli import cmd_run

    a = lab.create(toy.toy, inputs={"src": source}).run()
    d = a.fork({"train.iterations": 3}, draft=True)

    class Args:
        experiment = d.id
        imports = [toy.__name__]

    cmd_run(lab, Args)
    out = capsys.readouterr().out
    assert "train" in out and "computed" in out and out.strip().endswith("completed")
    assert lab.get(d.id).status == "completed"


def test_float_params_survive_json(lab, source, tmp_path):
    a = lab.create(toy.toy, inputs={"src": source}, params={"featurize.scale": 2.0}).run()
    c = TestClient(create_app(lab, ui_dir=no_bundle(tmp_path), allow_edit=True))
    d = c.post(f"/api/experiments/{a.id}/fork", json={}).json()
    # a browser sends 2.0 as `2`; listing the key in `floats` keeps it a float, so nothing reruns
    c.patch(f"/api/experiments/{d['id']}", json={"params": {"featurize.scale": 2}, "floats": ["featurize.scale"]})
    assert lab.get(d["id"]).params == {"featurize": {"scale": 2.0}}
    assert isinstance(lab.get(d["id"]).params["featurize"]["scale"], float)
    assert c.get(f"/api/experiments/{d['id']}/plan").json()["totals"]["run"] == 0


def test_edit_errors_are_client_errors(lab, source, tmp_path):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    root = lab.create(toy.toy, inputs={"src": source}, draft=True)
    c = TestClient(create_app(lab, ui_dir=no_bundle(tmp_path), allow_edit=True))
    d = c.post(f"/api/experiments/{a.id}/fork", json={}).json()
    c.post(f"/api/experiments/{d['id']}/fork", json={})
    assert c.delete(f"/api/experiments/{d['id']}").status_code == 409  # it has a fork
    assert c.patch(f"/api/experiments/{root.id}", json={"pins": {"train": "parent"}}).status_code == 400  # no parent
    assert c.patch(f"/api/experiments/{d['id']}", json={"pins": {"train": "elsewhere"}}).status_code == 400
    assert c.patch(f"/api/experiments/{d['id']}", json={"params": {"nodot": 1}}).status_code == 400
    assert c.patch(f"/api/experiments/nope", json={}).status_code == 404
