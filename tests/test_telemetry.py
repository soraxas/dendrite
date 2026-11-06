"""Run telemetry: console capture, live metrics, samples and environment (display only)."""

import logging
import sys
import threading
import time

import pytest

from dendrite import artifact_task, log_image, log_media, log_metric, pipeline, run, telemetry, workdir

GATE = threading.Event()
CALLS = {"chatty": 0}


@artifact_task(outputs=["out"])
def chatty(n: int = 3) -> int:
    CALLS["chatty"] += 1
    print("hello from chatty")
    print("to stderr", file=sys.stderr)
    logging.getLogger("tele.test").warning("a warning")
    for i in range(n):
        log_metric("loss/train", 1.0 / (i + 1), step=i)
        log_metric("loss/val", 2.0 / (i + 1), step=i)
    return n


@pipeline(name="tele_chatty")
def chatty_pipe():
    chatty()


@artifact_task(outputs=["out"], cache=False, ignore=["no-return"])
def failing() -> int:
    print("about to fail")
    log_metric("progress", 0.5)
    raise ValueError("nope")


@pipeline(name="tele_failing")
def failing_pipe():
    failing()


@artifact_task(outputs=["out"], cache=False)
def speaker(word: str = "a", lines: int = 50) -> str:
    for i in range(lines):
        print(f"{word}-{i}")
        time.sleep(0.001)
    return word


@pipeline(name="tele_concurrent")
def concurrent_pipe():
    a = speaker.named("left").submit(word="left")
    b = speaker.named("right").submit(word="right")
    a.result()
    b.result()


@artifact_task(outputs=["out"], cache=False)
def waiter() -> int:
    print("started", flush=True)
    GATE.wait(10)
    return 1


@pipeline(name="tele_live")
def live_pipe():
    waiter()


@artifact_task(outputs=["out"], cache=False)
def painter(steps: int = 3) -> int:
    for i in range(steps):
        img = workdir() / f"frame{i}.png"
        img.write_bytes(_tiny_png(i))
        log_image("render", img, step=i)
    clip = workdir() / "clip.mp4"
    clip.write_bytes(b"not really a video")
    log_media("clip", clip)
    return steps


@pipeline(name="tele_painter")
def painter_pipe(steps: int = 3):
    painter(steps=steps)


@artifact_task(outputs=["out"], cache=False)
def shell() -> str:
    return run([sys.executable, "-c", "print('from the child'); import sys; print('err', file=sys.stderr)"]).stdout


@pipeline(name="tele_shell")
def shell_pipe():
    shell()


def _tiny_png(shade: int) -> bytes:
    class Arr:  # stands in for a 1x1 RGB uint8 array (numpy isn't a dependency)
        shape = (1, 1, 3)
        ndim = 3

        def __getitem__(self, y):
            return self

        def tobytes(self):
            return bytes([shade, shade, shade])

    return telemetry._png(Arr())


def _log_text(lab, exec_id):
    return "".join(c["text"] for c in lab.db.logs(exec_id))


def _exec(exp, node):
    return {n["node"]: n["execution_id"] for n in exp.node_runs()}[node]


def test_console_is_captured_and_still_printed(lab, capsys):
    exp = lab.create(chatty_pipe).run()
    exec_id = _exec(exp, "chatty")
    chunks = lab.db.logs(exec_id)
    by_stream = {}
    for c in chunks:
        by_stream.setdefault(c["stream"], "")
        by_stream[c["stream"]] += c["text"]
    assert "hello from chatty" in by_stream["stdout"]
    assert "to stderr" in by_stream["stderr"]
    assert "WARNING tele.test: a warning" in by_stream["log"]
    out = capsys.readouterr()
    assert "hello from chatty" in out.out  # still teed to the terminal
    assert "to stderr" in out.err


def test_scalars_are_kept_with_title_and_series(lab):
    exp = lab.create(chatty_pipe, params={"chatty": {"n": 4}}).run()
    points = lab.db.scalars([_exec(exp, "chatty")])[_exec(exp, "chatty")]
    keys = {p["key"] for p in points}
    assert keys == {"loss/train", "loss/val"}
    assert [p["step"] for p in points if p["key"] == "loss/train"] == [0, 1, 2, 3]
    assert telemetry.split_key("loss/train") == ("loss", "train")
    assert telemetry.split_key("score") == ("score", "score")


