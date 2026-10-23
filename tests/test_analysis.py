import importlib
import sys
import textwrap
import uuid
import warnings
from typing import NamedTuple, TypedDict

import pytest

from dendrite import artifact_task
from dendrite.analysis import StepDefinitionWarning, pipeline_shape

from . import toy


@pytest.fixture
def load(tmp_path, monkeypatch):
    """Import a module written from source text (inspect needs a real file)."""
    monkeypatch.syspath_prepend(str(tmp_path))
    loaded = []

    def _load(body: str):
        name = f"mod_{uuid.uuid4().hex[:8]}"
        (tmp_path / f"{name}.py").write_text("from pathlib import Path\nfrom dendrite import artifact_task, pipeline\n" + textwrap.dedent(body))
        loaded.append(name)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            mod = importlib.import_module(name)
        mod.caught = [w for w in caught if issubclass(w.category, StepDefinitionWarning)]
        return mod

    yield _load
    from dendrite import runtime
    from dendrite.experiment import _PIPELINES
    from dendrite.task import _STEPS

    for n in loaded:  # forget everything these modules registered, so tests stay independent
        sys.modules.pop(n, None)
        runtime._MODULES.pop(n, None)
        runtime._PIPELINE_MODULES.discard(n)
        for key in [k for k in _STEPS if k[0] == n]:
            del _STEPS[key]
        for key in [k for k, p in _PIPELINES.items() if p.fn.__module__ == n]:
            del _PIPELINES[key]


def codes(mod, step=None):
    out = []
    for w in mod.caught:
        msg = str(w.message)
        if step is None or msg.startswith(f"{step}:"):
            out.append(msg.rsplit("[", 1)[1].rstrip("]"))
    return out


# -- step checks ----------------------------------------------------------------------------
def test_return_shape_mismatches(load):
    mod = load('''
        @artifact_task(outputs=["cameras", "sparse"])
        def short(frames):
            return frames

        @artifact_task(outputs=["cameras", "sparse"])
        def three(frames):
            return frames, frames, frames

        @artifact_task(outputs=["cameras", "sparse"])
        def keys(frames):
            return {"cameras": 1, "spares": 2}

        @artifact_task(outputs=["cameras", "sparse"])
        def fine(frames):
            if frames:
                return {"cameras": 1, "sparse": 2}
            out = (frames, frames)
            return out
    ''')
    assert codes(mod, "three") == ["return-shape"]
    assert codes(mod, "keys") == ["return-shape"]
    assert "missing sparse; unexpected spares" in str(next(w.message for w in mod.caught if str(w.message).startswith("keys:")))
    assert codes(mod, "short") == []  # a name may hold a tuple; only literals are judged
    assert codes(mod, "fine") == []


def test_warning_points_at_the_offending_line(load):
    mod = load('''
        @artifact_task(outputs=["a", "b"])
        def bad(x):
            y = x
            return 1, 2, 3
    ''')
    (w,) = mod.caught
    assert w.filename.endswith(".py") and w.lineno == 7  # 2 header lines + the module's 5th line


def test_no_return(load):
    mod = load('''
        @artifact_task
        def forgot(x):
            x.read_text()

        @artifact_task
        def abstract(x):
            """Not implemented yet."""
            raise NotImplementedError
    ''')
    assert codes(mod, "forgot") == ["no-return"]
    assert codes(mod, "abstract") == []


def test_names_and_defaults(load):
    mod = load('''
        @artifact_task(outputs=["point cloud"])
        def bad_name(x, opts=object()):
            return x
    ''')
    assert sorted(codes(mod, "bad_name")) == ["bad-output-name", "default-not-json"]


def test_hidden_inputs(load):
    mod = load('''
        import os, random, time

        @artifact_task
        def leaky(x):
            cfg = open("config.yaml").read()
            more = Path("extra.json").read_text()
            open("out.txt", "w").write("writes are fine")
            key = os.environ["API_KEY"] + os.getenv("HOME", "")
            stamp = time.time()
            return random.random()

        @artifact_task
        def seeded(x, seed: int = 0):
            rng = random.Random(seed)
            return random.random() + rng.random()

        @artifact_task(ignore=("hidden-env",))
        def allowed(x):
            return os.environ.get("X")
    ''')
    # reported once per code and line, so the two env reads on one line give one warning
    assert sorted(codes(mod, "leaky")) == ["hidden-clock", "hidden-env", "hidden-read", "hidden-read", "unseeded-random"]
    assert codes(mod, "seeded") == []
    assert codes(mod, "allowed") == []


def test_existing_steps_are_clean():
    from dendrite.task import _STEPS

    assert [d for (m, _), t in _STEPS.items() if m == toy.__name__ for d in t.diagnostics] == []


