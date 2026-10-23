import importlib
import os
import sys
import textwrap

import pytest
import sqlalchemy as sa

from dendrite import NotADraft
from dendrite.db import meta

from . import toy


@pytest.fixture(autouse=True)
def reset():
    toy.CALLS.clear()


def outcomes(plan):
    return {n["name"]: (n["plan"], n["reason"]) for n in plan["nodes"] if n["kind"] == "node"}


def row_counts(lab):
    with lab.db.ro() as conn:
        return {t.name: conn.execute(sa.select(sa.func.count()).select_from(t)).scalar_one() for t in meta.sorted_tables}


def blobs(lab):
    return sorted(p for p in lab.store.root.rglob("*") if p.is_file())


# -- drafts ----------------------------------------------------------------------------
def test_fork_into_draft_and_edit(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    d = a.fork(draft=True, name="try")
    assert (d.status, d.parent_id, d.params, d.node_runs()) == ("draft", a.id, a.params, [])
    d.update({"train.iterations": 20}, name="try-20")
    assert d.params == {"train": {"iterations": 20}} and d.name == "try-20"
    assert a.refresh().params == {}


def test_reset_returns_to_parent_value(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}, params={"train.iterations": 5}).run()
    d = a.fork({"train.seed": 3, "train.iterations": 50}, draft=True)
    d.update(reset=["train.iterations", "train.seed"])
    assert d.params == {"train": {"iterations": 5}}


def test_frozen_experiments_reject_edits(lab, source):
    a = lab.create(toy.toy, inputs={"src": source})
    with pytest.raises(NotADraft):
        a.update({"train.iterations": 2})
    a.run()
    with pytest.raises(NotADraft):
        a.update({"train.iterations": 2})
    with pytest.raises(NotADraft):
        a.discard()


