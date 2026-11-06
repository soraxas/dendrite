"""The hosted server end to end: a real server in a thread, Postgres, moto S3."""

import hashlib
import re

import httpx
import pytest

from dendrite import Lab
from dendrite.remote import NotLoggedIn, ServerStore

from . import toy


def statuses(exp):
    return {n["node"]: n["status"] for n in exp.node_runs()}


def api(hosted, token, method, path, headers=None, **kw):
    return httpx.request(
        method, hosted.url + path, headers={"Authorization": f"Bearer {token}", **(headers or {})}, **kw
    )


def rpc(hosted, token, project, method, *args, **kwargs):
    from dendrite.hosted import wire

    body = wire.dumps({"args": list(args), "kwargs": kwargs})
    return api(hosted, token, "POST", f"/api/v1/projects/{project}/rpc/{method}", content=body)


# -- running through the server -------------------------------------------------------
def test_run_and_fork_through_the_server(hosted, tmp_path, source):
    lab = hosted.lab(tmp_path / "a")
    assert lab.hosted
    toy.CALLS.clear()
    a = lab.create(toy.toy, inputs={"src": source}).run()
    assert a.status == "completed"
    assert a.load("featurize") == [3, 7, 11, 15]
    b = a.fork({"train.iterations": 3}).run()
    assert {n for n, s in statuses(b).items() if s == "cached"} == {"extract", "featurize"}
    assert toy.CALLS["extract"] == 1  # computed once, reused by the fork
    lab.db.dispose()


def test_another_user_reuses_results_and_downloads_them(hosted, tmp_path, source):
    project = hosted.project()
    alice = hosted.lab(tmp_path / "alice", project, hosted.user("alice@t.io", project, "runner"))
    bob = hosted.lab(tmp_path / "bob", project, hosted.user("bob@t.io", project, "runner"))
    toy.CALLS.clear()
    a = alice.create(toy.toy, inputs={"src": source}).run()
    b = bob.create(toy.toy, inputs={"src": source}).run()
    assert set(statuses(b).values()) == {"cached"} and toy.CALLS["extract"] == 1
    # Bob's machine has never seen the rows; loading them downloads from the bucket.
    assert not (tmp_path / "bob" / "cache" / "checkouts").exists()
    files = lambda d: {p.relative_to(d).as_posix(): p.read_bytes() for p in d.rglob("*") if p.is_file()}  # noqa: E731
    assert files(bob.load(b.output("extract"))) == files(alice.load(a.output("extract")))
    assert bob.get(b.id).summary()["status"] == "completed"
    rows = {e["id"]: e for e in bob.db.experiments()}
    assert rows[a.id]["created_by"] == "alice" and rows[b.id]["created_by"] == "bob"
    for lab in (alice, bob):
        lab.db.dispose()


def test_projects_never_share_cache_or_blobs(hosted, tmp_path, source):
    one, two = hosted.project(), hosted.project()
    a = hosted.lab(tmp_path / "one", one)
    a.create(toy.toy, inputs={"src": source}).run()
    toy.CALLS.clear()
    b = hosted.lab(tmp_path / "two", two)
    exp = b.create(toy.toy, inputs={"src": source}).run()
    assert "cached" not in set(statuses(exp).values()) and toy.CALLS["extract"] == 1
    h = a.get(a.experiments()[0].id).output("extract").hash
    urls = api(
        hosted,
        hosted.user("carol@t.io", two, "viewer"),
        "POST",
        f"/api/v1/projects/{one}/blobs/urls",
        json={"hashes": [h]},
    )
    assert urls.status_code == 403
    for lab in (a, b):
        lab.db.dispose()


# -- permissions --------------------------------------------------------------------
def test_roles_gate_writes(hosted, tmp_path, source):
    project = hosted.project()
    viewer = hosted.lab(tmp_path / "v", project, hosted.user("viewer@t.io", project, "viewer"))
    with pytest.raises(PermissionError):
        viewer.create(toy.toy, inputs={"src": source})
    assert viewer.experiments() == []  # nothing was written

    owner = hosted.lab(tmp_path / "o", project, hosted.user("owner@t.io", project, "runner"))
    other = hosted.lab(tmp_path / "x", project, hosted.user("other@t.io", project, "runner"))
    exp = owner.create(toy.toy, inputs={"src": source}).run()
    with pytest.raises(PermissionError):
        other.get(exp.id).delete()
    with pytest.raises(PermissionError):
        other.get(exp.id).keep()  # keep flags are for admins
    assert owner.get(exp.id).delete() == [exp.id]
    for lab in (viewer, owner, other):
        lab.db.dispose()