def test_source_unavailable_is_skipped():
    namespace: dict = {}
    exec("def made_up(x):\n    return x, x\n", namespace)
    with warnings.catch_warnings():
        warnings.simplefilter("error", StepDefinitionWarning)
        step = artifact_task(outputs=["a", "b", "c"])(namespace["made_up"])
    assert step.diagnostics == []


# -- pipeline shape -------------------------------------------------------------------------
def shape_of(mod, name):
    s = getattr(mod, name).shape()
    s.pop("_steps")
    return s


def edges(s):
    return {(e["from"], e["to"], e["label"]) for e in s["edges"]}


def diag_codes(s):
    return [d["code"] for d in s["diagnostics"]]


STEPS = '''
    @artifact_task(outputs=["frames"])
    def extract(video, fps: int = 2):
        return video

    @artifact_task(outputs=["cameras", "sparse"])
    def colmap(frames, matcher: str = "seq"):
        return frames, frames

    @artifact_task(outputs=["model"])
    def train(frames, cameras, iterations: int = 10):
        return frames

    @artifact_task(outputs=["model"])
    def refine(model):
        return model
'''


def test_shape_matches_the_traced_dag(lab, source):
    s = toy.toy.shape()
    s.pop("_steps")
    exp = lab.create(toy.toy, inputs={"src": source}).run()
    dag = exp.dag()
    assert {n["id"] for n in s["nodes"]} == {n["id"] for n in dag["nodes"]}
    assert edges(s) == {(e["from"], e["to"], e["label"]) for e in dag["edges"]}
    assert s["diagnostics"] == []


def test_conditionals_loops_and_dynamic_calls(load):
    mod = load(STEPS + '''
    import json

    def wrap(frames):
        return train(frames, colmap(frames)[0])

    @pipeline(name="shapes_" + __name__)
    def flow(video, refine_it=False):
        frames = extract(video)
        model = wrap(frames)
        if refine_it:
            model = refine(model)
        for i in range(3):
            extract.with_options(name=f"x{i}")(video)
        print(len(frames))
        return json.dumps(model)
    ''')
    s = shape_of(mod, "flow")
    nodes = {n["id"]: n for n in s["nodes"]}
    assert nodes["refine"]["conditional"] == ["refine_it"]
    assert nodes["train"]["line"] == 4  # a step inside a helper is placed at the helper's call
    assert ("colmap", "train", "cameras") in edges(s)  # expanded one level through `wrap`
    dyn = [n for n in s["nodes"] if n["kind"] == "dynamic"]
    assert [d["name"] for d in dyn] == ["json.dumps(model)"]  # not a step, not a local helper
    assert {("train", dyn[0]["id"], "model"), ("refine", dyn[0]["id"], "model")} <= edges(s)  # either branch's model
    assert s["dynamic"] is True
    assert diag_codes(s) == ["call-in-loop"]  # f-string names aren't literal, so the loop can't be checked


def test_body_diagnostics(load):
    mod = load(STEPS + '''
    @pipeline(name="bad_" + __name__)
    def flow(video):
        frames = extract(video, fsp=3)
        a, b, c = colmap(frames)
        cams = colmap(frames)
        m = train(frames)
        x, y = extract(video)
        return train(frames, nowhere)
    ''')
    s = shape_of(mod, "flow")
    found = [(d["code"], d["line"]) for d in s["diagnostics"]]
    assert ("unknown-arg", 3) in found and "did you mean 'fps'" in s["diagnostics"][0]["message"]
    assert ("unpack-mismatch", 4) in found
    assert ("duplicate-call", 5) in found
    assert ("missing-arg", 6) in found
    assert ("unpack-mismatch", 7) in found
    assert ("undefined-name", 8) in found


def test_exclusive_branches_are_not_duplicates(load):
    mod = load(STEPS + '''
    @pipeline(name="branches_" + __name__)
    def flow(video, fast=True):
        frames = extract(video)
        cams, sparse = colmap(frames)
        if fast:
            model = train(frames, cams, iterations=1)
        else:
            model = train(frames, cams)
        return model
    ''')
    s = shape_of(mod, "flow")
    assert diag_codes(s) == []
    (train,) = [n for n in s["nodes"] if n["id"] == "train"]
    assert train["alternatives"] == 2 and train["conditional"] == []  # runs on either branch


def test_unsaved_text_and_syntax_errors(load):
    mod = load(STEPS + '''
    @pipeline(name="edit_" + __name__)
    def flow(video):
        return extract(video)
    ''')
    edited = "def flow(video):\n    f = extract(video)\n    c, s = colmap(f)\n    return train(f, c)\n"
    s = mod.flow.shape(edited)
    assert {n["id"] for n in s["nodes"]} == {"input:video", "extract", "colmap", "train"}
    broken = mod.flow.shape("def flow(video):\n    f = extract(video\n")
    assert broken["nodes"] == [] and broken["diagnostics"][0]["code"] == "syntax"
    assert broken["diagnostics"][0]["line"] == 2


