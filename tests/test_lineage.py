import threading

import pytest

from dendrite.task import code_fingerprint

from . import toy


@pytest.fixture(autouse=True)
def reset():
    toy.CALLS.clear()
    toy.SLEEP["train"] = 0.0
    toy.FAIL["train"] = False


def statuses(exp):
    return {n["node"]: n["status"] for n in exp.node_runs()}


def execs(exp):
    return {n["node"]: n["execution_id"] for n in exp.node_runs()}


def test_first_run_computes_everything(lab, source):
    exp = lab.create(toy.toy, inputs={"src": source}, name="base").run()
    assert exp.status == "completed"
    assert statuses(exp) == {"extract": "computed", "featurize": "computed", "train": "computed"}
    assert dict(toy.CALLS) == {"extract": 1, "featurize": 1, "train": 1}
    assert exp.load("featurize") == [3, 7, 11, 15]
    assert exp.load("train", "report") == {"iterations": 10}
    assert exp.load("train", "model").read_text().startswith("[3, 7, 11, 15] x10")
    assert exp.metrics()["train"]["score"] == 360
    assert exp.flow_run_id


def test_fork_reuses_upstream_and_shares_executions(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    toy.CALLS.clear()
    b = a.fork({"train.iterations": 20}).run()
    assert b.parent_id == a.id
    assert dict(toy.CALLS) == {"train": 1}
    assert statuses(b) == {"extract": "cached", "featurize": "cached", "train": "computed"}
    ea, eb = execs(a), execs(b)
    assert ea["extract"] == eb["extract"] and ea["featurize"] == eb["featurize"]
    assert ea["train"] != eb["train"]


def test_identical_experiment_executes_nothing(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}, params={"train": {"iterations": 20}}).run()
    toy.CALLS.clear()
    c = lab.create(toy.toy, inputs={"src": source}, params={"train.iterations": 20}).run()
    assert c.id != a.id
    assert sum(toy.CALLS.values()) == 0
    assert set(statuses(c).values()) == {"cached"}
    assert c.metrics() == a.metrics()  # metrics come from the shared executions