def test_running_freezes_a_draft(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    d = a.fork({"train.iterations": 20}, draft=True)
    stale = lab.get(d.id)
    d.run()
    assert d.status == "completed"
    with pytest.raises(NotADraft):
        stale.update({"train.iterations": 30})  # an edit that lost the race
    assert lab.get(d.id).params == {"train": {"iterations": 20}}


def test_discard_rules(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    d = a.fork(draft=True)
    child = d.fork(draft=True)
    with pytest.raises(NotADraft):
        d.discard()  # has a fork
    child.discard()
    d.discard()
    assert [e.id for e in lab.experiments()] == [a.id]


# -- plans -----------------------------------------------------------------------------
def test_unchanged_fork_plans_all_reuse(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    p = a.fork(draft=True).plan()
    assert p["complete"] and outcomes(p) == {n: ("reuse", None) for n in ("extract", "featurize", "train")}
    train = next(n for n in p["nodes"] if n["name"] == "train")
    assert train["reuse_from"]["experiment_id"] == a.id and train["estimate"] is not None
    assert {(e["from"], e["to"]) for e in p["edges"]} == {("input:src", "extract"), ("extract", "featurize"), ("featurize", "train")}


def test_param_change_plans_run_and_upstream(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    p = a.fork({"featurize.scale": 2}, draft=True).plan()
    assert outcomes(p) == {"extract": ("reuse", None), "featurize": ("run", "params"), "train": ("run", "upstream")}
    assert p["totals"]["run"] == 2 and p["totals"]["estimate"] > 0
    train = next(n for n in p["nodes"] if n["name"] == "train")
    assert train["inputs"]["features"]["kind"] == "pending"


def test_pinned_step_is_frozen(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    p = a.fork({"featurize.scale": 2}, pin={"featurize": a}, draft=True).plan()
    assert outcomes(p) == {"extract": ("reuse", None), "featurize": ("frozen", None), "train": ("reuse", None)}
    assert ("extract", "featurize") in {(e["from"], e["to"]) for e in p["edges"]}  # frozen keeps its inputs


def test_first_ever_run_and_uncached(lab, source):
    p = lab.create(toy.toy_sample, inputs={"src": source}, draft=True).plan()
    assert outcomes(p) == {"extract": ("run", "new"), "featurize": ("run", "upstream"), "sample": ("run", "upstream")}
    lab.get(p["experiment"]["id"]).run()
    p = lab.get(p["experiment"]["id"]).fork(draft=True).plan()
    assert outcomes(p)["sample"] == ("run", "uncached")


def test_missing_blob(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    lab.store.delete(a.output("featurize").hash)
    p = a.fork(draft=True).plan()
    assert outcomes(p)["featurize"] == ("run", "missing_blob")


def test_plan_writes_nothing(lab, source):
    a = lab.create(toy.toy_parallel, inputs={"src": source}).run()
    d = a.fork({"train.iterations": 3}, draft=True)
    toy.CALLS.clear()
    before, files = row_counts(lab), blobs(lab)
    p = d.plan()
    assert outcomes(p)["train"] == ("run", "params")
    assert row_counts(lab) == before and blobs(lab) == files
    assert not toy.CALLS and lab.get(d.id).flow_run_id is None


def test_branch_on_contents_is_incomplete(lab, source):
    a = lab.create(toy.toy_branch, inputs={"src": source}).run()
    assert outcomes(a.fork(draft=True).plan()) == {"extract": ("reuse", None), "featurize": ("reuse", None), "train": ("reuse", None)}
    p = a.fork({"featurize.scale": 2}, draft=True).plan()
    assert (p["complete"], p["blocked_by"]) == (False, "featurize")
    assert set(outcomes(p)) == {"extract", "featurize"}


def test_param_schema_and_exact_round_trip(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    d = a.fork({"train.iterations": 20}, draft=True)
    train = next(n for n in d.plan()["nodes"] if n["name"] == "train")
    schema = {s["name"]: s for s in train["param_schema"]}
    assert set(schema) == {"iterations", "seed"}  # `features` is an artifact input
    assert (schema["iterations"]["value"], schema["iterations"]["inherited"], schema["iterations"]["changed"]) == (20, 10, True)
    assert (schema["seed"]["json_type"], schema["seed"]["changed"]) == ("integer", False)
    d.run()
    again = d.fork(draft=True)
    for n in again.plan()["nodes"]:
        for s in n.get("param_schema", []):
            again.update({f"{n['name']}.{s['name']}": s["value"]})
    assert {o for o, _ in outcomes(again.plan()).values()} == {"reuse"}
    again.update({"train.iterations": 20.0})
    assert outcomes(again.plan())["train"] == ("run", "params")


def test_plan_reloads_edited_step_module(lab, source, tmp_path, monkeypatch):
    mod = tmp_path / "edited_steps.py"
    body = '''
        from dendrite import artifact_task, pipeline

        @artifact_task
        def double(src):
            return src.read_text() * {n}

        @pipeline(name="edited")
        def edited(src):
            return double(src)
    '''
    mod.write_text(textwrap.dedent(body.format(n=2)))
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.import_module("edited_steps")
    try:
        a = lab.create("edited", inputs={"src": source}).run()
        assert outcomes(a.fork(draft=True).plan()) == {"double": ("reuse", None)}
        mod.write_text(textwrap.dedent(body.format(n=3)))
        st = mod.stat()
        os.utime(mod, (st.st_atime, st.st_mtime + 5))
        assert outcomes(a.fork(draft=True).plan()) == {"double": ("run", "code")}
    finally:
        sys.modules.pop("edited_steps", None)


# -- reasons, estimates and edge cases ---------------------------------------------------------
def test_input_change_is_the_reason(lab, source, tmp_path):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    other = tmp_path / "other.csv"
    other.write_text("a,b\n9,9\n")
    p = a.fork(inputs={"src": other}, draft=True).plan()
    assert outcomes(p)["extract"] == ("run", "inputs")


def test_env_change_is_the_reason(lab, source, monkeypatch):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    monkeypatch.setenv("DENDRITE_ENV", "image@sha256:new")
    p = a.fork(draft=True).plan()
    assert outcomes(p)["extract"] == ("run", "env")
    assert p["env"]["env"] == "image@sha256:new"


def test_estimate_falls_back_to_the_parents_run(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    parent_train = lab.db.execution(next(n for n in a.node_runs() if n["node"] == "train")["execution_id"])
    p = a.fork({"train.iterations": 99}, draft=True).plan()
    train = next(n for n in p["nodes"] if n["name"] == "train")
    assert train["estimate"] == pytest.approx(parent_train["finished_at"] - parent_train["started_at"])


def test_plan_resolves_futures_the_body_waits_on(lab, source):
    a = lab.create(toy.toy_result, inputs={"src": source}).run()
    assert outcomes(a.fork(draft=True).plan()) == {n: ("reuse", None) for n in ("extract", "featurize", "train")}
    p = a.fork({"featurize.scale": 3}, draft=True).plan()
    assert outcomes(p)["train"] == ("run", "upstream")


def test_plan_reports_bad_param_keys_instead_of_raising(lab, source):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    p = a.fork({"train.iteratons": 5}, draft=True).plan()
    assert p["complete"] is False and "iteratons" in p["error"]


def test_reset_without_a_parent_drops_the_override(lab, source):
    d = lab.create(toy.toy, inputs={"src": source}, params={"train.seed": 4, "train.iterations": 3}, draft=True)
    d.update(reset=["train.seed"])
    assert d.params == {"train": {"iterations": 3}}
    d.update(reset=["train.iterations"])
    assert d.params == {}


def test_starting_a_never_run_experiment_twice_fails(lab, source):
    a = lab.create(toy.toy, inputs={"src": source})
    stale = lab.get(a.id)
    a.run()
    with pytest.raises(NotADraft):
        stale.run()  # still believes it is "created"; the compare-and-set refuses


def test_reload_failure_keeps_the_previous_code(lab, source, tmp_path, monkeypatch):
    mod = tmp_path / "broken_steps.py"
    mod.write_text(textwrap.dedent('''
        from dendrite import artifact_task, pipeline

        @artifact_task
        def echo(src):
            return src.read_text()

        @pipeline(name="breaks")
        def breaks(src):
            return echo(src)
    '''))
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.import_module("broken_steps")
    try:
        a = lab.create("breaks", inputs={"src": source}).run()
        mod.write_text("def oops(:\n")
        st = mod.stat()
        os.utime(mod, (st.st_atime, st.st_mtime + 5))
        p = a.fork(draft=True).plan()
        assert "reloading broken_steps failed" in p["error"]
        assert outcomes(p) == {"echo": ("reuse", None)}  # planned with the code that was loaded
    finally:
        sys.modules.pop("broken_steps", None)


def test_param_editor_types():
    from dendrite.task import _json_type

    assert _json_type(float, 1) == "number"  # annotation wins over the default's type
    assert _json_type("bool", None) == "boolean"  # `from __future__ import annotations`
    assert _json_type(inspect_empty(), True) == "boolean"  # bool is not an integer here
    assert _json_type(inspect_empty(), 3) == "integer"
    assert _json_type(inspect_empty(), [1]) == "array"
    assert _json_type(inspect_empty(), None) == "any"
    assert _json_type(list[int], None) == "any"  # generics fall back to raw JSON


def inspect_empty():
    import inspect

    return inspect.Parameter.empty
