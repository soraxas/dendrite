"""Runs the core scenarios against PostgreSQL when DENDRITE_TEST_PG_URL is set."""

import os
import uuid

import pytest
import sqlalchemy as sa

from dendrite import Lab

from . import toy

PG = os.environ.get("DENDRITE_TEST_PG_URL")
pytestmark = pytest.mark.skipif(not PG, reason="set DENDRITE_TEST_PG_URL=postgresql+psycopg://… to run")


@pytest.fixture
def pg_lab(tmp_path):
    schema = f"t_{uuid.uuid4().hex[:8]}"
    admin = sa.create_engine(PG)
    with admin.begin() as c:
        c.execute(sa.text(f"CREATE SCHEMA {schema}"))
    url = sa.engine.make_url(PG).update_query_dict({"options": f"-csearch_path={schema}"})
    lab = Lab(tmp_path / "pg", db_url=url.render_as_string(hide_password=False))
    yield lab
    lab.db.dispose()
    with admin.begin() as c:
        c.execute(sa.text(f"DROP SCHEMA {schema} CASCADE"))
    admin.dispose()


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