def test_upstream_param_invalidates_downstream_only(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    toy.CALLS.clear()
    b = a.fork({"featurize": {"scale": 2}}).run()
    assert dict(toy.CALLS) == {"featurize": 1, "train": 1}
    assert statuses(b)["extract"] == "cached"
    assert b.load("featurize") == [6, 14, 22, 30]


def test_same_output_content_reuses_downstream(lab, source):
    # limit=4 vs default 100 yields identical rows -> featurize/train hit by content hash.
    a = lab.create(toy.toy, inputs={"src": source}).run()
    toy.CALLS.clear()
    b = a.fork({"extract.limit": 4}).run()
    assert dict(toy.CALLS) == {"extract": 1}
    assert statuses(b) == {"extract": "computed", "featurize": "cached", "train": "cached"}


def test_artifact_fork_pins_node(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    toy.CALLS.clear()
    b = a.fork({"featurize.scale": 99}, pin={"featurize": a}).run()
    assert statuses(b)["featurize"] == "pinned"
    assert "featurize" not in toy.CALLS
    assert b.output("featurize") == a.output("featurize")
    # downstream of the pin sees identical inputs, so it is reused too
    assert statuses(b)["train"] == "cached"

    c = a.fork({"train.seed": 1}, pin={"featurize.features": a.output("featurize").hash}).run()
    assert statuses(c)["featurize"] == "pinned"
    assert statuses(c)["train"] == "computed"

    d = b.fork(pin={"featurize": None}).run()
    assert d.pins == {} and statuses(d)["featurize"] == "computed"
    assert d.load("featurize") == [297, 693, 1089, 1485]


def test_missing_blob_forces_recompute(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    lab.store.delete(a.output("train", "model").hash)
    toy.CALLS.clear()
    b = lab.create(toy.toy, inputs={"src": source}).run()
    assert dict(toy.CALLS) == {"train": 1}
    assert statuses(b)["train"] == "computed"
    assert lab.store.verify(b.output("train", "model"))


def test_missing_dir_member_forces_recompute(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    rows = a.output("extract")
    _, member, _ = lab.store.manifest(rows)[0]
    lab.store.delete(member)
    toy.CALLS.clear()
    lab.create(toy.toy, inputs={"src": source}).run()
    assert toy.CALLS["extract"] == 1


def test_non_cacheable_node_always_runs(lab, source):
    lab.create(toy.toy_sample, inputs={"src": source}).run()
    b = lab.create(toy.toy_sample, inputs={"src": source}).run()
    assert toy.CALLS["sample"] == 2
    assert statuses(b) == {"extract": "cached", "featurize": "cached", "sample": "computed"}


def test_submit_parallel_branches(lab, source):
    exp = lab.create(toy.toy_parallel, inputs={"src": source}).run()
    assert statuses(exp) == {"extract": "computed", "featurize": "computed", "sample": "computed", "train": "computed"}
    edges = {(e["from"], e["to"]) for e in exp.dag()["edges"]}
    assert {("input:src", "extract"), ("extract", "featurize"), ("featurize", "sample"), ("featurize", "train")} <= edges


def test_concurrent_identical_runs_execute_once(lab, source):
    toy.SLEEP["train"] = 1.0
    a = lab.create(toy.toy, inputs={"src": source}, name="a")
    b = lab.create(toy.toy, inputs={"src": source}, name="b")
    errors = []

    def run(e):
        try:
            e.run()
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(e,)) for e in (a, b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert dict(toy.CALLS) == {"extract": 1, "featurize": 1, "train": 1}
    assert execs(a.refresh()) == execs(b.refresh())


def test_lease_steal_after_expiry(lab):
    import time

    assert lab.db.acquire_lease("sig", "w1", ttl=0.05)
    assert not lab.db.acquire_lease("sig", "w2", ttl=10)
    time.sleep(0.1)
    assert lab.db.acquire_lease("sig", "w2", ttl=10)
    assert not lab.db.renew_lease("sig", "w1", ttl=10)


def test_failure_records_lineage_and_releases_lease(lab, source):
    toy.FAIL["train"] = True
    exp = lab.create(toy.toy, inputs={"src": source})
    with pytest.raises(Exception):
        exp.run()
    exp.refresh()
    assert exp.status == "failed"
    assert statuses(exp)["train"] == "failed"
    with lab.db.ro() as conn:
        from dendrite.db import executions, leases

        assert conn.execute(leases.select()).all() == []
        assert {r.status for r in conn.execute(executions.select().where(executions.c.node == "train"))} == {"failed"}
    toy.FAIL["train"] = False
    toy.CALLS.clear()
    exp.run()
    assert dict(toy.CALLS) == {"train": 1}


def test_signature_ignores_experiment_and_scopes_params(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}, params={"train.iterations": 5}).run()
    b = lab.create(toy.toy, inputs={"src": source}, params={"train.iterations": 6}).run()
    sa = {n["node"]: n["signature"] for n in a.node_runs()}
    sb = {n["node"]: n["signature"] for n in b.node_runs()}
    assert sa["extract"] == sb["extract"] and sa["featurize"] == sb["featurize"]
    assert sa["train"] != sb["train"]


def test_source_change_changes_fingerprint(tmp_path):
    import importlib.util

    def load(code, name):
        p = tmp_path / f"{name}.py"
        p.write_text(code)
        spec = importlib.util.spec_from_file_location(name, p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.f

    f1 = load("def f(x):\n    return x + 1\n", "m1")
    f2 = load("def f(x):\n    return x + 2\n", "m2")
    f3 = load("def f(x):\n    return x + 1\n", "m3")
    assert code_fingerprint(f1) != code_fingerprint(f2)
    assert code_fingerprint(f1) == code_fingerprint(f3)


def test_unknown_param_key_is_rejected(lab, source):
    exp = lab.create(toy.toy, inputs={"src": source}, params={"train.iterashuns": 5})
    with pytest.raises(Exception, match="unknown keys"):
        exp.run()


def test_diff_and_lineage(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    b = a.fork({"train.iterations": 20}).run()
    d = a.diff(b)
    assert d["params"] == [{"key": "train.iterations", "a": None, "b": 20, "change": "added"}]
    by_node = {n["node"]: n for n in d["nodes"]}
    assert by_node["extract"]["same_execution"] and not by_node["train"]["same_signature"]

    model = b.output("train", "model")
    g = lab.lineage(model)
    nodes = {x["node"] for x in g["executions"] if x["id"] in g["ancestors"]}
    assert nodes == {"extract", "featurize", "train"}
    assert lab.ingest(source).hash in {x["hash"] for x in g["artifacts"]}
    src_desc = lab.lineage(lab.ingest(source))
    assert {x["node"] for x in src_desc["executions"]} == {"extract", "featurize", "train"}
    assert {e["id"] for e in g["experiments"]} == {b.id}


def test_experiment_fork_tree(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}, name="a")
    b = a.fork({"train.iterations": 1}, name="b")
    c = b.fork({"train.seed": 3}, name="c")
    tree = {e["id"]: e["parent_id"] for e in lab.tree()}
    assert tree == {a.id: None, b.id: a.id, c.id: b.id}
    assert c.params == {"train": {"iterations": 1, "seed": 3}}
    assert lab.get("c") == c
