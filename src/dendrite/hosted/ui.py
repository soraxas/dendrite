"""Each project's web UI at ``/p/{project}/``: the ordinary dendrite app, behind sign-in.

The dispatcher signs the request in (session cookie or bearer token), checks membership, sets
the caller for the project's metadata (which enforces roles and ownership on every write), and
forwards to that project's ``create_app``. Storage reclamation, which doesn't go through
metadata, is held to ``admin`` here. Step source is never written from a hosted server.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse

from ..server import create_app
from . import control as ctl
from .authz import CALLER

# UI routes that reclaim storage or protect data from it; they don't pass through metadata checks.
ADMIN_ROUTES = re.compile(
    r"^api/(gc|storage/clean|executions/[^/]+/evict|artifacts/[^/]+/evict|experiments/[^/]+/keep)$"
)


class _Hosted:
    """What the mounted app asks of the server."""

    def __init__(self, server, project: str):
        self.server, self.project = server, project

    def value_preview(self, h: str) -> str | None:
        return self.server.control.value_preview(self.project, h)

    def whoami(self, request: Request) -> dict[str, Any]:
        c = CALLER.get()
        if c is None:
            return {"project": self.project}
        sid = request.cookies.get("dendrite_session")
        s = ctl.session(self.server.control, sid) if sid else None
        return {
            "project": self.project,
            "user": c.label,
            "role": "admin" if c.is_admin else c.role,
            "csrf": s["csrf"] if s else None,
            "projects": [p["name"] for p in self.server.control.projects_for(c.user_id)],
        }


class ProjectsUI:
    """ASGI app mounted at ``/p``: dispatches ``/p/{project}/…`` to that project's app."""

    def __init__(self, server):
        self.server = server

    def app_for(self, project: str):
        p = self.server.project(project)
        if p.ui_app is None:
            p.ui_app = create_app(p.lab, allow_edit=True, source_edit=False, hosted=_Hosted(self.server, project))
        return p.ui_app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return
        root = scope.get("root_path", "")
        route = scope["path"][len(root) :] if scope["path"].startswith(root) else scope["path"]
        project, slash, rest = route.lstrip("/").partition("/")
        request = Request(scope)
        if not project:
            return await RedirectResponse("/", status_code=303)(scope, receive, send)
        if not slash:  # relative asset and API paths need the trailing slash
            return await RedirectResponse(f"{root}/{quote(project)}/", status_code=308)(scope, receive, send)
        from .app import ApiError

        try:
            caller = await run_in_threadpool(self.server.caller, request, project)
            if (
                request.method not in ("GET", "HEAD")
                and ADMIN_ROUTES.match(rest)
                and not (caller.is_admin or caller.role == "admin")
            ):
                raise ApiError(403, "PermissionError", "needs the admin role in this project")
            app = await run_in_threadpool(self.app_for, project)
        except ApiError as e:
            if e.status == 401 and not rest.startswith("api/"):
                target = quote(f"{root}/{project}/")
                return await RedirectResponse(f"/auth/login?next={target}", status_code=303)(scope, receive, send)
            resp = JSONResponse(
                {"detail": e.message, "error": {"type": e.kind, "message": e.message}}, status_code=e.status
            )
            return await resp(scope, receive, send)
        child = dict(scope)
        child["root_path"] = f"{root}/{project}"
        token = CALLER.set(caller)
        try:
            await app(child, receive, send)
        finally:
            CALLER.reset(token)


def install_ui(app: FastAPI, server) -> None:
    app.mount("/p", ProjectsUI(server))
