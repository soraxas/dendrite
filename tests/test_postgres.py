"""Core, draft and storage scenarios on PostgreSQL (a throwaway container unless
DENDRITE_TEST_PG_URL is set; skipped without Docker). The whole suite can also run on
Postgres with DENDRITE_TEST_DB=postgres."""

import pytest

from dendrite import Lab
from dendrite.storage import StorageBusy

from . import toy


@pytest.fixture
def pg_lab(tmp_path, pg_schema_url):
    lab = Lab(tmp_path / "pg", db_url=pg_schema_url)
    assert lab.db.engine.dialect.name == "postgresql"
    yield lab
    lab.db.dispose()


def test_postgres_fork_pin_lineage_and_lease(pg_lab, source):
    toy.CALLS.clear()
    a = pg_lab.create(toy.toy, inputs={"src": source}).run()
    b = a.fork({"train.iterations": 2}).run()
    c = a.fork({"featurize.scale": 5}, pin={"featurize": a}).run()
    assert dict(toy.CALLS) == {"extract": 1, "featurize": 1, "train": 2}
    assert {n["node"]: n["status"] for n in c.node_runs()} == {
        "extract": "cached",
        "featurize": "pinned",
        "train": "cached",
    }
    assert len(pg_lab.lineage(b.output("train", "model"))["ancestors"]) == 3
    assert pg_lab.db.acquire_lease("s", "w1", 10) and not pg_lab.db.acquire_lease("s", "w2", 10)
    assert pg_lab.db.experiments_using_artifact(a.output("featurize").hash)


def test_postgres_drafts_compare_and_set(pg_lab, source):
    a = pg_lab.create(toy.toy, inputs={"src": source}).run()
    d = a.fork({"train.iterations": 20}, draft=True)
    assert d.plan()["totals"]["run"] == 1
    d.update({"train.iterations": 10})
    assert d.plan()["totals"]["run"] == 0
    d.run()
    assert not pg_lab.db.update_experiment_if(d.id, "draft", name="late edit")


def test_postgres_keep_delete_reparent_and_gc(pg_lab, source):
    a = pg_lab.create(toy.toy, inputs={"src": source}).run()
    b = a.fork({"train.iterations": 20}).run()
    c = b.fork({"train.iterations": 30}).run()
    assert pg_lab.get(a.id).kept is False  # keep comes from an outer join on experiment_flags
    a.keep()
    assert pg_lab.tree()[0]["keep"] is True and pg_lab.get(a.id).kept
    est = pg_lab.deletion_estimate([b.id])
    assert pg_lab.delete([b.id], forks="keep") == {"deleted": [b.id], "reparented": {c.id: a.id}}
    assert c.refresh().parent_id == a.id
    assert pg_lab.gc(dry_run=False, grace=0)["bytes"] == est["bytes"]
    assert c.load("featurize") == [3, 7, 11, 15]
    with pytest.raises(ValueError, match="fork"):
        pg_lab.delete([a.id])
    assert pg_lab.delete([a.id, c.id])["deleted"] == [c.id, a.id]  # children first: parent_id is a FK
    assert pg_lab.experiments() == []


def test_postgres_running_guard_and_stop(pg_lab, source):
    a = pg_lab.create(toy.toy, inputs={"src": source}).run()
    pg_lab.db.update_experiment(a.id, status="running")
    with pytest.raises(StorageBusy):
        pg_lab.gc(dry_run=False)
    with pytest.raises(ValueError, match="running"):
        a.delete()
    a.mark_stopped()
    assert a.delete() == [a.id]
