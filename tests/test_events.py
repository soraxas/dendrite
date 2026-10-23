import json
import threading

from fastapi.testclient import TestClient

from dendrite.server import create_app

from . import toy
from .test_api import no_bundle


def events(response):
    for line in response.iter_lines():
        if line.startswith("data: "):
            yield json.loads(line[6:])


def test_events_push_tree_and_experiment_changes(lab, source, tmp_path):
    a = lab.create(toy.toy, inputs={"src": source}).run()
    client = TestClient(create_app(lab, ui_dir=no_bundle(tmp_path)))
    with client.stream("GET", "/api/events?limit=1") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        stream = events(r)
        assert next(stream) == {"hello": True}
        b = a.fork({"train.iterations": 3})  # a new experiment, from "another process"
        assert next(stream) == {"tree": True, "experiments": [b.id]}


def test_events_report_live_progress_of_a_running_step(lab, source, tmp_path):
    toy.SLEEP["train"] = 0.0
    client = TestClient(create_app(lab, ui_dir=no_bundle(tmp_path)))
    config = lab.ingest(lab.store.put_value({"scale": 1}))
    exp = lab.create(toy.toy_decides, inputs={"src": source, "config": config})
    with client.stream("GET", "/api/events?limit=2") as r:
        stream = events(r)
        next(stream)
        runner = threading.Thread(target=exp.run)
        runner.start()
        seen = [next(stream), next(stream)]
        runner.join()
    assert all(exp.id in e["experiments"] for e in seen)
