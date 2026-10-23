import warnings

import pytest

from dendrite import Lab
from dendrite.guard import UndeclaredReadWarning

from . import toy


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A project directory as the working directory, with a lab inside it."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("lr: 1\n")
    return tmp_path


def make_lab(project, monkeypatch, mode):
    monkeypatch.setenv("DENDRITE_CHECK_READS", mode)
    return Lab(project / ".dendrite")


def run(lab, source, **params):
    return lab.create(toy.toy_reads, inputs={"src": source}, params={"read_extra": params}).run()


def test_undeclared_project_read_warns(project, monkeypatch, source):
    lab = make_lab(project, monkeypatch, "warn")
    with pytest.warns(UndeclaredReadWarning, match=r"read_extra.*config\.yaml"):
        exp = run(lab, source, extra=str(project / "config.yaml"))
    assert exp.load("read_extra").endswith("lr: 1\nx")
    lab.db.dispose()


def test_error_mode_fails_even_if_swallowed(project, monkeypatch, source):
    lab = make_lab(project, monkeypatch, "error")
    with pytest.raises(Exception, match="not one of its declared inputs"):
        run(lab, source, extra=str(project / "config.yaml"), swallow=True)
    assert lab.db.find_success("x") == []
    with lab.db.ro() as conn:
        from sqlalchemy import select
        from dendrite.db import executions

        assert [r.status for r in conn.execute(select(executions.c.status))] == ["failed"]
    lab.db.dispose()


@pytest.mark.parametrize("mode", ["warn", "off"])
def test_declared_inputs_workdir_and_outside_reads_are_silent(project, monkeypatch, source, tmp_path_factory, mode):
    outside = tmp_path_factory.mktemp("elsewhere") / "notes.txt"
    outside.write_text("far away")
    lab = make_lab(project, monkeypatch, mode)
    with warnings.catch_warnings():
        warnings.simplefilter("error", UndeclaredReadWarning)
        run(lab, source)  # declared input (a checkout) + workdir only
        run(lab, source, extra=str(outside))  # outside the project directory
    lab.db.dispose()


def test_off_mode_ignores_project_reads(project, monkeypatch, source):
    lab = make_lab(project, monkeypatch, "off")
    with warnings.catch_warnings():
        warnings.simplefilter("error", UndeclaredReadWarning)
        run(lab, source, extra=str(project / "config.yaml"))
    lab.db.dispose()


def test_plan_does_not_trip_the_guard(project, monkeypatch, source):
    lab = make_lab(project, monkeypatch, "error")
    a = run(lab, source)
    assert a.fork({"read_extra.extra": str(project / "config.yaml")}, draft=True).plan()["complete"]
    lab.db.dispose()
