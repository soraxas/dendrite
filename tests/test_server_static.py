from fastapi.testclient import TestClient

from dendrite.server import create_app, ui_mode


def test_serves_react_bundle_when_present(lab, tmp_path):
    ui = tmp_path / "ui"
    (ui / "dist" / "assets").mkdir(parents=True)
    (ui / "dist" / "index.html").write_text('<html><script src="./assets/app.js"></script>react</html>')
    (ui / "dist" / "assets" / "app.js").write_text("console.log('app')")
    assert ui_mode(ui) == "react"

    client = TestClient(create_app(lab, ui_dir=ui))
    assert "react" in client.get("/").text
    asset = client.get("/assets/app.js")
    assert asset.status_code == 200 and "console.log" in asset.text
    assert client.get("/api/info").json()["ui"] == "react"
    assert client.get("/api/experiments").json() == []  # API routes still win over the static mount


def test_without_a_bundle_serves_the_api_and_a_build_hint(lab, tmp_path):
    ui = tmp_path / "ui"
    ui.mkdir()
    assert ui_mode(ui) == "missing"
    client = TestClient(create_app(lab, ui_dir=ui))
    page = client.get("/")
    assert page.status_code == 200 and "scripts/build-ui.sh" in page.text
    assert client.get("/api/info").json()["ui"] == "missing"
    assert client.get("/api/experiments").json() == []
