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
    try:  # the constructor already connects to Docker, so it belongs inside the try
        container = PostgresContainer(PG_IMAGE, driver="psycopg")
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

    if os.environ.get("DENDRITE_TEST_REMOTE") == "1":
        if request.node.get_closest_marker("local_only"):
            pytest.skip(request.node.get_closest_marker("local_only").args[0])
        lab = request.getfixturevalue("hosted").lab(tmp_path / "lab")
        yield lab
        lab.db.dispose()
        return
    db_url = request.getfixturevalue("pg_schema_url") if os.environ.get("DENDRITE_TEST_DB") == "postgres" else None
    lab = Lab(tmp_path / "lab", db_url=db_url)
    yield lab
    lab.db.dispose()


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "local_only(reason): reaches into local metadata or store internals; skipped in remote mode"
    )


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def moto_s3():
    """A moto S3 endpoint for the session: ``(storage options, bucket)``."""
    moto_server = pytest.importorskip("moto.moto_server.threaded_moto_server")
    s3fs = pytest.importorskip("s3fs")
    port = _free_port()
    server = moto_server.ThreadedMotoServer(ip_address="127.0.0.1", port=port)
    server.start()
    opts = {"endpoint_url": f"http://127.0.0.1:{port}", "key": "test", "secret": "test"}
    bucket = f"hosted-{uuid.uuid4().hex[:10]}"
    s3fs.S3FileSystem(**opts).mkdir(bucket)
    yield opts, bucket
    server.stop()


class HostedServer:
    """A dendrite server running in a thread (Postgres + moto), with helpers for tests."""

    def __init__(self, hosted, url: str):
        self.server, self.url = hosted, url
        admin = hosted.control.upsert_user("dev", "admin@test", "admin@test", "Admin", admin=True)
        self.admin_token, _ = hosted.control.create_token(admin["id"], name="tests")

    def project(self, name: str | None = None, **kw) -> str:
        name = name or f"t{uuid.uuid4().hex[:12]}"
        self.server.create_project(name, **kw)
        return name

    def user(self, email: str, project: str | None = None, role: str | None = None) -> str:
        """A signed-in user (optionally a member of ``project``); returns their token."""
        u = self.server.control.upsert_user("dev", email, email, email.split("@")[0])
        if project:
            self.server.control.set_member(project, u["id"], role)
        return self.server.control.create_token(u["id"], name=email)[0]

    def lab(self, home, project: str | None = None, token: str | None = None):
        from dendrite import Lab

        return Lab(
            home,
            server=self.url,
            project=project or self.project(),
            client_options={"token": token or self.admin_token},
        )


@pytest.fixture(scope="session")
def hosted(pg_url, moto_s3, tmp_path_factory):
    import threading
    import time

    import uvicorn

    from dendrite.hosted.app import Hosted, create_server_app
    from dendrite.hosted.settings import ServerSettings

    opts, bucket = moto_s3
    port = _free_port()
    settings = ServerSettings(
        base_url=f"http://127.0.0.1:{port}",
        database_url=pg_url,
        store_url=f"s3://{bucket}/srv",
        home=tmp_path_factory.mktemp("server"),
        store_options=opts,
        auth={"provider": "dev"},
        # moto accepts PUTs whose bytes don't match their checksum, so make the server re-hash.
        verify_uploads="hash",
        store_enforces_sha256=False,
        max_open_projects=6,  # every test makes a project; keep Postgres connections bounded
    )
    server = Hosted(settings)
    app = create_server_app(settings, server)
    uv = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            lifespan="off",
            timeout_keep_alive=settings.keep_alive_seconds,
        )
    )
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    deadline = time.time() + 30
    while not uv.started:
        if time.time() > deadline:
            raise RuntimeError("the test server didn't start")
        time.sleep(0.05)
    yield HostedServer(server, settings.base_url)
    uv.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
def source(tmp_path):
    p = tmp_path / "data.csv"
    p.write_text("a,b\n1,2\n3,4\n5,6\n7,8\n")
    return p
