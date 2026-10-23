import pytest

from dendrite.storage import EvictRefused, StorageBusy

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


def blob_sizes(lab):
    return {h: size for h, size, _ in lab.store.iter_objects()}


def closure_bytes(lab, hashes):
    sizes = blob_sizes(lab)
    out = set()
    for h in hashes:
        out.add(h)
        ref = lab.artifact(h)
        if ref.kind == "dir":
            out.update(m for _, m, _ in lab.store.manifest(ref))
    return sum(sizes.get(h, 0) for h in out)


def by_id(inv):
    return {e["id"]: e for e in inv["experiments"]}


def test_exclusive_bytes_count_shared_cache_once(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    b = a.fork({"train.iterations": 20}).run()
    inv = lab.storage()
    ea, eb = by_id(inv)[a.id], by_id(inv)[b.id]
    assert eb["exclusive"] == closure_bytes(lab, b.node_outputs("train").values())
    assert ea["exclusive"] == closure_bytes(lab, a.node_outputs("train").values())
    shared = closure_bytes(lab, [*a.inputs.values(), *a.node_outputs("extract").values(), *a.node_outputs("featurize").values()])
    assert inv["store"]["shared"]["bytes"] == shared
    assert ea["bytes"] == shared + ea["exclusive"]
    assert ea["with_forks"] == shared + ea["exclusive"] + eb["exclusive"]
    assert ea["forks"] == [b.id]
    assert inv["store"]["live"]["bytes"] == inv["store"]["bytes"]  # nothing collectable yet
    assert inv["store"]["collectable"]["objects"] == 0
    train = {c["execution_id"]: c for c in inv["cache"]}[execs(a)["extract"]]
    assert train["uses"] == 2 and train["reused"] == 1 and train["present"] and train["live"]


def test_delete_keeps_shared_cache_usable(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    b = a.fork({"train.iterations": 20}).run()
    with pytest.raises(ValueError, match="fork"):
        a.delete()
    assert a.delete(keep_forks=True) == [a.id]
    assert b.refresh().parent_id is None  # a had no parent
    with pytest.raises(KeyError):
        lab.get(a.id)
    report = lab.gc(dry_run=False, grace=0)
    assert {d["why"] for d in report["deleted"]} == {"unreferenced"}
    assert report["bytes"] > 0
    for node in ("extract", "featurize", "train"):
        for h in b.node_outputs(node).values():
            assert lab.store.verify(lab.artifact(h))
    # Lineage rows stay: the execution still names the deleted experiment that triggered it.
    assert lab.db.execution(execs(b)["extract"])["experiment_id"] == a.id
    toy.CALLS.clear()
    c = lab.create(toy.toy, inputs={"src": source}, params={"train.iterations": 20}).run()
    assert sum(toy.CALLS.values()) == 0 and set(statuses(c).values()) == {"cached"}
    toy.CALLS.clear()
    lab.create(toy.toy, inputs={"src": source}).run()  # a's config: only its own step recomputes
    assert dict(toy.CALLS) == {"train": 1}


def test_delete_with_forks_and_refusals(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    b = a.fork({"train.iterations": 20}).run()
    c = b.fork({"train.iterations": 30}, draft=True)
    lab.db.update_experiment(c.id, status="running")
    with pytest.raises(ValueError, match="running"):
        a.delete(forks=True)
    assert {e.id for e in lab.experiments()} == {a.id, b.id, c.id}  # nothing half-deleted
    lab.db.update_experiment(c.id, status="draft")
    a.keep()
    assert a.delete(forks=True) == [c.id, b.id, a.id]
    assert lab.experiments() == []


def test_bulk_delete_keeps_forks_under_nearest_survivor(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    b = a.fork({"train.iterations": 20}).run()
    c = b.fork({"train.iterations": 30}).run()
    d = b.fork({"train.iterations": 40}, draft=True)
    with pytest.raises(ValueError, match="fork"):
        lab.delete([b.id])
    est = lab.deletion_estimate([b.id])
    assert est["bytes"] > 0 and est["ids"] == [b.id]
    out = lab.delete([b.id], forks="keep")
    assert out == {"deleted": [b.id], "reparented": {c.id: a.id, d.id: a.id}}
    assert c.refresh().parent_id == a.id
    freed = lab.gc(dry_run=False, grace=0)["bytes"]
    assert freed == est["bytes"]
    # Deleting a chain at once re-attaches what stays to the nearest survivor (here none).
    out = lab.delete([a.id, c.id], forks="keep")
    assert out["deleted"][-1] == a.id and out["reparented"] == {d.id: None}
    assert [e.id for e in lab.experiments()] == [d.id]


def test_delete_estimate_with_forks(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    b = a.fork({"train.iterations": 20}).run()
    est = lab.deletion_estimate([a.id], forks="delete")
    assert est["ids"] == sorted([a.id, b.id])
    assert est["bytes"] == lab.storage()["store"]["bytes"]  # everything goes


def test_mark_stopped_unblocks_cleanup(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    lab.db.update_experiment(a.id, status="running")  # as if its process crashed
    with pytest.raises(StorageBusy):
        lab.gc(dry_run=False)
    assert a.mark_stopped().status == "failed"
    lab.gc(dry_run=False)
    with pytest.raises(ValueError, match="not running"):
        a.mark_stopped()


def test_evicted_step_recomputes(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    old = execs(a)["extract"]
    rows = lab.artifact(a.node_outputs("extract")["rows"])
    members = {h for _, h, _ in lab.store.manifest(rows)}
    a.load("extract")  # a checkout hardlinks the members
    preview = lab.evict(old)
    assert preview["dry_run"] and preview["deleted"]
    assert all(lab.store.has(d["hash"]) for d in preview["deleted"])  # a preview deletes nothing
    report = lab.evict(old, dry_run=False)
    assert not lab.store.verify(rows)
    assert {d["why"] for d in report["deleted"]} == {f"evicted:{old}"}
    # The manifest and every member are gone, and so is the checkout that hardlinked them.
    assert {d["hash"] for d in report["deleted"]} == {rows.hash, *members}
    assert not (lab.store.cache_dir / "checkouts" / rows.hash).exists()
    assert lab.db.execution(old)["status"] == "success"  # metadata stays
    entry = {c["execution_id"]: c for c in lab.storage()["cache"]}[old]
    assert (entry["present"], entry["live"], entry["bytes"]) == (False, True, 0)  # still used, just gone
    toy.CALLS.clear()
    a.run()
    assert dict(toy.CALLS) == {"extract": 1}  # downstream outputs are intact, so they reuse
    assert execs(a)["extract"] != old
    assert lab.store.verify(rows)
    assert lab.lineage(rows.hash)["producer"] == old  # the first producer stays recorded
    assert execs(a)["extract"] in lab.lineage(rows.hash)["ancestors"]


def test_evict_refuses_pinned_outputs(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    b = a.fork({"train.iterations": 20}, pin={"featurize": a})
    feat = execs(a)["featurize"]
    with pytest.raises(EvictRefused, match=b.id):
        lab.evict(feat, dry_run=False)
    assert lab.evict(feat, dry_run=False, force=True)["deleted"]


def test_evict_skips_experiment_inputs(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    report = lab.evict(a.inputs["src"], dry_run=False)
    assert report["deleted"] == [] and report["skipped"][0]["why"] == "input"


def test_orphan_and_grace(lab, source):
    lab.create(toy.toy, inputs={"src": source}).run()
    orphan = lab.store.put_value("left over by a failed run").hash
    fresh = lab.gc()
    assert {"hash": orphan, "size": blob_sizes(lab)[orphan], "why": "grace"} in fresh["skipped"]
    assert fresh["deleted"] == []
    old = lab.gc(grace=0)
    assert [(d["hash"], d["why"]) for d in old["deleted"]] == [(orphan, "orphan")]
    assert lab.store.has(orphan)  # dry run
    lab.gc(dry_run=False, grace=0)
    assert not lab.store.has(orphan)


def test_apply_exactly_the_preview(lab, source):
    lab.create(toy.toy, inputs={"src": source}).run()
    first = lab.store.put_value("first").hash
    preview = lab.gc(grace=0)
    second = lab.store.put_value("second").hash  # became collectable after the preview
    applied = lab.gc(dry_run=False, grace=0, hashes=[d["hash"] for d in preview["deleted"]])
    assert [d["hash"] for d in applied["deleted"]] == [first]
    assert lab.store.has(second) and not lab.store.has(first)


def test_age_gc_spares_kept_pinned_and_newest(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run().keep()
    b = a.fork({"train.iterations": 20}).run()
    c = a.fork({"train.iterations": 30}).run()  # newest completed run of the pipeline
    report = lab.gc(older_than=0, dry_run=False, grace=0)
    gone = {d["hash"] for d in report["deleted"]}
    assert gone and all(d["why"].startswith("unused-since:") for d in report["deleted"])
    assert gone == set(b.node_outputs("train").values())
    for exp in (a, c):
        for node in ("extract", "featurize", "train"):
            for h in exp.node_outputs(node).values():
                assert lab.store.verify(lab.artifact(h))
    assert {s["why"] for s in report["skipped"]} <= {"kept", "newest", "pinned"}
    assert "newest" in {s["why"] for s in report["skipped"]}
    toy.CALLS.clear()
    b.run()
    assert dict(toy.CALLS) == {"train": 1}


def test_running_guard(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    lab.store.put_value("orphan")
    lab.db.update_experiment(a.id, status="running")
    assert lab.gc(grace=0)["deleted"]  # previews are always allowed
    with pytest.raises(StorageBusy, match=a.id):
        lab.gc(dry_run=False, grace=0)
    with pytest.raises(StorageBusy):
        lab.evict(execs(a)["train"], dry_run=False)
    with pytest.raises(StorageBusy):
        lab.clean()
    assert lab.gc(dry_run=False, grace=0, force=True)["deleted"]
    with pytest.raises(ValueError, match="running"):
        a.delete()
    assert a.delete(force=True) == [a.id]


def test_clean_then_load(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    rows = a.load("extract")
    assert rows.is_dir()
    stale = lab.home / "tmp" / "deadbeef"
    stale.mkdir(parents=True)
    (stale / "half-written").write_text("x")
    assert lab.storage()["scratch"]["checkouts"] > 0
    out = lab.clean()
    assert out["removed"] >= 2 and out["bytes"] > 0
    assert not stale.exists() and not rows.exists()
    assert sorted(p.name for p in a.load("extract").iterdir()) == ["000.txt", "001.txt", "002.txt", "003.txt"]
    assert lab.storage()["store"]["collectable"]["objects"] == 0  # objects untouched


def test_keep_flag_in_summary_and_tree(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}, draft=True)
    assert a.summary()["keep"] is False
    a.keep()
    assert a.summary()["keep"] is True and lab.tree()[0]["keep"] is True
    assert lab.get(a.id).kept
    a.keep(False)
    assert lab.tree()[0]["keep"] is False
