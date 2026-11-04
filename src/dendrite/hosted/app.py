"""The hosted dendrite server: the versioned API, sign-in, and every project's web UI.

``/api/v1`` is what clients use:

* ``projects/{p}/rpc/{method}`` (and ``rpc`` for a batch) calls one allowlisted ``Metadata``
  method on the project's ``ServerMetadata``, as the authenticated caller;
* ``projects/{p}/blobs/…`` plans, commits and serves blob transfers (``blobs.py``);
* ``projects/{p}/storage/{op}`` runs storage reports and reclamation on the server;
* ``projects``, ``members`` and ``tokens`` manage access.

Each project's web UI is the ordinary dendrite app mounted at ``/p/{project}/`` (``ui.py``).
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import threading
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from sqlalchemy.exc import IntegrityError

from .. import __version__
from ..experiment import Lab
from ..store import open_store
from . import wire
from .authz import CALLER, Conflict, Forbidden, ServerMetadata
from .blobs import Broker, UploadError
from .control import Caller, Control, role_at_least
from .settings import ServerSettings

log = logging.getLogger("dendrite.server")
IDEMPOTENCY_SECONDS = 600


def _version(v: str) -> tuple[int, ...]:
    out = []
    for part in v.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits or 0))
    return tuple(out)


@contextmanager
def acting(caller: Caller | None):
    token = CALLER.set(caller)
    try:
        yield
    finally:
        CALLER.reset(token)


class ApiError(Exception):
    def __init__(self, status: int, kind: str, message: str):
        super().__init__(message)
        self.status, self.kind, self.message = status, kind, message


def _as_api_error(e: Exception) -> ApiError:
    if isinstance(e, ApiError):
        return e
    if isinstance(e, HTTPException):
        return ApiError(e.status_code, "HTTPException", str(e.detail))
    if isinstance(e, Forbidden):
        return ApiError(403, "PermissionError", str(e))
    if isinstance(e, Conflict):
        return ApiError(409, "Conflict", str(e))
    if isinstance(e, UploadError):
        return ApiError(400, "UploadError", str(e))
    if type(e).__name__ in ("StorageBusy", "EvictRefused", "NotADraft", "PlanIncomplete"):
        return ApiError(409, type(e).__name__, str(e))
    if isinstance(e, IntegrityError):
        return ApiError(409, "IntegrityError", str(e.orig))
    if isinstance(e, (ValueError, KeyError, LookupError, TypeError)):
        msg = e.args[0] if isinstance(e, KeyError) and e.args else str(e)
        return ApiError(422, type(e).__name__, str(msg))
    log.exception("internal error")
    return ApiError(500, type(e).__name__, str(e))


def _error_response(e: Exception) -> JSONResponse:
    err = _as_api_error(e)
    return JSONResponse({"error": {"type": err.kind, "message": err.message}}, status_code=err.status)


class Project:
    """One project's server-side lab: authorizing metadata, its store and the blob broker."""

    def __init__(self, server: Hosted, row: dict):
        s = server.settings
        url = sa.engine.make_url(s.database_url).update_query_dict({"options": f"-csearch_path={row['db_schema']}"})
        home = s.home / "projects" / row["name"]
        store = open_store(row["store_url"], home / "cache", **s.store_options)
        self.name = row["name"]
        self.store = store
        self.db = ServerMetadata(
            url.render_as_string(hide_password=False), project=self.name, control=server.control, store=store
        )
        self.lab = Lab.from_parts(self.db, store, home)
        if s.gc_grace is not None:
            from dataclasses import replace

            self.lab.settings = replace(self.lab.settings, gc_grace=float(s.gc_grace))
        self.broker = Broker(store, s, server.control, row["name"])
        self.tmp = home / "uploads"
        self.ui_app = None  # the project's web UI, built on first use (ui.py)

    def close(self) -> None:
        self.db.dispose()


