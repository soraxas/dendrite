"""Hosted mode on Google Cloud Storage, against fake-gcs-server (Docker; skipped without it).

The emulator serves signed URLs and reports CRC32C, but doesn't enforce
``x-goog-if-generation-match``: no-overwrite is GCS's behaviour, checked by
``dendrite server check`` against a real bucket.
"""

import hashlib
import json
import threading
import time
import uuid

import httpx
import pytest

from . import toy
from .conftest import HostedServer, _free_port

IMAGE = "fsouza/fake-gcs-server:1.52.2"


@pytest.fixture(scope="module")
def fake_gcs():
    pytest.importorskip("gcsfs")
    try:
        from testcontainers.core.container import DockerContainer
        from testcontainers.core.waiting_utils import wait_for_logs
    except ImportError:
        pytest.skip("testcontainers is not installed")
    port = _free_port()
    c = DockerContainer(IMAGE).with_bind_ports(4443, port)
    c = c.with_command(f"-scheme http -port 4443 -public-host 127.0.0.1:{port}")
    try:
        c.start()
    except Exception as e:  # no Docker
        pytest.skip(f"could not start {IMAGE}: {e}")
    try:
        wait_for_logs(c, "server started", timeout=60)
        endpoint = f"http://127.0.0.1:{port}"
        httpx.post(f"{endpoint}/storage/v1/b?project=test", json={"name": "lineage"}).raise_for_status()
        yield endpoint
    finally:
        c.stop()


@pytest.fixture(scope="module")
def signing_key(tmp_path_factory):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    path = tmp_path_factory.mktemp("gcs") / "key.json"
    path.write_text(
        json.dumps(
            {
                "type": "service_account",
                "project_id": "test",
                "private_key_id": "k",
                "private_key": pem.decode(),
                "client_email": "dendrite@test.iam.gserviceaccount.com",
                "client_id": "1",
                "token_uri": "http://127.0.0.1:1/token",
            }
        )
    )
    return str(path)


@pytest.fixture(scope="module")
def gcs_hosted(pg_url, fake_gcs, signing_key, tmp_path_factory):
    import uvicorn

    from dendrite.hosted.app import Hosted, create_server_app
    from dendrite.hosted.settings import ServerSettings

    port = _free_port()
    settings = ServerSettings(
        base_url=f"http://127.0.0.1:{port}",
        database_url=pg_url,
        store_url=f"gs://lineage/srv-{uuid.uuid4().hex[:8]}",
        home=tmp_path_factory.mktemp("gcs-server"),
        store_options={"endpoint_url": fake_gcs, "token": "anon"},
        gcs_signing_key=signing_key,
        auth={"provider": "dev"},
        max_open_projects=4,
    )
    server = Hosted(settings)
    app = create_server_app(settings, server)
    uv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    while not uv.started:
        time.sleep(0.05)
    yield HostedServer(server, settings.base_url)
    uv.should_exit = True
    thread.join(timeout=10)


def test_pipelines_run_and_share_results_through_gcs(gcs_hosted, tmp_path, source):
    project = gcs_hosted.project()
    alice = gcs_hosted.lab(tmp_path / "alice", project)
    toy.CALLS.clear()
    a = alice.create(toy.toy, inputs={"src": source}).run()
    assert a.status == "completed" and a.load("featurize") == [3, 7, 11, 15]
    bob = gcs_hosted.lab(tmp_path / "bob", project)
    b = bob.create(toy.toy, inputs={"src": source}).run()
    assert {r["status"] for r in b.node_runs()} == {"cached"} and toy.CALLS["extract"] == 1
    # The outputs are objects in the bucket, at their content addresses.
    store = gcs_hosted.server.project(project).store
    rows = a.output("extract")
    assert store.has(rows.hash) and store.verify(rows)
    for lab in (alice, bob):
        lab.db.dispose()


def test_a_large_file_goes_up_with_a_signed_put(gcs_hosted, tmp_path, monkeypatch):
    from dendrite.remote import ServerStore

    lab = gcs_hosted.lab(tmp_path / "big")
    seen = []
    real = ServerStore._upload
    monkeypatch.setattr(ServerStore, "_upload", lambda self, plan, src: (seen.append(plan), real(self, plan, src))[1])
    f = tmp_path / "big.bin"
    f.write_bytes(bytes(range(256)) * 4096)  # 1 MiB: above the inline limit
    ref = lab.ingest(f)
    assert seen and seen[0]["method"] == "put" and seen[0]["checksum"] == "crc32c"
    assert seen[0]["headers"] == {"x-goog-if-generation-match": "0"}
    assert lab.store.read_bytes(ref.hash) == f.read_bytes()  # a signed GET from the bucket
    lab.db.dispose()


def test_a_crc32c_mismatch_is_rejected(gcs_hosted, tmp_path):
    project = gcs_hosted.project()
    p = gcs_hosted.server.project(project)
    data = b"x" * 300_000
    h = hashlib.sha256(data).hexdigest()
    plan = p.broker.plan(h, len(data))
    assert httpx.put(plan["url"], content=data, headers=plan["headers"]).status_code == 200
    out = p.broker.commit(plan["upload"], crc32c="AAAAAA==")  # what a corrupted transfer would report
    assert out["state"] == "failed" and "CRC32C" in out["error"]
    assert not p.store.has(h)  # the bad object was removed


def test_server_check_on_gcs(gcs_hosted):
    from dendrite.hosted.check import run_checks

    seen = {}
    run_checks(gcs_hosted.server.settings, lambda status, name, detail: seen.setdefault(name, status))
    assert seen["presigned upload"] == "ok" and seen["presigned download"] == "ok"
    assert seen["no overwrites"] == "warn"  # the emulator doesn't enforce it; real GCS does
