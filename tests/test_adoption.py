"""Easy adoption: steps outside a pipeline, rerun, and DENDRITE_CACHE modes."""

import math
import random
import warnings
from pathlib import Path

import pytest

import dendrite
from dendrite import NondeterministicStep, pipeline, runtime, workdir

CALLS = {"count": 0, "lines": 0, "noisy": 0}


@dendrite.step
def count_lines(text: Path, skip_blank: bool = True) -> int:
    CALLS["count"] += 1
    lines = text.read_text().splitlines()
    return sum(1 for line in lines if line.strip() or not skip_blank)


@dendrite.step
def upper(text: Path) -> Path:
    CALLS["lines"] += 1
    out = workdir() / "upper.txt"
    out.write_text(text.read_text().upper())
    return out


@dendrite.step(cache=True)
def noisy(n: int = 3) -> list:
    CALLS["noisy"] += 1
    return [random.random() for _ in range(n)]  # unseeded: a hidden input


@dendrite.step
def total(values: set) -> int:
    return sum(values)


@pipeline(name="adopt_pipe")
def adopt_pipe(text):
    return count_lines(upper(text))


@pytest.fixture(autouse=True)
def eager_lab(lab):
    """Point eager calls at the test's lab, with a fresh session each test."""
    for k in CALLS:
        CALLS[k] = 0
    dendrite.use(lab)
    yield lab
    runtime._sessions.clear()
    runtime._default_lab = None


@pytest.fixture
def text(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("one\n\ntwo\nthree\n")
    return p


def session_runs(lab):
    state = runtime.session(lab)
    return {n["node"]: n for n in lab.db.node_runs(state.experiment.id)}


def test_one_decorator_caches_a_plain_call(lab, text):
    assert count_lines(text) == 3
    assert count_lines(text) == 3
    assert CALLS["count"] == 1  # the second call was a cache hit
    runs = session_runs(lab)
    assert [runs["count_lines"]["status"], runs["count_lines[2]"]["status"]] == ["computed", "cached"]
    exp = runtime.session(lab).experiment.refresh()
    assert exp.pipeline_name == "session" and exp.status == "completed"
    assert set(exp.inputs) == {"count_lines.text", "count_lines[2].text"}  # outside files show as inputs


def test_returned_paths_chain_into_lineage_without_rehashing(lab, text, monkeypatch):
    out = upper(text)
    assert isinstance(out, Path) and out.read_text() == "ONE\n\nTWO\nTHREE\n"
    ingested = []
    real = lab.ingest
    monkeypatch.setattr(lab, "ingest", lambda p: ingested.append(p) or real(p))
    assert count_lines(out) == 3
    assert ingested == []  # the checkout was recognised, not hashed again
    runs = session_runs(lab)
    assert runs["count_lines"]["inputs"]["text"] == runs["upper"]["outputs"]["output"]


def test_object_arguments_become_value_inputs(lab):
    assert total({1, 2, 3}) == 6
    assert total({3, 2, 1}) == 6
    runs = session_runs(lab)
    assert runs["total[2]"]["status"] == "cached"


def test_wrapping_a_function_you_cannot_edit():
    fact = dendrite.step(math.factorial)
    assert fact(20) == math.factorial(20)
    assert fact(20) == math.factorial(20)
    assert {n["status"] for n in session_runs(runtime.default_lab()).values()} == {"computed", "cached"}


def test_rerun_recomputes_and_warns_when_outputs_differ(lab):
    first = noisy()
    with pytest.warns(NondeterministicStep, match=r"noisy(\[\d+\])? is not deterministic"):
        second = noisy.rerun()
    assert first != second and CALLS["noisy"] == 2
    assert noisy() == second  # the rerun's result is the one reused now
    runs = session_runs(lab)
    log = "".join(c["text"] for c in lab.db.logs(runs["noisy[2]"]["execution_id"]))
    assert "not deterministic" in log


def test_rerun_of_a_deterministic_step_is_quiet(lab, text):
    count_lines(text)
    with warnings.catch_warnings():
        warnings.simplefilter("error", NondeterministicStep)
        assert count_lines.rerun(text) == 3
    assert CALLS["count"] == 2


def test_experiment_rerun_one_step(lab, text):
    exp = lab.create(adopt_pipe, inputs={"text": text}).run()
    CALLS["count"] = CALLS["lines"] = 0
    exp.run(rerun=["count_lines"])
    statuses = {n["node"]: n["status"] for n in exp.node_runs()}
    assert statuses == {"upper": "cached", "count_lines": "computed"}
    assert (CALLS["lines"], CALLS["count"]) == (0, 1)
    assert exp.status == "completed"


@pytest.mark.local_only("the CLI is pointed at a local database URL")
def test_cli_rerun(lab, text, capsys):
    from dendrite.cli import main

    exp = lab.create(adopt_pipe, inputs={"text": text}).run()
    home = ["--home", str(lab.home), "--db-url", lab.settings.db_url]
    with pytest.raises(SystemExit, match="--rerun"):
        main([*home, "run", exp.id, "--import", "tests.test_adoption"])
    CALLS["lines"] = 0
    main([*home, "run", exp.id, "--import", "tests.test_adoption", "--rerun-all"])
    assert CALLS["lines"] == 1
    assert "rerunning every step" in capsys.readouterr().out


def test_cache_off_recomputes_but_records(lab, text, monkeypatch):
    monkeypatch.setenv("DENDRITE_CACHE", "off")
    lab.create(adopt_pipe, inputs={"text": text}).run()
    lab.create(adopt_pipe, inputs={"text": text}).run()
    assert CALLS["lines"] == 2
    monkeypatch.setenv("DENDRITE_CACHE", "on")
    lab.create(adopt_pipe, inputs={"text": text}).run()
    assert CALLS["lines"] == 2  # what "off" computed is reusable afterwards


def test_cache_readonly_reuses_but_never_adds(lab, text, monkeypatch):
    lab.create(adopt_pipe, inputs={"text": text}).run()  # upper and count_lines cached
    other = text.with_name("other.txt")
    other.write_text("a\nb\n")
    monkeypatch.setenv("DENDRITE_CACHE", "readonly")
    CALLS["lines"] = 0
    lab.create(adopt_pipe, inputs={"text": text}).run()
    assert CALLS["lines"] == 0  # reused
    lab.create(adopt_pipe, inputs={"text": other}).run()
    lab.create(adopt_pipe, inputs={"text": other}).run()
    assert CALLS["lines"] == 2  # new work is never added to the cache


def test_cache_bypass_runs_the_plain_function(lab, text, monkeypatch):
    monkeypatch.setenv("DENDRITE_CACHE", "bypass")
    before = len(lab.db.experiments())
    assert count_lines(text) == 3 and count_lines(text) == 3
    assert CALLS["count"] == 2
    assert len(lab.db.experiments()) == before  # nothing recorded, not even a session


def test_an_unknown_cache_mode_is_an_error(monkeypatch):
    monkeypatch.setenv("DENDRITE_CACHE", "sometimes")
    with pytest.raises(ValueError, match="DENDRITE_CACHE"):
        runtime.cache_mode()
