"""Wheel packaging of the UI bundle and the build guard (builds a copy of the project)."""

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(shutil.which("uv") is None, reason="uv is required to build wheels")


def project_copy(dest: Path, with_bundle: bool) -> Path:
    for name in ("pyproject.toml", "hatch_build.py", "README.md"):
        shutil.copy(ROOT / name, dest / name)
    shutil.copytree(ROOT / "src", dest / "src", ignore=shutil.ignore_patterns("dist", "__pycache__", "*.pyc"))
    if with_bundle:
        dist = dest / "src" / "dendrite" / "ui" / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text("<html>stub</html>")
        (dist / "assets" / "app.js").write_text("//")
    return dest


def build(project: Path, out: Path):
    env = {k: v for k, v in os.environ.items() if k != "DENDRITE_SKIP_UI_CHECK"}
    return subprocess.run(
        ["uv", "build", "--wheel", "--no-sources", "-o", str(out), str(project)],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )


def test_wheel_contains_the_ui_bundle(tmp_path):
    (tmp_path / "proj").mkdir()
    proj = project_copy(tmp_path / "proj", with_bundle=True)
    res = build(proj, tmp_path / "out")
    assert res.returncode == 0, res.stderr[-2000:]
    (wheel,) = (tmp_path / "out").glob("*.whl")
    names = set(zipfile.ZipFile(wheel).namelist())
    assert {"dendrite/ui/dist/index.html", "dendrite/ui/dist/assets/app.js"} <= names
    assert "dendrite/ui/legacy.html" not in names


def test_build_fails_without_bundle(tmp_path):
    (tmp_path / "proj").mkdir()
    proj = project_copy(tmp_path / "proj", with_bundle=False)
    res = build(proj, tmp_path / "out")
    assert res.returncode != 0
    assert "scripts/build-ui.sh" in res.stderr