def test_failure_keeps_log_traceback_and_metrics(lab):
    exp = lab.create(failing_pipe)
    with pytest.raises(ValueError, match="nope"):
        exp.run()
    ex = lab.db.execution(_exec(exp, "failing"))
    assert ex["status"] == "failed"
    text = _log_text(lab, ex["id"])
    assert "about to fail" in text
    assert "ValueError: nope" in text
    assert {m["key"] for m in ex["metrics"]} == {"progress"}


def test_concurrent_steps_keep_separate_logs(lab):
    exp = lab.create(concurrent_pipe).run()
    left = _log_text(lab, _exec(exp, "speaker[left]"))
    right = _log_text(lab, _exec(exp, "speaker[right]"))
    assert "left-49" in left and "right-" not in left
    assert "right-49" in right and "left-" not in right


@pytest.mark.local_only("polls a run in another thread of this process")
def test_log_is_visible_while_running(lab, monkeypatch):
    monkeypatch.setattr(telemetry, "LOG_INTERVAL", 0.0)
    GATE.clear()
    exp = lab.create(live_pipe)
    t = threading.Thread(target=exp.run)
    t.start()
    try:
        deadline = time.time() + 20
        seen = ""
        while time.time() < deadline and "started" not in seen:
            runs = {n["node"]: n for n in lab.db.node_runs(exp.id)}
            eid = runs.get("waiter", {}).get("execution_id")
            if eid:
                seen = _log_text(lab, eid)
            time.sleep(0.05)
        assert "started" in seen
        assert lab.db.execution(eid)["status"] == "running"
    finally:
        GATE.set()
        t.join(30)


def test_truncation_keeps_head_and_tail():
    class Sink:
        def __init__(self):
            self.chunks = []
            self.tail = None

        def write_telemetry(self, exec_id, logs=None, tail=None, metric_values=None):
            self.chunks += logs or []
            if tail is not None:
                self.tail = tail

    sink = Sink()
    t = telemetry.Telemetry(sink, "x", max_bytes=1000)
    for i in range(500):
        t.write("stdout", f"line {i:04d}\n")  # 10 bytes each: 5000 in all
    t.flush(final=True)
    text = "".join(c["text"] for c in sink.chunks)
    assert text.startswith("line 0000\n")
    assert text.endswith("line 0499\n")
    assert sum(c["stream"] == "marker" for c in sink.chunks) == 1
    assert len(text.encode()) <= 1000 + 100  # plus the marker line
    assert sink.tail == ""  # the live tail row is dropped once the log is final


def test_samples_are_stored_capped_and_never_outputs(lab, monkeypatch):
    monkeypatch.setenv("DENDRITE_SAMPLE_HISTORY", "2")
    exp = lab.create(painter_pipe, params={"painter": {"steps": 3}}).run()
    exec_id = _exec(exp, "painter")
    samples = lab.db.samples(exec_id)
    renders = [s for s in samples if s["title"] == "render"]
    assert [s["step"] for s in renders] == [1, 2]  # history 2 keeps the latest steps
    assert all(s["filename"] and s["filename"].endswith(".png") for s in renders)
    assert [s["title"] for s in samples if s["title"] != "render"] == ["clip"]
    assert lab.store.verify(lab.artifact(renders[0]["hash"]))
    assert set(lab.db.execution(exec_id)["outputs"]) == {"out"}


def test_subprocess_output_streams_into_the_log(lab):
    exp = lab.create(shell_pipe).run()
    text = _log_text(lab, _exec(exp, "shell"))
    assert "from the child" in text and "err" in text
    assert "from the child" in exp.load("shell")


def test_run_raises_on_failure(capsys):
    import subprocess

    with pytest.raises(subprocess.CalledProcessError):
        run([sys.executable, "-c", "print('x'); raise SystemExit(3)"])
    assert run([sys.executable, "-c", "raise SystemExit(3)"], check=False).returncode == 3


def test_environment_is_recorded(lab):
    exp = lab.create(chatty_pipe).run()
    env = lab.db.environment(_exec(exp, "chatty"))
    assert env["python"]["version"] == __import__("platform").python_version()
    assert any(p.lower().startswith("prefect==") for p in env["packages"])
    assert "environ" not in env


