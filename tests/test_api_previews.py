from fastapi.testclient import TestClient

from dendrite.server import create_app


def test_download_directory_member(lab, tmp_path):
    folder = tmp_path / "reports"
    (folder / "nested").mkdir(parents=True)
    (folder / "nested" / "a report.csv").write_text("x,y\n1,2\n")
    ref = lab.ingest(folder)
    client = TestClient(create_app(lab))

    response = client.get(f"/api/artifacts/{ref.hash}/download", params={"path": "nested/a report.csv"})
    assert response.status_code == 200
    assert response.text == "x,y\n1,2\n"
    assert "a%20report.csv" in response.headers["content-disposition"]
    partial = client.get(
        f"/api/artifacts/{ref.hash}/download",
        params={"path": "nested/a report.csv"},
        headers={"Range": "bytes=0-2"},
    )
    assert partial.status_code == 206
    assert partial.content == b"x,y"


def test_directory_preview_only_serves_manifest_members(lab, tmp_path):
    folder = tmp_path / "reports"
    folder.mkdir()
    (folder / "report.txt").write_text("report")
    ref = lab.ingest(folder)
    client = TestClient(create_app(lab))
    for path in ["../report.txt", "/etc/passwd", "absent.txt", "report.txt/../report.txt"]:
        assert client.get(f"/api/artifacts/{ref.hash}/download", params={"path": path}).status_code == 404
    assert client.get(f"/api/artifacts/{ref.hash}/download").status_code == 400
    lab.store.delete(ref.hash)
    assert client.get(f"/api/artifacts/{ref.hash}/download", params={"path": "report.txt"}).status_code == 410


def test_download_file_and_missing_blob(lab, tmp_path):
    file = tmp_path / "large.pdf"
    file.write_bytes(b"%PDF-1.7\n" + b"0" * 70000)
    ref = lab.ingest(file)
    client = TestClient(create_app(lab))
    response = client.get(f"/api/artifacts/{ref.hash}/download")
    assert response.status_code == 200
    assert response.content == file.read_bytes()
    lab.store.delete(ref.hash)
    assert client.get(f"/api/artifacts/{ref.hash}/download").status_code == 410


def test_directory_preview_loads_only_the_selected_blob(lab, tmp_path, monkeypatch):
    folder = tmp_path / "reports"
    folder.mkdir()
    (folder / "small.txt").write_text("small")
    (folder / "large.bin").write_bytes(b"x" * 100000)
    ref = lab.ingest(folder)
    members = {p: h for p, h, _ in lab.store.manifest(ref)}
    loaded = []
    checkout = lab.store._checkout

    def record_checkout(h, dest):
        loaded.append(h)
        checkout(h, dest)

    monkeypatch.setattr(lab.store, "_checkout", record_checkout)
    client = TestClient(create_app(lab))
    response = client.get(f"/api/artifacts/{ref.hash}/download", params={"path": "small.txt"})
    assert response.status_code == 200 and response.text == "small"
    assert loaded == [members["small.txt"]]
    # A missing sibling must not prevent previewing this file or trigger extra checkouts.
    lab.store.delete(members["large.bin"])
    response = client.get(f"/api/artifacts/{ref.hash}/download", params={"path": "small.txt"})
    assert response.status_code == 200 and response.text == "small"
    assert loaded == [members["small.txt"]]


def test_deleted_member_cannot_be_served_from_preview_cache(lab, tmp_path):
    folder = tmp_path / "reports"
    folder.mkdir()
    (folder / "report.txt").write_text("cached report")
    ref = lab.ingest(folder)
    client = TestClient(create_app(lab))
    url = f"/api/artifacts/{ref.hash}/download"
    assert client.get(url, params={"path": "report.txt"}).status_code == 200
    lab.store.delete(lab.store.manifest(ref)[0][1])
    assert client.get(url, params={"path": "report.txt"}).status_code == 410