def test_pipeline_shape_resolves_only_steps():
    s = pipeline_shape("def p(x):\n    return helper(x)\n", lambda n: None)
    assert [n["kind"] for n in s["nodes"]] == ["input", "dynamic"]


# -- experiment checks ----------------------------------------------------------------------
def test_experiment_check_finds_typos(lab, source):
    exp = lab.create(toy.toy, inputs={"src": source}, params={"trian": {"iterations": 5}, "train": {"iteratons": 1}}, draft=True)
    found = {(d["code"], d["step"]) for d in exp.check()}
    assert found == {("unknown-step", "trian"), ("unknown-param", "train")}
    assert "did you mean 'train'" in next(d["message"] for d in exp.check() if d["code"] == "unknown-step")


def test_clean_experiment_has_no_findings(lab, source):
    assert lab.create(toy.toy, inputs={"src": source}, params={"train.iterations": 3}).check() == []


# -- typed declarations ---------------------------------------------------------------------
class Colmap(NamedTuple):
    cameras: str
    sparse: str


class Report(TypedDict):
    psnr: float
    frames: int


@artifact_task
def typed_colmap(x: str = "a") -> Colmap:
    return Colmap(x + "-cams", x + "-sparse")


@artifact_task
def typed_report(x: str = "a") -> Report:
    return {"psnr": 1.0, "frames": 2}


def test_outputs_from_return_annotations():
    assert typed_colmap.outputs == ("cameras", "sparse")
    assert typed_report.outputs == ("psnr", "frames")
    assert typed_colmap._normalise(Colmap("c", "s")) == {"cameras": "c", "sparse": "s"}


def test_unresolvable_annotation_falls_back(load):
    mod = load('''
        @artifact_task
        def later(x) -> "NotDefinedYet":
            return x
    ''')
    assert mod.later.outputs == ("output",)
    assert codes(mod, "later") == ["unresolved-return-type"]


def test_override_checks_names():
    assert toy.train.override(iterations=5, seed=1) == {"train.iterations": 5, "train.seed": 1}
    with pytest.raises(TypeError, match="did you mean 'iterations'"):
        toy.train.override(iteratons=5)


# -- API and CLI ----------------------------------------------------------------------------
def test_shape_endpoints(lab, tmp_path, load):
    from fastapi.testclient import TestClient

    from dendrite.server import create_app

    from .test_api import legacy_only

    mod = load(STEPS + '''
    @pipeline(name="api_" + __name__)
    def flow(video):
        frames = extract(video)
        return colmap(frames)
    ''')
    name = mod.flow.name
    file = mod.__file__
    before = open(file).read()
    c = TestClient(create_app(lab, ui_dir=legacy_only(tmp_path)))

    s = c.get(f"/api/pipelines/{name}/shape").json()
    assert [n["id"] for n in s["nodes"]] == ["input:video", "extract", "colmap"]
    snap = c.get(f"/api/sources/{s['source_hash']}").json()  # viewable (and editable) like a step's source
    assert snap["status"] == "same" and "def flow" in snap["source"]

    edited = snap["source"].replace("return colmap(frames)", "a, b, c = colmap(frames)\n    return a")
    r = c.post(f"/api/pipelines/{name}/shape", json={"source": edited}).json()
    assert [d["code"] for d in r["diagnostics"]] == ["unpack-mismatch"]
    assert open(file).read() == before  # analysing text never writes it

    assert c.get("/api/pipelines/nope/shape").status_code == 404


def test_experiment_check_endpoint(lab, source, tmp_path):
    from fastapi.testclient import TestClient

    from dendrite.server import create_app

    from .test_api import legacy_only

    d = lab.create(toy.toy, inputs={"src": source}, params={"trian.iterations": 1}, draft=True)
    c = TestClient(create_app(lab, ui_dir=legacy_only(tmp_path)))
    assert [f["code"] for f in c.get(f"/api/experiments/{d.id}/check").json()] == ["unknown-step"]


def test_cli_check_exit_status(lab, source, load, capsys):
    from dendrite.cli import cmd_check

    class Args:
        imports: list = []
        ignore: list = []

    lab.create(toy.toy, inputs={"src": source}, params={"train.iteratons": 1}, draft=True)
    with pytest.raises(SystemExit) as stopped:
        cmd_check(lab, Args)
    out = capsys.readouterr().out
    assert stopped.value.code == 1 and "unknown-param" in out and "did you mean 'iterations'" in out

    Args.ignore = ["unknown-param"]
    load('''
        import os

        @artifact_task
        def env_reader(x):
            return os.environ.get("X")
    ''')
    cmd_check(lab, Args)  # only a warning left: exits normally
    out = capsys.readouterr().out
    assert "warning hidden-env" in out and "0 error(s)" in out
