"""Artifact names: human-readable, re-pointable labels usable as @name (metadata only)."""

import pytest

from dendrite.db import NameConflict

from . import toy


@pytest.fixture(autouse=True)
def reset():
    toy.CALLS.clear()
    toy.SLEEP["train"] = 0.0
    toy.FAIL["train"] = False


def test_name_an_output_and_load_it(lab, source):
    exp = lab.create(toy.toy, inputs={"src": source}).run()
    row = lab.name("best-model", f"{exp.id}:train.model", note="first try")
    assert row["hash"] == exp.output("train", "model").hash
    assert (row["experiment_id"], row["node"], row["output"]) == (exp.id, "train", "model")
    assert lab.load("@best-model").read_text() == exp.load("train", "model").read_text()
    assert lab.artifact("@best-model").hash == row["hash"]


def test_targets_inputs_single_outputs_and_hashes(lab, source):
    exp = lab.create(toy.toy, inputs={"src": source}).run()
    assert lab.name("raw", f"{exp.id}:src")["output"] == "src"
    assert lab.name("feats", f"{exp.id}:featurize")["output"] == "features"  # the only output
    h = exp.output("featurize").hash
    assert lab.name("by-prefix", h[:10])["hash"] == h
    assert lab.name("by-name", "@feats")["hash"] == h
    with pytest.raises(KeyError, match="outputs"):
        lab.name("ambiguous", f"{exp.id}:train")  # two outputs
    with pytest.raises(KeyError):
        lab.name("nope", f"{exp.id}:missing.out")


def test_invalid_and_taken_names_change_nothing(lab, source):
    exp = lab.create(toy.toy, inputs={"src": source}).run()
    lab.name("best", f"{exp.id}:featurize")
    for bad in ("a/b", "has space", "", "-leading", "x" * 101):
        with pytest.raises(ValueError):
            lab.name(bad, f"{exp.id}:featurize")
    with pytest.raises(NameConflict) as e:
        lab.name("best", f"{exp.id}:train.model")
    assert e.value.code == "name_taken"
    assert lab.db.name("best")["hash"] == exp.output("featurize").hash
    assert lab.db.name_history("best") == []


def test_moving_keeps_history_and_removing_keeps_the_artifact(lab, source):
    exp = lab.create(toy.toy, inputs={"src": source}).run()
    a, b = exp.output("featurize").hash, exp.output("train", "model").hash
    lab.name("best", a)
    lab.name("best", b, move=True)
    assert lab.artifact("@best").hash == b
    hist = lab.db.name_history("best")
    assert [(h["action"], h["hash"]) for h in hist] == [("moved", a)]
    assert lab.unname("best")
    with pytest.raises(KeyError, match="@best"):
        lab.artifact("@best")
    assert [h["action"] for h in lab.db.name_history("best")] == ["removed", "moved"]
    assert lab.store.verify(lab.artifact(b))
    assert lab.unname("best") is False


def test_compare_and_set_refuses_a_stale_move(lab, source):
    exp = lab.create(toy.toy, inputs={"src": source}).run()
    a, b = exp.output("featurize").hash, exp.output("train", "model").hash
    lab.name("best", a)
    lab.name("best", b, move=True)
    with pytest.raises(NameConflict) as e:
        lab.name("best", a, move=True, expect=a)  # someone moved it since this client looked
    assert e.value.code == "name_moved"
    with pytest.raises(NameConflict):
        lab.unname("best", expect=a)
    assert lab.artifact("@best").hash == b