class Hosted:
    def __init__(self, settings: ServerSettings):
        self.settings = settings
        settings.home.mkdir(parents=True, exist_ok=True)
        self.engine = sa.create_engine(settings.database_url, pool_pre_ping=True)
        self.control = Control(self.engine)
        self._projects: OrderedDict[str, Project] = OrderedDict()
        self._lock = threading.Lock()
        for email in settings.admins:  # admins named in the config are admins before they first sign in
            self.control.invite(email, admin=True)

    def project(self, name: str) -> Project:
        with self._lock:
            if name in self._projects:
                self._projects.move_to_end(name)
                return self._projects[name]
        row = self.control.project(name)
        if row is None:
            raise ApiError(404, "LookupError", f"no project {name!r}")
        p = Project(self, row)
        with self._lock:
            if name in self._projects:  # another request opened it meanwhile
                p.close()
                return self._projects[name]
            self._projects[name] = p
            while len(self._projects) > self.settings.max_open_projects:
                _, old = self._projects.popitem(last=False)
                old.close()  # requests still using it reconnect on demand
            return p

    def create_project(self, name: str, *, db_schema: str | None = None, store_url: str | None = None) -> Project:
        self.control.create_project(name, store_url or self.settings.project_store(name), db_schema=db_schema)
        return self.project(name)

    # -- who is asking -----------------------------------------------------------
    def identify(self, request: Request) -> tuple[dict, dict | None] | None:
        """``(user, token)`` from a bearer token, or ``(user, None)`` from a browser session."""
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            found = self.control.authenticate(auth[7:].strip())
            if found is None:
                raise ApiError(401, "NotLoggedIn", "this token is unknown, expired or revoked; run `dendrite login`")
            return found
        from .auth import session_user

        return session_user(self, request)

    def caller(self, request: Request, project: str | None, needed: str | None = "viewer") -> Caller:
        found = self.identify(request)
        if found is None:
            raise ApiError(401, "NotLoggedIn", "sign in first: run `dendrite login`")
        user, token = found
        if project is not None:
            self.project(project)  # 404 for an unknown project
        c = self.control.caller(user, token, project)
        if project is not None and needed and not (c.is_admin or role_at_least(c.role, needed)):
            if c.role is None:
                raise ApiError(403, "PermissionError", f"you are not a member of project {project!r}")
            raise ApiError(403, "PermissionError", f"needs the {needed} role in project {project!r}")
        return c

    # -- idempotency (in the database, so a retry may reach another server process) --------
    def remembered(self, caller: Caller, key: str | None) -> tuple[int, bytes] | None:
        return self.control.reply(caller.user_id, key, IDEMPOTENCY_SECONDS) if key else None

    def remember(self, caller: Caller, key: str | None, status: int, body: bytes) -> None:
        if key:
            self.control.save_reply(caller.user_id, key, status, body, IDEMPOTENCY_SECONDS)

    # -- remote metadata ---------------------------------------------------------
    def call(self, project: str, caller: Caller, method: str, args: list, kwargs: dict) -> Any:
        role = wire.REMOTE.get(method)
        if role is None:
            raise ApiError(404, "LookupError", f"{method} can't be called remotely")
        if not (caller.is_admin or role_at_least(caller.role, role)):
            raise ApiError(403, "PermissionError", f"{method} needs the {role} role")
        db = self.project(project).db
        with acting(caller):
            return getattr(db, method)(*args, **kwargs)


class VersionGate:
    """Refuse clients older than ``min_client`` (426). Plain ASGI: ``@app.middleware("http")``
    wraps request bodies in a way that stalls them under many concurrent uploads."""

    def __init__(self, app, min_client: str):
        self.app, self.min = app, _version(min_client)
        self.min_text = min_client

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            for name, value in scope.get("headers", []):
                if name == b"x-dendrite-version" and _version(value.decode()) < self.min:
                    msg = f"this server needs dendrite >= {self.min_text}"
                    resp = JSONResponse({"error": {"type": "UpgradeRequired", "message": msg}}, status_code=426)
                    return await resp(scope, receive, send)
        return await self.app(scope, receive, send)


