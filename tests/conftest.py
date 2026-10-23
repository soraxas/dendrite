import os
import uuid

import pytest

os.environ.setdefault("PREFECT_LOGGING_LEVEL", "WARNING")

# Postgres for tests: DENDRITE_TEST_PG_URL if set, otherwise a throwaway container (testcontainers)
# when Docker is available. DENDRITE_TEST_PG=require (CI) turns "unavailable" into a failure;
# DENDRITE_TEST_DB=postgres runs the whole suite's `lab` fixture on Postgres.
PG_IMAGE = "postgres:17-alpine"


@pytest.fixture(scope="session", autouse=True)
def prefect_harness():
    from prefect.testing.utilities import prefect_test_harness

    with prefect_test_harness(server_startup_timeout=120):
        yield


def _unavailable(reason: str):
    if os.environ.get("DENDRITE_TEST_PG") == "require":
        pytest.fail(f"Postgres required but unavailable: {reason}")
    pytest.skip(reason)


@pytest.fixture(scope="session")
def pg_url():
    url = os.environ.get("DENDRITE_TEST_PG_URL")
    if url:
        yield url
        return
    try:
        from testcontainers.community.postgres import PostgresContainer
    except ImportError:
        try:
            from testcontainers.postgres import PostgresContainer
        except ImportError:
            _unavailable("testcontainers is not installed")
    container = PostgresContainer(PG_IMAGE, driver="psycopg")
    try:
        container.start()
    except Exception as e:  # no Docker daemon, image pull failed, …
        _unavailable(f"could not start {PG_IMAGE}: {e}")
    try:
        yield container.get_connection_url()
    finally:
        container.stop()


@pytest.fixture
def pg_schema_url(pg_url):
    """A fresh schema per test, dropped afterwards; returns a URL whose search_path is it."""
    import sqlalchemy as sa

    schema = f"t_{uuid.uuid4().hex[:8]}"
    admin = sa.create_engine(pg_url)
    with admin.begin() as c:
        c.execute(sa.text(f"CREATE SCHEMA {schema}"))
    url = sa.engine.make_url(pg_url).update_query_dict({"options": f"-csearch_path={schema}"})
    yield url.render_as_string(hide_password=False)
    with admin.begin() as c:
        c.execute(sa.text(f"DROP SCHEMA {schema} CASCADE"))
    admin.dispose()


@pytest.fixture
def lab(request, tmp_path):
    from dendrite import Lab

    db_url = request.getfixturevalue("pg_schema_url") if os.environ.get("DENDRITE_TEST_DB") == "postgres" else None
    lab = Lab(tmp_path / "lab", db_url=db_url)
    yield lab
    lab.db.dispose()


@pytest.fixture
def source(tmp_path):
    p = tmp_path / "data.csv"
    p.write_text("a,b\n1,2\n3,4\n5,6\n7,8\n")
    return p