def test_names_never_change_signatures(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    lab.name("feats", f"{a.id}:featurize")
    toy.CALLS.clear()
    b = a.fork().run()
    assert dict(toy.CALLS) == {}  # everything reused
    assert {n["status"] for n in b.node_runs()} == {"cached"}


def test_inputs_and_pins_by_name_freeze_the_hash(lab, source, tmp_path):
    base = lab.create(toy.toy, inputs={"src": source}).run()
    lab.name("raw-scan", f"{base.id}:src")
    lab.name("good-feats", f"{base.id}:featurize")
    by_input = lab.create(toy.toy, inputs={"src": "@raw-scan"})
    assert by_input.inputs["src"] == base.inputs["src"]
    pinned = base.fork(pin={"featurize.features": "@good-feats"})
    other = tmp_path / "other.csv"
    other.write_text("a,b\n9,9\n")
    elsewhere = lab.create(toy.toy, inputs={"src": other}).run()
    lab.name("good-feats", f"{elsewhere.id}:featurize", move=True)
    assert pinned.pins["featurize"]["features"] == base.output("featurize").hash  # resolved at creation


def test_unknown_name_never_reads_a_file(lab, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "@ghost").write_text("a file, not a name")
    with pytest.raises(KeyError, match="Path"):
        lab.ingest("@ghost")
    from pathlib import Path

    assert lab.ingest(Path("@ghost")).kind == "file"


@pytest.mark.local_only("reads the storage report of a local store")
def test_named_artifacts_survive_gc_and_refuse_eviction(lab, source):
    from dendrite.storage import EvictRefused

    exp = lab.create(toy.toy, inputs={"src": source}).run()
    model = exp.output("train", "model")
    lab.name("keeper", model)
    exp.delete()
    lab.gc(dry_run=False, grace=0)
    assert lab.store.verify(model)
    assert lab.load("@keeper").exists()
    inv = lab.storage()
    assert inv["store"]["named"]["objects"] >= 1
    with pytest.raises(EvictRefused, match="@keeper"):
        lab.evict(model.hash, dry_run=False)
    lab.unname("keeper")
    lab.gc(dry_run=False, grace=0)
    assert not lab.store.verify(model)


def test_names_in_api_responses(lab, source, tmp_path):
    from fastapi.testclient import TestClient

    from dendrite.server import create_app

    from .test_api import no_bundle

    exp = lab.create(toy.toy, inputs={"src": source}).run()
    c = TestClient(create_app(lab, ui_dir=no_bundle(tmp_path), allow_edit=True))
    r = c.put("/api/names/best", json={"experiment": exp.id, "node": "train", "output": "model", "note": "n"})
    assert r.status_code == 200, r.text
    h = r.json()["hash"]
    assert c.get(f"/api/experiments/{exp.id}/dag").json()["names"][h] == ["best"]
    assert c.get(f"/api/artifacts/{h}").json()["names"] == {h: ["best"]}
    assert c.get(f"/api/artifacts/{h}/lineage").json()["names"][h] == ["best"]
    assert [n["name"] for n in c.get("/api/names").json()["names"]] == ["best"]
    taken = c.put("/api/names/best", json={"experiment": exp.id, "node": "featurize"})
    assert taken.status_code == 409 and taken.json()["detail"]["code"] == "name_taken"
    stale = c.put("/api/names/best", json={"experiment": exp.id, "node": "featurize", "move": True, "expect": "0" * 64})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "name_moved"
    moved = c.put("/api/names/best", json={"experiment": exp.id, "node": "featurize", "move": True, "expect": h})
    assert moved.status_code == 200
    assert c.get("/api/names/best/history").json()["history"][0]["hash"] == h
    assert c.delete("/api/names/best").status_code == 200
    assert c.delete("/api/names/best").status_code == 404
    assert c.put("/api/names/bad name", json={"hash": h}).status_code == 422
    ro = TestClient(create_app(lab, ui_dir=no_bundle(tmp_path), allow_edit=False))
    assert ro.put("/api/names/x", json={"hash": h}).status_code == 403


@pytest.mark.local_only("the CLI is pointed at a local database URL")
def test_cli_name_names_unname(lab, source, capsys):
    from dendrite.cli import main

    exp = lab.create(toy.toy, inputs={"src": source}).run()
    home = ["--home", str(lab.home), "--db-url", lab.settings.db_url]
    main([*home, "name", "paper-fig3", f"{exp.id}:train.model", "--note", "camera ready"])
    assert "@paper-fig3 ->" in capsys.readouterr().out
    main([*home, "names"])
    out = capsys.readouterr().out
    assert "@paper-fig3" in out and "camera ready" in out and f"{exp.id}:train.model" in out
    with pytest.raises(SystemExit, match="--force"):
        main([*home, "name", "paper-fig3", f"{exp.id}:featurize"])
    main([*home, "name", "paper-fig3", f"{exp.id}:featurize", "--force"])
    capsys.readouterr()
    main([*home, "names", "--history", "paper-fig3"])
    assert "moved" in capsys.readouterr().out
    main([*home, "unname", "paper-fig3"])
    with pytest.raises(SystemExit):
        main([*home, "unname", "paper-fig3"])