def create_server_app(settings: ServerSettings, hosted: Hosted | None = None) -> FastAPI:
    server = hosted or Hosted(settings)
    app = FastAPI(title="dendrite server", version=__version__)
    app.state.server = server
    app.state.closing = threading.Event()

    @app.exception_handler(ApiError)
    async def api_error(_: Request, e: ApiError):
        return _error_response(e)

    app.add_middleware(VersionGate, min_client=settings.min_client)

    def json_body(raw: bytes) -> Any:
        return wire.loads(raw) if raw else {}

    def encoded(result: Any, status: int = 200) -> Response:
        return Response(wire.dumps(result), status_code=status, media_type="application/json")

    async def threaded(fn, *a):
        try:
            return await run_in_threadpool(fn, *a)
        except Exception as e:  # noqa: BLE001 - every error becomes an API error
            raise _as_api_error(e) from e

    # -- info and session --------------------------------------------------------
    @app.get("/api/v1/info")
    def info() -> dict[str, Any]:
        return {
            "version": __version__,
            "api": wire.API_VERSION,
            "min_client": settings.min_client,
            "auth": settings.auth.provider,
            "base_url": settings.base_url,
        }

    @app.get("/api/v1/session")
    async def session(request: Request):
        c = await threaded(server.caller, request, None, None)
        user = server.control.user(c.user_id)
        return {
            "user": {"id": c.user_id, "email": user["email"], "name": user["name"], "admin": c.is_admin},
            "label": c.label,
            "projects": server.control.projects_for(c.user_id),
            "token": c.token_id and server.control.token(c.token_id),
        }

    # -- remote metadata ---------------------------------------------------------
    @app.post("/api/v1/projects/{project}/rpc/{method}")
    async def rpc(project: str, method: str, request: Request):
        key = request.headers.get("idempotency-key")
        raw = await request.body()
        c = None
        try:
            c = await run_in_threadpool(server.caller, request, project)
            if (hit := await run_in_threadpool(server.remembered, c, key)) is not None:
                return Response(hit[1], status_code=hit[0], media_type="application/json")
            body = json_body(raw)
            result = await run_in_threadpool(
                server.call, project, c, method, body.get("args", []), body.get("kwargs", {})
            )
            resp = encoded({"result": result})
        except Exception as e:  # noqa: BLE001
            resp = _error_response(e)
        if c is not None and resp.status_code < 500:
            await run_in_threadpool(server.remember, c, key, resp.status_code, resp.body)
        return resp

    @app.post("/api/v1/projects/{project}/rpc")
    async def rpc_batch(project: str, request: Request):
        raw = await request.body()
        c = await threaded(server.caller, request, project)
        calls = json_body(raw).get("calls", [])

        def run() -> list:
            out = []
            for i, call in enumerate(calls):
                try:
                    out.append(server.call(project, c, call["method"], call.get("args", []), call.get("kwargs", {})))
                except Exception as e:  # noqa: BLE001
                    err = _as_api_error(e)
                    raise ApiError(err.status, err.kind, f"call {i} ({call['method']}): {err.message}") from e
            return out

        return encoded({"results": await threaded(run)})

    # -- blobs -------------------------------------------------------------------
    def proxy_url(project: str, h: str) -> str:
        return f"{settings.base_url}/api/v1/projects/{project}/blobs/{h}"

    @app.post("/api/v1/projects/{project}/blobs/missing")
    async def blobs_missing(project: str, request: Request):
        c = await threaded(server.caller, request, project, "runner")
        items = json_body(await request.body()).get("items", [])
        if len(items) > 1000:
            raise ApiError(400, "ValueError", "at most 1000 blobs per request")

        def plan() -> list:
            p = server.project(project)
            inline = [i for i in items if i.get("data") is not None]
            for i in inline:  # small blobs arrive with the request; hash them like a proxied upload
                if hashlib.sha256(i["data"]).hexdigest() != i["hash"]:
                    raise ApiError(400, "UploadError", f"blob {i['hash'][:12]}: bytes don't match their hash")
            present = p.store.has_many(i["hash"] for i in items)
            for i in inline:
                if i["hash"] not in present:
                    p.store._put_object_bytes(i["data"])
            # Only whoever uploads a value may describe it, and a recorded preview is never replaced.
            previews = {
                i["hash"]: str(i["preview"])[:65536]
                for i in items
                if i.get("preview") is not None and i["hash"] not in present
            }
            if previews:
                server.control.save_value_previews(project, previews)
            plans = p.broker.missing([i for i in items if i.get("data") is None and i["hash"] not in present])
            for p in plans:
                if p["method"] == "proxy":
                    p["url"] = proxy_url(project, p["hash"])
            server.control.log_signing(project, c, "put", [p["hash"] for p in plans])
            return plans

        return encoded({"uploads": await threaded(plan)})

    @app.post("/api/v1/projects/{project}/blobs/parts")
    async def blobs_parts(project: str, request: Request):
        await threaded(server.caller, request, project, "runner")
        body = json_body(await request.body())
        return encoded(
            {"parts": await threaded(server.project(project).broker.part_urls, body["upload"], body["parts"])}
        )

    @app.post("/api/v1/projects/{project}/blobs/commit")
    async def blobs_commit(project: str, request: Request):
        await threaded(server.caller, request, project, "runner")
        body = json_body(await request.body())
        broker = server.project(project).broker
        return encoded(await threaded(broker.commit, body["upload"], body.get("parts"), body.get("crc32c")))

    @app.post("/api/v1/projects/{project}/blobs/presence")
    async def blobs_presence(project: str, request: Request):
        await threaded(server.caller, request, project)
        hashes = json_body(await request.body()).get("hashes", [])
        if len(hashes) > 1000:
            raise ApiError(400, "ValueError", "at most 1000 hashes per request")
        present = await threaded(server.project(project).store.has_many, hashes)
        return encoded({"present": sorted(present)})

    @app.post("/api/v1/projects/{project}/blobs/urls")
    async def blobs_urls(project: str, request: Request):
        c = await threaded(server.caller, request, project)
        hashes = json_body(await request.body()).get("hashes", [])
        if len(hashes) > 1000:
            raise ApiError(400, "ValueError", "at most 1000 hashes per request")

        def sign() -> dict:
            urls = server.project(project).broker.download_urls(hashes)
            out = {h: (proxy_url(project, h) if u == "proxy" else u) for h, u in urls.items()}
            server.control.log_signing(project, c, "get", [h for h, u in out.items() if u])
            return out

        return encoded({"urls": await threaded(sign)})

    @app.put("/api/v1/projects/{project}/blobs/{h}")
    async def blob_put(project: str, h: str, request: Request):
        await threaded(server.caller, request, project, "runner")
        p = server.project(project)
        p.tmp.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.tmp, prefix=".up-")
        digest = hashlib.sha256()
        try:
            with os.fdopen(fd, "wb") as f:
                async for chunk in request.stream():
                    digest.update(chunk)
                    f.write(chunk)
            if digest.hexdigest() != h:
                raise ApiError(400, "UploadError", "uploaded bytes don't match their hash")
            if not await threaded(p.store.has, h):
                await threaded(p.store._put_object_file, h, Path(tmp))
        finally:
            Path(tmp).unlink(missing_ok=True)
        return {"hash": h, "state": "committed"}

    @app.get("/api/v1/projects/{project}/blobs/{h}")
    async def blob_get(project: str, h: str, request: Request):
        await threaded(server.caller, request, project)
        p = server.project(project)
        if not await threaded(p.store.has, h):
            raise ApiError(404, "LookupError", "no such blob")
        local = p.store.local_path(h)
        if local is not None:
            return FileResponse(local, media_type="application/octet-stream")

        def body():
            with p.store.open_object(h) as f:
                while chunk := f.read(1024 * 1024):
                    yield chunk

        return StreamingResponse(body(), media_type="application/octet-stream")

    @app.post("/api/v1/projects/{project}/blobs/delete")
    async def blobs_delete(project: str, request: Request):
        await threaded(server.caller, request, project, "admin")
        hashes = json_body(await request.body()).get("hashes", [])
        p = server.project(project)
        await threaded(p.store.delete_many, hashes)
        await threaded(p.store.drop_checkouts, hashes)
        return {"deleted": len(hashes)}

    # -- storage (runs on the server, against the project's store) -------------------
    STORAGE = {"inventory": "viewer", "estimate": "viewer", "gc": "admin", "evict": "admin"}

    @app.post("/api/v1/projects/{project}/storage/{op}")
    async def storage(project: str, op: str, request: Request):
        if op not in STORAGE:
            raise ApiError(404, "LookupError", f"no storage operation {op!r}")
        c = await threaded(server.caller, request, project, STORAGE[op])
        kw = json_body(await request.body())
        lab = server.project(project).lab

        def run() -> Any:
            with acting(c):
                if op == "inventory":
                    return lab.storage()
                if op == "estimate":
                    return lab.deletion_estimate(kw["ids"], forks=kw.get("forks", "keep"))
                if op == "gc":
                    return lab.gc(**kw)
                return lab.evict(**kw)

        return encoded({"result": await threaded(run)})

    # -- projects, members, tokens ------------------------------------------------
    @app.get("/api/v1/projects")
    async def list_projects(request: Request):
        c = await threaded(server.caller, request, None, None)
        return {"projects": server.control.projects_for(c.user_id)}

    @app.post("/api/v1/projects")
    async def create_project(request: Request):
        c = await threaded(server.caller, request, None, None)
        if not c.is_admin:
            raise ApiError(403, "PermissionError", "only server admins create projects")
        name = json_body(await request.body())["name"]
        await threaded(server.create_project, name)
        return {"name": name}

    @app.get("/api/v1/projects/{project}/members")
    async def members(project: str, request: Request):
        await threaded(server.caller, request, project, "admin")
        return {"members": server.control.members(project)}

    @app.put("/api/v1/projects/{project}/members")
    async def set_member(project: str, request: Request):
        await threaded(server.caller, request, project, "admin")
        body = json_body(await request.body())
        user = server.control.user_by_email(body["email"]) or server.control.invite(body["email"])
        await threaded(server.control.set_member, project, user["id"], body.get("role"))
        return {"email": body["email"], "role": body.get("role")}

    @app.post("/api/v1/tokens")
    async def create_token(request: Request):
        body = json_body(await request.body())
        project, role = body.get("project"), body.get("role")
        if not project or not role:
            raise ApiError(422, "ValueError", "a service token needs a project and a role")
        await threaded(server.caller, request, project, "admin")
        name = body.get("name") or f"{project}-{role}"
        svc = server.control.service_user(name)
        secret, rec = server.control.create_token(
            svc["id"], name=name, project=project, role=role, ttl=body.get("ttl_days", 365) * 86400
        )
        return {"token": secret, **rec}

    @app.get("/api/v1/tokens")
    async def list_tokens(request: Request, project: str):
        await threaded(server.caller, request, project, "admin")
        return {"tokens": server.control.list_tokens(project=project)}

    @app.delete("/api/v1/tokens/{token_id}")
    async def revoke_token(token_id: str, request: Request):
        tok = server.control.token(token_id)
        if tok is None:
            raise ApiError(404, "LookupError", "no such token")
        c = await threaded(server.caller, request, tok["project"], "admin" if tok["project"] else None)
        if tok["project"] is None and tok["user_id"] != c.user_id and not c.is_admin:
            raise ApiError(403, "PermissionError", "not your token")
        return {"revoked": server.control.revoke_token(token_id)}

    @app.post("/api/v1/logout")
    async def logout(request: Request):
        c = await threaded(server.caller, request, None, None)
        if c.token_id:
            server.control.revoke_token(c.token_id)
        return {"revoked": bool(c.token_id)}

    from .auth import install_auth
    from .ui import install_ui

    install_auth(app, server)
    install_ui(app, server)
    return app