def test_environment_strips_credentials_and_handles_no_git(tmp_path, monkeypatch):
    telemetry._git_cache.clear()
    assert telemetry.snapshot(tmp_path)["git"] is None
    assert telemetry._CRED.sub("", "https://user:token@github.com/org/repo.git") == "https://github.com/org/repo.git"


def test_cache_hit_reuses_original_telemetry(lab):
    a = lab.create(chatty_pipe).run()
    CALLS["chatty"] = 0
    b = a.fork().run()
    assert CALLS["chatty"] == 0  # telemetry never changes a signature
    assert _exec(a, "chatty") == _exec(b, "chatty")
    assert "hello from chatty" in _log_text(lab, _exec(b, "chatty"))


def test_plan_writes_no_telemetry(lab):
    exp = lab.create(chatty_pipe, draft=True)
    exp.plan()
    assert lab.db.sample_hashes() == []


@pytest.mark.local_only("reads the storage report of a local store")
def test_samples_survive_gc_until_their_experiment_is_deleted(lab, monkeypatch):
    exp = lab.create(painter_pipe).run()
    h = lab.db.samples(_exec(exp, "painter"))[0]["hash"]
    lab.gc(dry_run=False, grace=0)
    assert lab.store.verify(lab.artifact(h))
    exp.delete()
    lab.gc(dry_run=False, grace=0)
    assert not lab.store.verify(lab.artifact(h))


def test_metrics_survive_a_failed_live_flush(lab, monkeypatch):
    def broken(*args, **kwargs):
        raise ConnectionError("telemetry backend down")

    monkeypatch.setattr(lab.db, "write_telemetry", broken)
    exp = lab.create(chatty_pipe, params={"chatty": {"n": 5}}).run()
    points = lab.db.scalars([_exec(exp, "chatty")])[_exec(exp, "chatty")]
    assert len([p for p in points if p["key"] == "loss/train"]) == 5  # publish writes them all


def test_telemetry_endpoints(lab, tmp_path):
    from fastapi.testclient import TestClient

    from dendrite.server import create_app

    from .test_api import no_bundle

    exp = lab.create(painter_pipe).run()
    c = TestClient(create_app(lab, ui_dir=no_bundle(tmp_path)))
    eid = _exec(exp, "painter")
    logs = c.get(f"/api/executions/{eid}/logs").json()
    assert logs["running"] is False and logs["status"] == "success"
    assert {s["title"] for s in c.get(f"/api/executions/{eid}/samples").json()["samples"]} == {"clip", "render"}
    assert c.get(f"/api/executions/{eid}/environment").json()["environment"]["python"]
    chatty = lab.create(chatty_pipe).run()
    ceid = _exec(chatty, "chatty")
    assert {p["key"] for p in c.get(f"/api/executions/{ceid}/scalars").json()["points"]} == {"loss/train", "loss/val"}
    nodes = c.get(f"/api/experiments/{chatty.id}/scalars").json()["nodes"]
    assert [n["node"] for n in nodes] == ["chatty"]
    first = c.get(f"/api/executions/{ceid}/logs?after=-1").json()["chunks"]
    assert c.get(f"/api/executions/{ceid}/logs?after={first[-1]['seq']}").json()["chunks"] == []
    assert c.get("/api/executions/nope/logs").status_code == 404


@pytest.mark.local_only("the CLI is pointed at a local database URL")
def test_cli_logs_and_env(lab, capsys):
    from dendrite.cli import main

    exp = lab.create(chatty_pipe).run()
    home = ["--home", str(lab.home), "--db-url", lab.settings.db_url]
    capsys.readouterr()
    main([*home, "logs", exp.id, "chatty"])
    assert "hello from chatty" in capsys.readouterr().out
    main([*home, "logs", exp.id, "chatty", "-f"])  # a finished run: prints and returns
    assert "hello from chatty" in capsys.readouterr().out
    main([*home, "env", exp.id, "chatty"])
    out = capsys.readouterr().out
    assert "python:" in out and "packages:" in out
    with pytest.raises(SystemExit):
        main([*home, "logs", exp.id, "missing"])
