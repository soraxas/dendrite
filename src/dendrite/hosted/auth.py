"""Signing in: browser sessions through an identity provider, and the CLI's device flow.

The server never stores passwords. Browsers sign in with the configured provider:

* ``github``: GitHub OAuth; the user's verified primary email is their email.
* ``oidc``: any OpenID Connect issuer (Google, Okta, Keycloak, …), with PKCE; an email counts
  only when the issuer marks it verified.
* ``dev``: type an email and you're that user. Only allowed when the server runs on localhost.

The CLI uses a device flow that the server runs itself (not every issuer supports RFC 8628):
``dendrite login`` gets a code, the user confirms it on ``/device`` after signing in, and the
CLI's next poll receives a dendrite token. The token is minted at that moment and stored only
as a hash.
"""

from __future__ import annotations

import base64
import hashlib
import html
import secrets
import threading
import time
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import control as ctl

SESSION_COOKIE = "dendrite_session"
CSRF_HEADER = "x-dendrite-csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
STATE_TTL = 10 * 60


def session_user(server, request: Request) -> tuple[dict, None] | None:
    """The browser session's user. Unsafe methods must also carry the session's CSRF token."""
    sid = request.cookies.get(SESSION_COOKIE)
    if not sid:
        return None
    s = ctl.session(server.control, sid)
    if s is None:
        return None
    if request.method not in SAFE_METHODS and not secrets.compare_digest(
        request.headers.get(CSRF_HEADER, ""), s["csrf"]
    ):
        from .app import ApiError

        raise ApiError(403, "PermissionError", "missing or wrong CSRF token")
    user = server.control.user(s["user_id"])
    return (user, None) if user else None


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{html.escape(title)} · dendrite</title><style>
:root {{ color-scheme: light dark; --fg:#1f2328; --muted:#59636e; --bg:#fff; --card:#f6f8fa; --line:#d1d9e0; --accent:#0969da; }}
@media (prefers-color-scheme: dark) {{ :root {{ --fg:#e6edf3; --muted:#9198a1; --bg:#0d1117; --card:#151b23; --line:#3d444d; --accent:#4493f8; }} }}
body {{ font: 15px/1.5 system-ui, sans-serif; color: var(--fg); background: var(--bg); margin: 0; padding: 48px 16px; }}
main {{ max-width: 440px; margin: 0 auto; background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 24px; }}
h1 {{ font-size: 20px; margin: 0 0 12px; }} p, li {{ color: var(--muted); }} a {{ color: var(--accent); }}
code {{ font: 600 22px/1.2 ui-monospace, monospace; letter-spacing: 2px; color: var(--fg); }}
input {{ font: inherit; width: 100%; box-sizing: border-box; padding: 8px; margin: 4px 0 12px; border: 1px solid var(--line); border-radius: 6px; background: var(--bg); color: var(--fg); }}
button {{ font: inherit; padding: 8px 16px; border-radius: 6px; border: 1px solid var(--accent); background: var(--accent); color: #fff; cursor: pointer; }}
ul {{ padding-left: 20px; }}
</style></head><body><main>{body}</main></body></html>"""
    )


class Providers:
    """The configured identity provider's browser sign-in."""

    def __init__(self, server):
        self.server = server
        self.settings = server.settings
        self.http = httpx.Client(timeout=20, follow_redirects=True)
        self._states: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._oidc: dict[str, Any] | None = None

    @property
    def redirect_uri(self) -> str:
        return f"{self.settings.base_url}/auth/callback"

    def remember(self, **data: Any) -> str:
        state = secrets.token_urlsafe(24)
        now = time.time()
        with self._lock:
            for k in [k for k, v in self._states.items() if v["at"] < now - STATE_TTL]:
                del self._states[k]
            self._states[state] = {**data, "at": now}
        return state

    def recall(self, state: str | None) -> dict[str, Any] | None:
        with self._lock:
            return self._states.pop(state or "", None)

    def discovery(self) -> dict[str, Any]:
        if self._oidc is None:
            issuer = self.settings.auth.issuer.rstrip("/")
            r = self.http.get(f"{issuer}/.well-known/openid-configuration")
            r.raise_for_status()
            self._oidc = r.json()
        return self._oidc

    def authorize_url(self, next_url: str) -> str:
        a = self.settings.auth
        if a.provider == "github":
            state = self.remember(next=next_url)
            q = {
                "client_id": a.client_id,
                "redirect_uri": self.redirect_uri,
                "scope": "read:user user:email",
                "state": state,
            }
            return "https://github.com/login/oauth/authorize?" + urlencode(q)
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = self.remember(next=next_url, verifier=verifier, nonce=secrets.token_urlsafe(16))
        q = {
            "client_id": a.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(a.scopes),
            "state": state,
            "nonce": self._states[state]["nonce"],
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return self.discovery()["authorization_endpoint"] + "?" + urlencode(q)

    def identity(self, code: str, remembered: dict[str, Any]) -> tuple[str, str, str | None, str | None]:
        """``(issuer, subject, verified email, name)`` for an authorization code."""
        a = self.settings.auth
        if a.provider == "github":
            r = self.http.post(
                "https://github.com/login/oauth/access_token",
                data={
                    "client_id": a.client_id,
                    "client_secret": a.client_secret,
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                },
                headers={"Accept": "application/json"},
            )
            r.raise_for_status()
            token = r.json()["access_token"]
            auth = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
            me = self.http.get("https://api.github.com/user", headers=auth).json()
            emails = self.http.get("https://api.github.com/user/emails", headers=auth).json()
            email = next((e["email"] for e in emails if e.get("primary") and e.get("verified")), None)
            return "github", str(me["id"]), email, me.get("name") or me.get("login")
        d = self.discovery()
        r = self.http.post(
            d["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "client_id": a.client_id,
                "client_secret": a.client_secret or "",
                "code_verifier": remembered["verifier"],
            },
            headers={"Accept": "application/json"},
        )
        r.raise_for_status()
        info = self.http.get(
            d["userinfo_endpoint"], headers={"Authorization": f"Bearer {r.json()['access_token']}"}
        ).json()
        email = info.get("email") if info.get("email_verified") else None
        return d.get("issuer", a.issuer), str(info["sub"]), email, info.get("name") or info.get("preferred_username")


def install_auth(app: FastAPI, server) -> None:
    providers = Providers(server)
    app.state.providers = providers
    settings = server.settings

    def signed_in(request: Request) -> dict | None:
        try:
            found = session_user(server, request)
        except Exception:  # noqa: BLE001 - a bad CSRF token on a GET page just means "not signed in"
            return None
        return found[0] if found else None

    def start_session(user: dict, next_url: str) -> RedirectResponse:
        ctl.join_domain_projects(server.control, user, settings.allowed_domains)
        sid, _ = ctl.create_session(server.control, user["id"])
        resp = RedirectResponse(_local(next_url), status_code=303)
        resp.set_cookie(
            SESSION_COOKIE, sid, max_age=ctl.SESSION_TTL, httponly=True, secure=settings.secure_cookies, samesite="lax"
        )
        return resp

    def admin_email(email: str | None) -> bool:
        return bool(email) and email.lower() in {a.lower() for a in settings.admins}

    @app.get("/auth/login")
    def login(next: str = "/"):
        if settings.auth.provider == "dev":
            return _page(
                "Sign in",
                f"""<h1>Sign in (development)</h1>
<p>This server uses the <b>dev</b> provider: anyone who can reach localhost signs in as the email they type.</p>
<form method="post" action="/auth/dev"><input type="hidden" name="next" value="{html.escape(_local(next))}">
<label>Email<input name="email" type="email" required autofocus></label>
<label>Name<input name="name"></label><button>Sign in</button></form>""",
            )
        return RedirectResponse(providers.authorize_url(_local(next)), status_code=303)

    @app.post("/auth/dev")
    def dev_login(email: str = Form(...), name: str = Form(""), next: str = Form("/")):
        if settings.auth.provider != "dev":
            return JSONResponse(
                {"error": {"type": "PermissionError", "message": "dev sign-in is off"}}, status_code=403
            )
        user = server.control.upsert_user("dev", email.lower(), email, name or None, admin=admin_email(email))
        return start_session(user, next)

    @app.get("/auth/callback")
    def callback(code: str = "", state: str = "", error: str = ""):
        remembered = providers.recall(state)
        if error or not code or remembered is None:
            return _page(
                "Sign-in failed",
                f"<h1>Sign-in failed</h1><p>{html.escape(error or 'The sign-in expired; try again.')}</p><p><a href='/auth/login'>Sign in</a></p>",
            )
        issuer, subject, email, name = providers.identity(code, remembered)
        user = server.control.upsert_user(issuer, subject, email, name, admin=admin_email(email))
        return start_session(user, remembered["next"])

    @app.post("/auth/logout")
    def logout(request: Request):
        sid = request.cookies.get(SESSION_COOKIE)
        if sid:
            session_user(server, request)  # CSRF check
            ctl.end_session(server.control, sid)
        resp = JSONResponse({"signed_out": True})
        resp.delete_cookie(SESSION_COOKIE)
        return resp

    @app.get("/api/v1/session/csrf")
    def csrf(request: Request):
        sid = request.cookies.get(SESSION_COOKIE)
        s = ctl.session(server.control, sid) if sid else None
        if s is None:
            return JSONResponse({"error": {"type": "NotLoggedIn", "message": "no session"}}, status_code=401)
        return {"csrf": s["csrf"]}

    # -- device flow -------------------------------------------------------------
    @app.post("/api/v1/auth/device")
    async def device_start(request: Request):
        body = await request.json() if await request.body() else {}
        d = ctl.start_device_login(server.control, (body or {}).get("client_name"))
        return {
            **d,
            "verification_uri": f"{settings.base_url}/device",
            "verification_uri_complete": f"{settings.base_url}/device?code={quote(d['user_code'])}",
            "interval": 2,
        }

    @app.post("/api/v1/auth/token")
    async def device_poll(request: Request):
        body = await request.json()
        res = ctl.poll_device_login(server.control, str(body.get("device_code", "")))
        return JSONResponse(res, status_code=400 if res["status"] == "expired" else 200)

    @app.get("/device")
    def device_page(request: Request, code: str = ""):
        user = signed_in(request)
        if user is None:
            return RedirectResponse(f"/auth/login?next={quote('/device?code=' + code)}", status_code=303)
        login = ctl.device_login(server.control, code) if code else None
        if login is None:
            return _page(
                "Confirm sign-in",
                f"""<h1>Connect a device</h1><p>Enter the code shown by <tt>dendrite login</tt>.</p>
<form method="get" action="/device"><input name="code" placeholder="ABCD-EFGH" value="{html.escape(code)}" required autofocus>
<button>Continue</button></form>{"<p>That code is unknown or expired.</p>" if code else ""}""",
            )
        s = ctl.session(server.control, request.cookies[SESSION_COOKIE])
        who = html.escape(user["email"] or user["name"] or user["id"])
        client = html.escape(login["client_name"] or "dendrite CLI")
        return _page(
            "Confirm sign-in",
            f"""<h1>Sign in {client}?</h1>
<p>Check that this code matches the one in your terminal:</p><p><code>{html.escape(login["user_code"])}</code></p>
<p>It will act as <b>{who}</b> on <b>{html.escape(settings.base_url)}</b>, with your access to each project.</p>
<form method="post" action="/device"><input type="hidden" name="code" value="{html.escape(login["user_code"])}">
<input type="hidden" name="csrf" value="{html.escape(s["csrf"])}"><button>Approve</button></form>""",
        )

    @app.post("/device")
    def device_approve(request: Request, code: str = Form(...), csrf: str = Form(...)):
        sid = request.cookies.get(SESSION_COOKIE)
        s = ctl.session(server.control, sid) if sid else None
        if s is None or not secrets.compare_digest(csrf, s["csrf"]):
            return _page("Not approved", "<h1>Not approved</h1><p>Your session expired; open the link again.</p>")
        if not ctl.approve_device_login(server.control, code, s["user_id"]):
            return _page("Not approved", "<h1>Not approved</h1><p>That code is unknown, expired or already used.</p>")
        return _page("Approved", "<h1>Approved</h1><p>You can close this tab and return to your terminal.</p>")

    # -- landing page ------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        user = signed_in(request)
        if user is None:
            return _page(
                "dendrite",
                "<h1>dendrite</h1><p>Experiment lineage and shared results for your team.</p><p><a href='/auth/login'>Sign in</a></p>",
            )
        projects = server.control.projects_for(user["id"])
        items = "".join(
            f"<li><a href='/p/{quote(p['name'])}/'>{html.escape(p['name'])}</a> · {p['role']}</li>" for p in projects
        )
        who = html.escape(user["email"] or user["name"] or user["id"])
        return _page(
            "Projects",
            f"<h1>Projects</h1><p>Signed in as {who}.</p>"
            + (f"<ul>{items}</ul>" if items else "<p>You aren't in any project yet; ask an admin to add you.</p>"),
        )


def _local(next_url: str) -> str:
    """Only same-site redirect targets (no open redirects)."""
    return next_url if next_url.startswith("/") and not next_url.startswith("//") else "/"
