from fastapi.testclient import TestClient

from dendrite.server import create_app, ui_mode


def test_serves_react_bundle_when_present(lab, tmp_path):
    ui = tmp_path / "ui"
    (ui / "dist" / "assets").mkdir(parents=True)
    (ui / "legacy.html").write_text("<html>legacy</html>")
    (ui / "dist" / "index.html").write_text('<html><script src="./assets/app.js"></script>react</html>')
    (ui / "dist" / "assets" / "app.js").write_text("console.log('app')")
    assert ui_mode(ui) == "react"

    client = TestClient(create_app(lab, ui_dir=ui))
    assert "react" in client.get("/").text
    asset = client.get("/assets/app.js")
    assert asset.status_code == 200 and "console.log" in asset.text
    assert client.get("/api/info").json()["ui"] == "react"
    assert client.get("/api/experiments").json() == []  # API routes still win over the static mount


def test_falls_back_to_legacy_without_bundle(lab, tmp_path):
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "legacy.html").write_text("<html>legacy page</html>")
    assert ui_mode(ui) == "legacy"
    client = TestClient(create_app(lab, ui_dir=ui))
    assert "legacy page" in client.get("/").text
    assert client.get("/api/info").json()["ui"] == "legacy"