def test_only_the_executions_owner_writes_its_telemetry(hosted, tmp_path, source):
    project = hosted.project()
    owner_token = hosted.user("tel-owner@t.io", project, "runner")
    other_token = hosted.user("tel-other@t.io", project, "runner")
    owner = hosted.lab(tmp_path / "o", project, owner_token)
    exp = owner.create(toy.toy, inputs={"src": source}).run()
    exec_id = {n["node"]: n["execution_id"] for n in exp.node_runs()}["train"]
    chunk = {"seq": 99, "stream": "stdout", "t": 0.0, "text": "forged"}
    assert rpc(hosted, other_token, project, "write_telemetry", exec_id, logs=[chunk]).status_code == 403
    assert rpc(hosted, other_token, project, "record_environment", exec_id, "h", {}).status_code == 403
    assert rpc(hosted, owner_token, project, "write_telemetry", exec_id, logs=[chunk]).status_code == 200
    assert "forged" in "".join(c["text"] for c in owner.db.logs(exec_id))
    assert owner.db.environment(exec_id)["python"]  # recorded by the run itself
    owner.db.dispose()


def test_unlisted_methods_and_outsiders_are_refused(hosted):
    project = hosted.project()
    assert rpc(hosted, hosted.admin_token, project, "tx").status_code == 404
    assert rpc(hosted, hosted.admin_token, project, "_upsert_node_run").status_code == 404
    outsider = hosted.user("stranger@t.io")
    assert rpc(hosted, outsider, project, "experiments").status_code == 403
    assert rpc(hosted, "dtk_not-a-token", project, "experiments").status_code == 401
    assert api(hosted, hosted.admin_token, "GET", "/api/v1/projects/nope/members").status_code == 404


def test_leases_are_bound_to_their_holder(hosted, tmp_path, source):
    project = hosted.project()
    a, b = hosted.user("la@t.io", project, "runner"), hosted.user("lb@t.io", project, "runner")
    assert rpc(hosted, a, project, "acquire_lease", "sig", "w1", 60).json()["result"] is True
    # Same worker name, different person: not the holder.
    assert rpc(hosted, b, project, "renew_lease", "sig", "w1", 60).json()["result"] is False
    assert rpc(hosted, b, project, "acquire_lease", "sig", "w1", 60).json()["result"] is False
    assert rpc(hosted, a, project, "renew_lease", "sig", "w1", 60).json()["result"] is True

    lab = hosted.lab(tmp_path / "b", project, b)
    exp = lab.create(toy.toy, inputs={"src": source})
    ref = lab.ingest(source)
    exec_id = lab.db.start_execution(
        signature="sig", node="extract", spec={}, cacheable=True, experiment_id=exp.id, task_run_id=None
    )
    with pytest.raises(Exception, match="another worker holds the lease"):
        lab.db.publish(
            exec_id,
            experiment_id=exp.id,
            node="extract",
            inputs={},
            outputs={"rows": ref},
            metric_values=[],
            node_run={"status": "success", "execution_id": exec_id},
            lease=("sig", "w1"),
        )
    lab.db.dispose()


def test_publish_refuses_uncommitted_blobs(hosted, tmp_path, source):
    from dendrite.store import ArtifactRef

    lab = hosted.lab(tmp_path / "p")
    exp = lab.create(toy.toy, inputs={"src": source})
    exec_id = lab.db.start_execution(
        signature="s2", node="extract", spec={}, cacheable=True, experiment_id=exp.id, task_run_id=None
    )
    ghost = ArtifactRef("ab" * 32, "file", 3, "x")
    with pytest.raises(Exception, match="not committed"):
        lab.db.publish(
            exec_id,
            experiment_id=exp.id,
            node="extract",
            inputs={},
            outputs={"rows": ghost},
            metric_values=[],
            node_run={"status": "success", "execution_id": exec_id},
            lease=None,
        )
    with pytest.raises(Exception, match="not committed"):
        lab.db.register_artifacts([ghost])
    lab.db.dispose()


# -- blobs ----------------------------------------------------------------------------
def test_uploads_skip_what_the_project_has(hosted, tmp_path, monkeypatch):
    lab = hosted.lab(tmp_path / "d")
    f = tmp_path / "big.bin"
    f.write_bytes(b"x" * 100_000)
    first = lab.ingest(f)
    uploads = []
    monkeypatch.setattr(ServerStore, "_upload", lambda self, plan, src: uploads.append(plan))
    again = lab.ingest(f)
    assert again == first and uploads == []
    lab.db.dispose()


def test_mismatched_bytes_are_rejected(hosted, tmp_path):
    lab = hosted.lab(tmp_path / "m")
    store = lab.store
    f = tmp_path / "real.txt"
    f.write_bytes(b"honest bytes")
    lying_hash = hashlib.sha256(b"something else").hexdigest()
    with pytest.raises(Exception, match="don't match their hash"):
        store._put_missing([(lying_hash, f)])
    assert not store.has(lying_hash)
    lab.db.dispose()


