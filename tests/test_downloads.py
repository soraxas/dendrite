"""Artifact downloads and previews: local files, streamed remote objects, and signed redirects."""

import socket
import uuid

import pytest
from fastapi.testclient import TestClient

from dendrite import Lab
from dendrite.server import AUTO_PREVIEW_BYTES, create_app

from .test_api import no_bundle

PAYLOAD = bytes(range(256)) * 40  # 10 KiB, every byte value


def _files(tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(PAYLOAD)
    d = tmp_path / "frames"
    (d / "sub").mkdir(parents=True)
    (d / "a.txt").write_text("first")
    (d / "sub" / "b.bin").write_bytes(PAYLOAD[:100])
    return f, d


def _remote_lab(lab, tmp_path, url, **opts):
    return Lab(tmp_path / "remote", db_url=lab.settings.db_url, store_url=url, storage_options=opts or None)


@pytest.mark.local_only("LocalStore-specific")
def test_local_downloads_serve_the_blob_with_ranges(lab, tmp_path):
    f, d = _files(tmp_path)
    file_ref, dir_ref = lab.ingest(f), lab.ingest(d)
    client = TestClient(create_app(lab, ui_dir=no_bundle(tmp_path)))

    detail = client.get(f"/api/artifacts/{file_ref.hash}").json()
    assert detail["present"] and detail["auto_preview_bytes"] is None  # local: always preview

    r = client.get(f"/api/artifacts/{file_ref.hash}/download")
    assert r.content == PAYLOAD and r.headers["content-type"] == "video/mp4"
    assert "clip.mp4" in r.headers["content-disposition"]
    part = client.get(f"/api/artifacts/{file_ref.hash}/download", headers={"Range": "bytes=10-19"})
    assert part.status_code == 206 and part.content == PAYLOAD[10:20]

    member = client.get(f"/api/artifacts/{dir_ref.hash}/download", params={"path": "sub/b.bin"})
    assert member.content == PAYLOAD[:100]
    assert client.get(f"/api/artifacts/{dir_ref.hash}/download", params={"path": "../x"}).status_code == 404
    # Served straight from the object store: no checkout copies were made.
    assert not (lab.store.cache_dir / "checkouts").exists()


def test_remote_downloads_stream_without_a_checkout(lab, tmp_path):
    remote = _remote_lab(lab, tmp_path, f"memory://dendrite-{uuid.uuid4().hex}")
    assert remote.store.remote
    f, d = _files(tmp_path)
    file_ref, dir_ref = remote.ingest(f), remote.ingest(d)
    client = TestClient(create_app(remote, ui_dir=no_bundle(tmp_path)))
    url = f"/api/artifacts/{file_ref.hash}/download"

    assert client.get(f"/api/artifacts/{file_ref.hash}").json()["auto_preview_bytes"] == AUTO_PREVIEW_BYTES

    r = client.get(url)
    assert r.status_code == 200 and r.content == PAYLOAD
    assert r.headers["accept-ranges"] == "bytes" and r.headers["content-type"] == "video/mp4"
    for header, start, end in (
        ("bytes=100-199", 100, 199),
        ("bytes=10000-", 10000, 10239),
        ("bytes=-40", 10200, 10239),
    ):
        part = client.get(url, headers={"Range": header})
        assert part.status_code == 206, header
        assert part.content == PAYLOAD[start : end + 1]
        assert part.headers["content-range"] == f"bytes {start}-{end}/{len(PAYLOAD)}"
    assert client.get(url, headers={"Range": "bytes=99999-"}).status_code == 416
    # memory:// can't sign, so asking for a redirect still streams.
    assert client.get(url, params={"redirect": 1}, follow_redirects=False).content == PAYLOAD
    assert not (remote.store.cache_dir / "checkouts").exists()

    # The detail view checks a directory's manifest only; a missing member surfaces on download.
    member = next(h for p, h, _ in remote.store.manifest(dir_ref) if p == "sub/b.bin")
    remote.store.delete(member)
    assert client.get(f"/api/artifacts/{dir_ref.hash}").json()["present"] is True
    assert remote.store.verify(dir_ref) is False  # cache hits still check every member
    gone = client.get(f"/api/artifacts/{dir_ref.hash}/download", params={"path": "sub/b.bin"})
    assert gone.status_code == 410
    assert client.get(f"/api/artifacts/{dir_ref.hash}/download", params={"path": "a.txt"}).text == "first"
    remote.db.dispose()


def test_remote_download_reuses_an_existing_checkout(lab, tmp_path):
    remote = _remote_lab(lab, tmp_path, f"memory://dendrite-{uuid.uuid4().hex}")
    f, _ = _files(tmp_path)
    ref = remote.ingest(f)
    local = remote.store.load(ref)  # what a step that read this input leaves behind
    assert remote.store.local_path(ref.hash) == local
    client = TestClient(create_app(remote, ui_dir=no_bundle(tmp_path)))

    def no_remote_reads(h):
        raise AssertionError("read the remote object although a checkout exists")

    remote.store.open_object = no_remote_reads
    assert client.get(f"/api/artifacts/{ref.hash}/download").content == PAYLOAD
    remote.store.delete(ref.hash)  # a checkout never stands in for a deleted blob
    assert client.get(f"/api/artifacts/{ref.hash}/download").status_code == 410
    remote.db.dispose()


@pytest.fixture
def s3():
    moto_server = pytest.importorskip("moto.moto_server.threaded_moto_server")
    s3fs = pytest.importorskip("s3fs")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = moto_server.ThreadedMotoServer(ip_address="127.0.0.1", port=port)
    server.start()
    opts = {"endpoint_url": f"http://127.0.0.1:{port}", "key": "test", "secret": "test"}
    # moto servers in one process share their buckets, so don't collide with other tests'.
    bucket = f"downloads-{uuid.uuid4().hex[:12]}"
    s3fs.S3FileSystem(**opts).mkdir(bucket)
    yield opts, bucket
    server.stop()


def test_s3_downloads_redirect_to_a_signed_url(lab, tmp_path, s3):
    import httpx

    s3_opts, bucket = s3
    remote = _remote_lab(lab, tmp_path, f"s3://{bucket}/runs", **s3_opts)
    f, d = _files(tmp_path)
    file_ref, dir_ref = remote.ingest(f), remote.ingest(d)
    client = TestClient(create_app(remote, ui_dir=no_bundle(tmp_path)))
    url = f"/api/artifacts/{file_ref.hash}/download"

    r = client.get(url, params={"redirect": 1}, follow_redirects=False)
    assert r.status_code == 307
    signed = r.headers["location"]
    assert signed.startswith(s3_opts["endpoint_url"]) and "Signature" in signed
    direct = httpx.get(signed)
    assert direct.content == PAYLOAD and "clip.mp4" in direct.headers["content-disposition"]

    r = client.get(
        f"/api/artifacts/{dir_ref.hash}/download", params={"path": "a.txt", "redirect": 1}, follow_redirects=False
    )
    assert r.status_code == 307 and httpx.get(r.headers["location"]).text == "first"
    # Without redirect (the in-page viewer, which would need CORS) the server streams it.
    assert client.get(url).content == PAYLOAD

    no_sign = TestClient(create_app(remote, ui_dir=no_bundle(tmp_path), presign=False))
    assert no_sign.get(url, params={"redirect": 1}, follow_redirects=False).status_code == 200
    remote.db.dispose()
