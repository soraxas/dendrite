import pytest

from dendrite.store import FsspecStore, LocalStore


@pytest.fixture(params=["local", "memory"])
def store(request, tmp_path):
    if request.param == "local":
        return LocalStore(tmp_path / "store")
    import uuid

    return FsspecStore(f"memory://dendrite-{uuid.uuid4().hex}", tmp_path / "cache")


def test_file_dedup_and_load(store, tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("same")
    b.write_text("same")
    ra, rb = store.put_path(a), store.put_path(b)
    assert ra.hash == rb.hash and ra.kind == "file"
    assert store.load(ra).read_text() == "same"


def test_dir_hash_is_content_derived(store, tmp_path):
    import os
    import time

    for name in ("d1", "d2"):
        d = tmp_path / name
        (d / "sub").mkdir(parents=True)
        (d / "x.txt").write_text("x")
        (d / "sub" / "y.txt").write_text("y")
    os.utime(tmp_path / "d2" / "x.txt", (time.time() - 1000, time.time() - 1000))
    r1, r2 = store.put_path(tmp_path / "d1"), store.put_path(tmp_path / "d2")
    assert r1.hash == r2.hash and r1.kind == "dir"
    out = store.load(r1)
    assert (out / "sub" / "y.txt").read_text() == "y"
    assert store.verify(r1)
    store.delete(store.manifest(r1)[0][1])
    assert not store.verify(r1)


def test_value_roundtrip(store):
    ref = store.put_value({"a": [1, 2]})
    assert ref.kind == "value"
    assert store.load(ref) == {"a": [1, 2]}


def test_s3_backend_with_moto(tmp_path, source):
    moto_server = pytest.importorskip("moto.moto_server.threaded_moto_server")
    pytest.importorskip("s3fs")
    import socket

    from dendrite import Lab

    from . import toy

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = moto_server.ThreadedMotoServer(ip_address="127.0.0.1", port=port)
    server.start()
    try:
        opts = {"endpoint_url": f"http://127.0.0.1:{port}", "key": "test", "secret": "test"}
        import s3fs

        s3fs.S3FileSystem(**opts).mkdir("lineage")
        lab = Lab(tmp_path / "s3lab", store_url="s3://lineage/runs", storage_options=opts)
        a = lab.create(toy.toy, inputs={"src": source}).run()
        b = a.fork({"train.iterations": 3}).run()
        assert {n["status"] for n in b.node_runs() if n["node"] != "train"} == {"cached"}
        assert b.load("featurize") == [3, 7, 11, 15]
        assert lab.store.fs.exists(f"lineage/runs/objects/{a.output('extract').hash[:2]}")
    finally:
        server.stop()