def test_multipart_uploads_are_verified(hosted, tmp_path):
    s = hosted.server.settings
    old = (s.multipart_threshold, s.part_size)
    s.multipart_threshold, s.part_size = 1024, 5 * 1024**2  # S3's minimum part size
    try:
        lab = hosted.lab(tmp_path / "mp")
        f = tmp_path / "large.bin"
        f.write_bytes(bytes(range(256)) * (11 * 1024**2 // 256))  # 11 MiB: three parts
        ref = lab.ingest(f)
        assert lab.store.has(ref.hash)
        assert lab.store.read_bytes(ref.hash) == f.read_bytes()
        lab.db.dispose()
    finally:
        s.multipart_threshold, s.part_size = old


def test_presence_checks_are_batched(hosted, tmp_path, monkeypatch):
    project = hosted.project(store_url=str(tmp_path / "server-store"))
    lab = hosted.lab(tmp_path / "b", project)
    d = tmp_path / "many"
    d.mkdir()
    for i in range(2500):
        (d / f"{i}.txt").write_text(str(i))
    ref = hosted.server.project(project).store.put_path(d)  # stored server-side: this test is about checking
    calls = []
    real = lab.store.client.request

    def counting(method, path, *a, **kw):
        calls.append(path)
        return real(method, path, *a, **kw)

    monkeypatch.setattr(lab.store.client, "request", counting)
    assert lab.store.verify(ref)
    assert sum(p.endswith("blobs/presence") for p in calls) == 4  # the manifest, then 3 batches of <=1000
    lab.db.dispose()


def test_proxied_store_when_the_bucket_cannot_presign(hosted, tmp_path, source):
    project = hosted.project(store_url=str(tmp_path / "server-store"))
    lab = hosted.lab(tmp_path / "px", project)
    exp = lab.create(toy.toy, inputs={"src": source}).run()
    assert exp.load("featurize") == [3, 7, 11, 15]
    assert any((tmp_path / "server-store" / "objects").iterdir())
    lab.db.dispose()


def test_the_server_never_unpickles(hosted, tmp_path, source, monkeypatch):
    lab = hosted.lab(tmp_path / "u")
    exp = lab.create(toy.toy, inputs={"src": source}).run()
    h = exp.output("featurize").hash
    import dendrite.server

    def refuse(*_a, **_k):
        raise AssertionError("the hosted server unpickled a value")

    monkeypatch.setattr(dendrite.server.pickle, "loads", refuse)
    s = httpx.Client(base_url=hosted.url, headers={"Authorization": f"Bearer {hosted.admin_token}"})
    detail = s.get(f"/p/{lab.settings.project}/api/artifacts/{h}").json()
    assert detail["preview"] == {"type": "value", "repr": "[3, 7, 11, 15]"}
    lab.db.dispose()


# -- storage on the server ------------------------------------------------------------
def test_storage_reports_and_gc_run_on_the_server(hosted, tmp_path, source):
    project = hosted.project()
    lab = hosted.lab(tmp_path / "s", project)
    a = lab.create(toy.toy, inputs={"src": source}).run()
    inv = lab.storage()
    assert inv["store"]["objects"] > 0
    assert a.id in {e["id"] for e in inv["experiments"]}
    a.delete()
    report = lab.gc(dry_run=False, grace=0)
    assert report["deleted"]
    runner = hosted.lab(tmp_path / "r", project, hosted.user("gc@t.io", project, "runner"))
    with pytest.raises(PermissionError):
        runner.gc(dry_run=False, grace=0)
    for x in (lab, runner):
        x.db.dispose()


# -- login and tokens -------------------------------------------------------------------
def test_device_login_through_the_browser(hosted, tmp_path, monkeypatch):
    from dendrite import remote

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    opened = []
    monkeypatch.setattr(remote.webbrowser, "open", opened.append)
    browser = httpx.Client(base_url=hosted.url, follow_redirects=True)

    def approve_on_first_poll(_seconds):  # what the person does in their browser meanwhile
        if opened and not getattr(approve_on_first_poll, "done", False):
            approve_on_first_poll.done = True
            url = opened[0].removeprefix(hosted.url)
            assert "Sign in" in browser.get(url).text  # not signed in yet: the sign-in page
            page = browser.post("/auth/dev", data={"email": "dana@t.io", "name": "Dana", "next": url}).text
            code = re.search(r'name="code" value="([^"]+)"', page).group(1)
            csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
            assert "Approved" in browser.post("/device", data={"code": code, "csrf": csrf}).text

    monkeypatch.setattr(remote.time, "sleep", approve_on_first_poll)
    token = remote.device_login(hosted.url, out=open(tmp_path / "out.txt", "w"))
    assert approve_on_first_poll.done and token.startswith("dtk_")
    assert remote.load_credentials()[hosted.url]["token"] == token
    assert oct(remote.credentials_path().stat().st_mode)[-3:] == "600"
    who = api(hosted, token, "GET", "/api/v1/session").json()
    assert who["user"]["email"] == "dana@t.io"
    # Logging out revokes the token on the server.
    assert api(hosted, token, "POST", "/api/v1/logout").json() == {"revoked": True}
    assert api(hosted, token, "GET", "/api/v1/session").status_code == 401


def test_without_credentials_a_non_interactive_run_fails_fast(hosted, tmp_path, monkeypatch, source):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
    monkeypatch.delenv("DENDRITE_TOKEN", raising=False)
    lab = Lab(tmp_path / "ci", server=hosted.url, project=hosted.project())  # constructing never prompts
    with pytest.raises(NotLoggedIn, match="dendrite login"):
        lab.create(toy.toy, inputs={"src": source})


def test_service_tokens_are_scoped_to_one_project(hosted, tmp_path, source, monkeypatch):
    one, two = hosted.project(), hosted.project()
    r = api(hosted, hosted.admin_token, "POST", "/api/v1/tokens", json={"project": one, "role": "runner", "name": "ci"})
    token = r.json()["token"]
    monkeypatch.setenv("DENDRITE_TOKEN", token)
    lab = Lab(tmp_path / "ci", server=hosted.url, project=one)
    exp = lab.create(toy.toy, inputs={"src": source}).run()
    assert exp.status == "completed" and lab.db.experiments()[0]["created_by"] == "ci"
    assert rpc(hosted, token, two, "experiments").status_code == 403
    tid = r.json()["id"]
    assert api(hosted, hosted.admin_token, "DELETE", f"/api/v1/tokens/{tid}").json() == {"revoked": True}
    assert rpc(hosted, token, one, "experiments").status_code == 401
    lab.db.dispose()


def test_outdated_clients_are_told_to_upgrade(hosted):
    r = httpx.get(hosted.url + "/api/v1/info", headers={"X-Dendrite-Version": "0.0.1"})  # min_client is 0.1.0
    assert r.status_code == 426 and "dendrite >= 0.1.0" in r.json()["error"]["message"]
    assert httpx.get(hosted.url + "/api/v1/info", headers={"X-Dendrite-Version": "0.1.0"}).status_code == 200


# -- the hosted UI --------------------------------------------------------------------
def test_ui_requires_sign_in_and_csrf(hosted):
    project = hosted.project()
    anon = httpx.Client(base_url=hosted.url)
    r = anon.get(f"/p/{project}/")
    assert r.status_code == 303 and r.headers["location"].startswith("/auth/login")
    assert anon.get(f"/p/{project}/api/experiments").status_code == 401

    browser = httpx.Client(base_url=hosted.url)
    viewer = hosted.server.control.upsert_user("dev", "eve@t.io", "eve@t.io", "Eve")
    hosted.server.control.set_member(project, viewer["id"], "viewer")
    browser.post("/auth/dev", data={"email": "eve@t.io", "next": "/"})
    info = browser.get(f"/p/{project}/api/info").json()
    assert info["hosted"]["role"] == "viewer" and info["source_edit"] is False
    assert info["db"] == "server"  # the database URL is never shown
    # A mutating request with the session cookie but without the CSRF header is refused.
    assert browser.post(f"/p/{project}/api/gc", json={}).status_code == 403
    # With the header, a viewer is still refused storage reclamation.
    hdr = {"X-Dendrite-CSRF": info["hosted"]["csrf"]}
    assert browser.post(f"/p/{project}/api/gc", json={}, headers=hdr).status_code == 403
    assert browser.get(f"/p/{project}/api/storage").status_code == 200


# -- OIDC sign-in, against a fake issuer ---------------------------------------------
def _fake_issuer(verified: bool, subject: str):
    import base64 as b64
    import hashlib as hl

    from fastapi import FastAPI, Form

    app = FastAPI()
    codes: dict[str, str] = {}  # code -> PKCE challenge it was issued for
    app.state.codes = codes

    @app.get("/.well-known/openid-configuration")
    def discovery():
        base = "https://issuer.test"
        return {
            "issuer": base,
            "authorization_endpoint": f"{base}/authorize",
            "token_endpoint": f"{base}/token",
            "userinfo_endpoint": f"{base}/userinfo",
        }

    @app.post("/token")
    def token(code: str = Form(...), code_verifier: str = Form(...), client_id: str = Form(...)):
        challenge = b64.urlsafe_b64encode(hl.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()
        if codes.get(code) != challenge or client_id != "dendrite-app":
            return {"error": "invalid_grant"}, 400
        return {"access_token": "at-" + code, "token_type": "Bearer"}

    @app.get("/userinfo")
    def userinfo():
        return {"sub": subject, "email": f"{subject}@corp.example", "email_verified": verified, "name": "Olive"}

    return app


@pytest.mark.parametrize("verified", [True, False])
def test_oidc_sign_in_with_pkce(pg_url, tmp_path, verified):
    from urllib.parse import parse_qs, urlparse

    from fastapi.testclient import TestClient

    from dendrite.hosted.app import Hosted, create_server_app
    from dendrite.hosted.settings import ServerSettings

    settings = ServerSettings(
        base_url="http://127.0.0.1:9999",
        database_url=pg_url,
        store_url=str(tmp_path / "store"),
        home=tmp_path / "srv",
        auth={"provider": "oidc", "issuer": "https://issuer.test", "client_id": "dendrite-app", "client_secret": "s"},
    )
    server = Hosted(settings)
    subject = f"user-{uuid_hex()}"
    issuer = _fake_issuer(verified, subject)
    app = create_server_app(settings, server)
    app.state.providers.http = TestClient(issuer, base_url="https://issuer.test")
    browser = TestClient(app, base_url=settings.base_url)

    r = browser.get("/auth/login", params={"next": "/device"}, follow_redirects=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert r.headers["location"].startswith("https://issuer.test/authorize")
    assert q["code_challenge_method"] == ["S256"] and q["client_id"] == ["dendrite-app"]
    issuer.state.codes["c1"] = q["code_challenge"][0]  # the issuer issues a code for this challenge

    # An admin invited this address earlier; only a verified sign-in may claim the invitation.
    project = f"oidc{uuid_hex()}"
    server.create_project(project)
    invite = server.control.invite(f"{subject}@corp.example")
    server.control.set_member(project, invite["id"], "runner")
    done = browser.get("/auth/callback", params={"code": "c1", "state": q["state"][0]}, follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"] == "/device"
    me = server.control.upsert_user("https://issuer.test", subject, None, None)
    assert me["name"] == "Olive"
    assert me["email"] == (f"{subject}@corp.example" if verified else None)  # unverified emails don't count
    assert server.control.role(me, project) == ("runner" if verified else None)
    # A replayed or unknown state is refused.
    again = browser.get("/auth/callback", params={"code": "c1", "state": q["state"][0]}, follow_redirects=False)
    assert "Sign-in failed" in again.text


def uuid_hex() -> str:
    import uuid

    return uuid.uuid4().hex[:8]


def test_adopt_an_existing_direct_mode_schema(hosted, pg_schema_url, tmp_path, source):
    import sqlalchemy as sa

    store = tmp_path / "direct-store"
    direct = Lab(tmp_path / "direct", db_url=pg_schema_url, store_url=str(store))
    toy.CALLS.clear()
    exp = direct.create(toy.toy, inputs={"src": source}).run()
    direct.db.dispose()
    schema = sa.engine.make_url(pg_schema_url).query["options"].split("=", 1)[1]
    name = f"adopted{uuid_hex()}"
    hosted.server.create_project(name, db_schema=schema, store_url=str(store))

    lab = hosted.lab(tmp_path / "client", name)
    assert [e.id for e in lab.experiments()] == [exp.id]  # history carries over
    again = lab.create(toy.toy, inputs={"src": source}).run()
    assert set(statuses(again).values()) == {"cached"} and toy.CALLS["extract"] == 1  # and so does the cache
    lab.db.dispose()


def test_an_expired_sign_in_code_stops_the_cli(hosted, tmp_path, monkeypatch):
    from dendrite import remote
    from dendrite.hosted import control as ctl

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(remote.webbrowser, "open", lambda _url: None)

    def expire_codes(_seconds):  # nobody approves; the code runs out
        with hosted.server.engine.begin() as conn:
            conn.execute(ctl.device_logins.update().values(expires_at=0))

    monkeypatch.setattr(remote.time, "sleep", expire_codes)
    with pytest.raises(NotLoggedIn, match="expired"):
        remote.device_login(hosted.url, out=open(tmp_path / "out.txt", "w"))
    assert remote.load_credentials() == {}


def test_the_server_never_opens_a_recorded_source_path(hosted, monkeypatch):
    import dendrite.server

    project = hosted.project()
    runner = hosted.user(f"src{uuid_hex()}@t.io", project, "runner")
    snap = {
        "hash": "f" * 64,
        "source": "def f(): pass\n",
        "file": "/etc/hosts",
        "line": 1,
        "module": "m",
        "qualname": "f",
    }
    assert rpc(hosted, runner, project, "save_snapshot", snap).status_code == 200

    def refuse(_snap):
        raise AssertionError("the hosted server opened a client-recorded path")

    monkeypatch.setattr(dendrite.server, "disk_status", refuse)
    r = api(hosted, runner, "GET", f"/p/{project}/api/sources/{'f' * 64}").json()
    assert r["status"] == "elsewhere" and r["file_sha"] is None and r["editable"] is False


def test_a_hosted_server_never_plans_or_runs_pipeline_code(hosted, tmp_path, source):
    lab = hosted.lab(tmp_path / "pl")
    draft = lab.create(toy.toy, inputs={"src": source}, draft=True)  # toy is imported in this process
    p = lab.settings.project
    r = api(hosted, hosted.admin_token, "GET", f"/p/{p}/api/experiments/{draft.id}/plan")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "pipeline_not_imported"
    assert api(hosted, hosted.admin_token, "GET", f"/p/{p}/api/pipelines/toy/shape").status_code == 409
    assert api(hosted, hosted.admin_token, "GET", f"/p/{p}/api/info").json()["plan"] is False
    assert draft.plan()["nodes"]  # planning happens where the code is
    lab.db.dispose()


def test_value_previews_belong_to_whoever_uploaded_the_value(hosted, tmp_path):
    from dendrite.hosted import wire

    project = hosted.project()
    a = hosted.lab(tmp_path / "pa", project, hosted.user(f"pa{uuid_hex()}@t.io", project, "runner"))
    ref = a.store.put_value({"answer": 42})
    mallory = hosted.user(f"m{uuid_hex()}@t.io", project, "runner")
    body = {"items": [{"hash": ref.hash, "size": ref.size, "preview": "you've been hacked"}]}
    api(hosted, mallory, "POST", f"/api/v1/projects/{project}/blobs/missing", content=wire.dumps(body))
    assert hosted.server.control.value_preview(project, ref.hash) == "{'answer': 42}"
    a.db.dispose()


def test_publish_refuses_uncommitted_inputs(hosted, tmp_path, source):
    from dendrite.store import ArtifactRef

    lab = hosted.lab(tmp_path / "pi")
    exp = lab.create(toy.toy, inputs={"src": source})
    out = lab.ingest(source)
    exec_id = lab.db.start_execution(
        signature="s3", node="extract", spec={}, cacheable=True, experiment_id=exp.id, task_run_id=None
    )
    with pytest.raises(Exception, match="not committed"):
        lab.db.publish(
            exec_id,
            experiment_id=exp.id,
            node="extract",
            inputs={"src": ArtifactRef("cd" * 32, "file", 3, "x")},
            outputs={"rows": out},
            metric_values=[],
            node_run={"status": "success", "execution_id": exec_id},
            lease=None,
        )
    lab.db.dispose()


def test_two_server_processes_share_uploads_and_replies(hosted):
    from fastapi.testclient import TestClient

    from dendrite.hosted import wire
    from dendrite.hosted.app import Hosted, create_server_app

    other = TestClient(create_server_app(hosted.server.settings, Hosted(hosted.server.settings)))  # replica B
    project = hosted.project()
    auth = {"Authorization": f"Bearer {hosted.admin_token}"}

    data = bytes(range(256)) * 2000  # above the inline limit, so it's a presigned upload
    h = hashlib.sha256(data).hexdigest()
    body = wire.dumps({"items": [{"hash": h, "size": len(data)}]})
    plan = wire.loads(
        api(hosted, hosted.admin_token, "POST", f"/api/v1/projects/{project}/blobs/missing", content=body).content
    )["uploads"][0]
    assert httpx.put(plan["url"], content=data, headers=plan["headers"]).status_code == 200
    # Planned on A, committed on B.
    done = other.post(
        f"/api/v1/projects/{project}/blobs/commit", content=wire.dumps({"upload": plan["upload"]}), headers=auth
    )
    assert wire.loads(done.content)["state"] == "committed"

    # A retried write that lands on B replays A's reply instead of running again.
    call = wire.dumps({"args": ["sig-r", "w", 60], "kwargs": {}})
    first = api(
        hosted,
        hosted.admin_token,
        "POST",
        f"/api/v1/projects/{project}/rpc/acquire_lease",
        content=call,
        headers={"Idempotency-Key": "k-1"},
    )
    retry = other.post(
        f"/api/v1/projects/{project}/rpc/acquire_lease", content=call, headers={**auth, "Idempotency-Key": "k-1"}
    )
    fresh = other.post(
        f"/api/v1/projects/{project}/rpc/acquire_lease", content=call, headers={**auth, "Idempotency-Key": "k-2"}
    )
    assert wire.loads(first.content)["result"] is True and wire.loads(retry.content)["result"] is True
    assert wire.loads(fresh.content)["result"] is False  # without the key it really ran again: the lease is held


def test_github_sign_in_uses_the_verified_primary_email(pg_url, tmp_path):
    from urllib.parse import parse_qs, urlparse

    from fastapi import FastAPI, Form, Header
    from fastapi.testclient import TestClient

    from dendrite.hosted.app import Hosted, create_server_app
    from dendrite.hosted.settings import ServerSettings

    gh = FastAPI()  # api.github.com and github.com, as far as sign-in needs them
    uid = uuid_hex()

    @gh.post("/login/oauth/access_token")
    def access_token(client_id: str = Form(...), client_secret: str = Form(...), code: str = Form(...)):
        assert (client_id, client_secret, code) == ("gh-app", "gh-secret", "c-gh")
        return {"access_token": "gho_x", "token_type": "bearer"}

    @gh.get("/user")
    def user(authorization: str = Header(...)):
        assert authorization == "Bearer gho_x"
        return {"id": int(uid, 16), "login": "octo", "name": None}

    @gh.get("/user/emails")
    def emails():
        return [
            {"email": f"old-{uid}@example.com", "primary": False, "verified": True},
            {"email": f"unverified-{uid}@example.com", "primary": True, "verified": False},
        ]

    settings = ServerSettings(
        base_url="http://127.0.0.1:9998",
        database_url=pg_url,
        store_url=str(tmp_path / "store"),
        home=tmp_path / "srv",
        auth={"provider": "github", "client_id": "gh-app", "client_secret": "gh-secret"},
    )
    server = Hosted(settings)
    app = create_server_app(settings, server)
    app.state.providers.http = TestClient(gh)  # routes every github.com / api.github.com call here
    browser = TestClient(app, base_url=settings.base_url)
    r = browser.get("/auth/login", follow_redirects=False)
    assert r.headers["location"].startswith("https://github.com/login/oauth/authorize?")
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    done = browser.get("/auth/callback", params={"code": "c-gh", "state": state}, follow_redirects=False)
    assert done.status_code == 303 and "dendrite_session" in done.cookies
    me = server.control.upsert_user("github", str(int(uid, 16)), None, None)
    assert me["name"] == "octo" and me["email"] is None  # the primary email isn't verified, so no email


def test_server_check_reports_what_the_store_enforces(hosted):
    from dendrite.hosted.check import run_checks

    seen = []
    ok = run_checks(hosted.server.settings, lambda status, name, detail: seen.append((status, name)))
    found = dict((name, status) for status, name in seen)
    assert ok and found["database"] == "ok" and found["presigned upload"] == "ok"
    assert found["upload checksums"] == "warn"  # moto accepts mismatched bytes; the check says so
    assert found["no overwrites"] == "ok" and found["presigned download"] == "ok"


# -- dendrite push ----------------------------------------------------------------
def test_push_local_experiments_to_the_server(hosted, tmp_path, source):
    from dendrite.push import push

    local = Lab(tmp_path / "laptop", db_url=f"sqlite:///{tmp_path / 'laptop' / 'lineage.db'}")
    base = local.create(toy.toy, inputs={"src": source}, name="base").run()
    fork = base.fork({"train.iterations": 3}, name="fork").run()
    project = hosted.project()
    me = hosted.lab(tmp_path / "me", project, hosted.user(f"pusher{uuid_hex()}@t.io", project, "runner"))
    notes = []
    out = push(local, me, [fork.id], report=notes.append)  # the parent comes along
    assert set(out["created"]) == {base.id, fork.id} and not out["reused"]
    rows = {e["id"]: e for e in me.db.experiments()}
    assert rows[fork.id]["parent_id"] == base.id and rows[fork.id]["created_by"].startswith("pusher")
    assert statuses(me.get(fork.id)) == statuses(fork)
    assert me.get(fork.id).load("featurize") == [3, 7, 11, 15]  # served from the bucket now

    # A teammate's identical run is a cache hit on what was pushed.
    toy.CALLS.clear()
    mate = hosted.lab(tmp_path / "mate", project, hosted.user(f"mate{uuid_hex()}@t.io", project, "runner"))
    again = mate.create(toy.toy, inputs={"src": source}).run()
    assert set(statuses(again).values()) == {"cached"} and sum(toy.CALLS.values()) == 0

    # Pushing again changes nothing.
    second = push(local, me, None, report=notes.append)
    assert second["created"] == [] and set(second["skipped"]) == {base.id, fork.id}
    for lab in (local, me, mate):
        lab.db.dispose()


def test_names_are_attributed_and_guarded(hosted, tmp_path, source):
    project = hosted.project()
    alice = hosted.lab(tmp_path / "a", project, hosted.user(f"alice{uuid_hex()}@t.io", project, "runner"))
    bob = hosted.lab(tmp_path / "b", project, hosted.user(f"bob{uuid_hex()}@t.io", project, "runner"))
    exp = alice.create(toy.toy, inputs={"src": source}).run()
    row = alice.name("best", f"{exp.id}:train.model")
    assert row["set_by"].startswith("alice")  # the server's record of who, not the client's
    assert bob.load("@best").read_text()  # anyone in the project can use it
    with pytest.raises(PermissionError):
        bob.name("best", f"{exp.id}:featurize", move=True)
    with pytest.raises(PermissionError):
        bob.unname("best")
    bob.name("bobs", f"{exp.id}:featurize")  # runners may create their own
    alice.name("best", f"{exp.id}:featurize", move=True)
    assert [h["hash"] for h in alice.db.name_history("best")] == [row["hash"]]
    assert alice.db.name("best")["set_by_id"] and alice.db.name("best")["set_by_id"] != bob.db.name("bobs")["set_by_id"]
    with pytest.raises(PermissionError):
        bob.name("best", f"{exp.id}:featurize", tags=["mine"])  # re-tagging is changing it too
    hosted.lab(tmp_path / "admin", project).unname("bobs")  # admins may change anyone's
    for lab in (alice, bob):
        lab.db.dispose()


def test_push_carries_names_without_overwriting(hosted, tmp_path, source):
    from dendrite.push import push

    project = hosted.project()
    me = hosted.lab(tmp_path / "me", project, hosted.user(f"namer{uuid_hex()}@t.io", project, "runner"))
    theirs = me.create(toy.toy, inputs={"src": source}).run()
    me.name("taken", f"{theirs.id}:featurize")
    local = Lab(tmp_path / "laptop", db_url=f"sqlite:///{tmp_path / 'laptop' / 'lineage.db'}")
    mine = local.create(toy.toy, inputs={"src": source}).fork({"train.iterations": 4}).run()
    local.name("fresh", f"{mine.id}:train.model")
    local.name("taken", f"{mine.id}:train.model")
    notes = []
    out = push(local, me, [mine.id], report=notes.append)
    assert out["names"] == ["fresh"] and out["names_skipped"] == ["taken"]
    assert me.artifact("@fresh").hash == mine.output("train", "model").hash
    assert me.artifact("@taken").hash == theirs.output("featurize").hash
    assert me.db.name("fresh")["set_by"].startswith("namer")
    assert any("@taken" in n for n in notes)
    for lab in (local, me):
        lab.db.dispose()


def test_push_matches_executions_the_project_already_has(hosted, tmp_path, source):
    from dendrite.push import push

    project = hosted.project()
    first = hosted.lab(tmp_path / "first", project)
    first.create(toy.toy, inputs={"src": source}).run()  # the project computes it first
    local = Lab(tmp_path / "laptop", db_url=f"sqlite:///{tmp_path / 'laptop' / 'lineage.db'}")
    mine = local.create(toy.toy, inputs={"src": source}).run()
    me = hosted.lab(tmp_path / "me", project)
    out = push(local, me, [mine.id], report=lambda _m: None)
    assert (
        out["created"] == [mine.id] and out["executions"] == [] and len(out["reused"]) == 3
    )  # extract, featurize, train
    for lab in (first, local, me):
        lab.db.dispose()


def test_push_leaves_out_what_was_evicted_locally(hosted, tmp_path, source):
    from dendrite.push import push

    local = Lab(tmp_path / "laptop", db_url=f"sqlite:///{tmp_path / 'laptop' / 'lineage.db'}")
    exp = local.create(toy.toy, inputs={"src": source}).run()
    train = exp.node_outputs("train")
    local.store.delete_many(train.values())  # evicted on the laptop
    me = hosted.lab(tmp_path / "me", hosted.project())
    notes = []
    out = push(local, me, [exp.id], report=notes.append)
    assert out["created"] == [exp.id] and any("no longer stored" in n for n in notes)
    runs = {r["node"]: r for r in me.db.node_runs(exp.id)}
    assert runs["train"]["execution_id"] is None and runs["extract"]["execution_id"]
    # Nothing the server can't serve was recorded as a result: a new run recomputes train only.
    toy.CALLS.clear()
    me.create(toy.toy, inputs={"src": source}).run()
    assert toy.CALLS["train"] == 1 and toy.CALLS["extract"] == 0
    for lab in (local, me):
        lab.db.dispose()


def test_viewers_cannot_push(hosted, tmp_path, source):
    from dendrite.push import push

    local = Lab(tmp_path / "laptop", db_url=f"sqlite:///{tmp_path / 'laptop' / 'lineage.db'}")
    exp = local.create(toy.toy, inputs={"src": source}).run()
    project = hosted.project()
    viewer = hosted.lab(tmp_path / "v", project, hosted.user(f"v{uuid_hex()}@t.io", project, "viewer"))
    with pytest.raises(PermissionError):
        push(local, viewer, [exp.id], report=lambda _m: None)
    for lab in (local, viewer):
        lab.db.dispose()
