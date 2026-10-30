"""The hosted server's control plane: users, projects, memberships, tokens and attribution.

These tables live in their own Postgres schema (``dendrite_control``). Each project's lineage
lives in a schema of its own holding the unchanged dendrite tables (``db.py``), so the core
schema never changes for hosting, and an existing direct-mode schema can be adopted as a
project as it is.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

SCHEMA = "dendrite_control"
ROLES = ("viewer", "runner", "admin")
TOKEN_PREFIX = "dtk_"
TOKEN_TTL = 90 * 24 * 3600  # user tokens; renewed on use
PROJECT_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")

meta = sa.MetaData(schema=SCHEMA)

users = sa.Table(
    "users",
    meta,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("issuer", sa.String, nullable=False),  # "github", an OIDC issuer URL, "dev" or "service"
    sa.Column("subject", sa.String, nullable=False),
    sa.Column("email", sa.String),
    sa.Column("name", sa.String),
    sa.Column("is_admin", sa.Boolean, nullable=False, default=False),  # admin in every project
    sa.Column("created_at", sa.Float, nullable=False),
    sa.UniqueConstraint("issuer", "subject"),
)

projects = sa.Table(
    "projects",
    meta,
    sa.Column("name", sa.String, primary_key=True),
    sa.Column("db_schema", sa.String, nullable=False, unique=True),
    sa.Column("store_url", sa.String, nullable=False),
    sa.Column("created_at", sa.Float, nullable=False),
)

memberships = sa.Table(
    "memberships",
    meta,
    sa.Column("project", sa.String, sa.ForeignKey(projects.c.name, ondelete="CASCADE"), primary_key=True),
    sa.Column("user_id", sa.String, sa.ForeignKey(users.c.id, ondelete="CASCADE"), primary_key=True),
    sa.Column("role", sa.String, nullable=False),
)

tokens = sa.Table(
    "tokens",
    meta,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("digest", sa.String, nullable=False, unique=True),  # sha256 of the secret; never the secret
    sa.Column("user_id", sa.String, sa.ForeignKey(users.c.id, ondelete="CASCADE"), nullable=False),
    sa.Column("name", sa.String),
    # A service token is limited to one project and role; a user token acts with the user's memberships.
    sa.Column("project", sa.String),
    sa.Column("role", sa.String),
    sa.Column("created_at", sa.Float, nullable=False),
    sa.Column("expires_at", sa.Float),
    sa.Column("last_used_at", sa.Float),
    sa.Column("revoked_at", sa.Float),
)

device_logins = sa.Table(
    "device_logins",
    meta,
    sa.Column("device_code", sa.String, primary_key=True),  # stored hashed
    sa.Column("user_code", sa.String, nullable=False, unique=True),
    sa.Column("created_at", sa.Float, nullable=False),
    sa.Column("expires_at", sa.Float, nullable=False),
    sa.Column("user_id", sa.String),  # set once the user confirms the code; the token is minted on the next poll
    sa.Column("client_name", sa.String),
)

sessions = sa.Table(
    "sessions",
    meta,
    sa.Column("id", sa.String, primary_key=True),  # stored hashed
    sa.Column("user_id", sa.String, sa.ForeignKey(users.c.id, ondelete="CASCADE"), nullable=False),
    sa.Column("csrf", sa.String, nullable=False),
    sa.Column("created_at", sa.Float, nullable=False),
    sa.Column("expires_at", sa.Float, nullable=False),
)

# Who created an experiment or an execution. Side tables keep the core schema unchanged.
attribution = sa.Table(
    "attribution",
    meta,
    sa.Column("project", sa.String, primary_key=True),
    sa.Column("kind", sa.String, primary_key=True),  # "experiment" | "execution"
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("user_id", sa.String, nullable=False),
    sa.Column("label", sa.String, nullable=False),  # display name at the time (user or token name)
)

# Producer-recorded text of value artifacts: the server never unpickles.
value_previews = sa.Table(
    "value_previews",
    meta,
    sa.Column("project", sa.String, primary_key=True),
    sa.Column("hash", sa.String, primary_key=True),
    sa.Column("text", sa.Text, nullable=False),
)

# Every presigned URL handed out, for auditing reads.
signings = sa.Table(
    "signings",
    meta,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("project", sa.String, nullable=False),
    sa.Column("user_id", sa.String, nullable=False),
    sa.Column("method", sa.String, nullable=False),
    sa.Column("hash", sa.String, nullable=False),
    sa.Column("at", sa.Float, nullable=False),
)


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def new_secret(prefix: str = TOKEN_PREFIX) -> str:
    return prefix + secrets.token_urlsafe(32)


def role_at_least(role: str | None, needed: str) -> bool:
    return role is not None and ROLES.index(role) >= ROLES.index(needed)


@dataclass(frozen=True)
class Caller:
    """Who is making a request, and what they may do in the project it addresses."""

    user_id: str
    label: str  # name shown in attribution: the user's name/email, or the service token's name
    role: str | None  # in the addressed project; None when not a member
    is_admin: bool = False
    token_id: str | None = None

    @property
    def tag(self) -> str:
        """Prefix that binds lease owners to this caller."""
        return f"{self.user_id}|"


AUTH_CACHE_SECONDS = 30  # how long a token or role lookup is reused (and revocation can lag)


class Control:
    def __init__(self, engine: sa.Engine):
        self.engine = engine
        self._auth: dict[str, tuple[float, dict, dict]] = {}
        self._roles: dict[tuple[str, str], tuple[float, str | None]] = {}
        with engine.begin() as conn:
            conn.execute(sa.text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        meta.create_all(engine)

    # -- users -------------------------------------------------------------------
    def invite(self, email: str, admin: bool = False) -> dict:
        """A placeholder user for someone who hasn't signed in yet; their first sign-in with this
        (verified) email claims it, with its memberships and admin flag."""
        found = self.user_by_email(email)
        if found is not None:
            if admin and not found["is_admin"]:
                with self.engine.begin() as conn:
                    conn.execute(users.update().where(users.c.id == found["id"]).values(is_admin=True))
                found["is_admin"] = True
            return found
        return self.upsert_user("invite", email.lower(), email, None, admin=admin)

    def upsert_user(self, issuer: str, subject: str, email: str | None, name: str | None, admin: bool = False) -> dict:
        now = time.time()
        with self.engine.begin() as conn:
            row = conn.execute(sa.select(users).where(users.c.issuer == issuer, users.c.subject == subject)).first()
            if row is None and email and issuer != "invite":
                row = conn.execute(
                    sa.select(users).where(users.c.issuer == "invite", sa.func.lower(users.c.email) == email.lower())
                ).first()
                if row is not None:  # claim the invitation
                    conn.execute(users.update().where(users.c.id == row.id).values(issuer=issuer, subject=subject))
            if row is None:
                uid = uuid.uuid4().hex[:16]
                conn.execute(
                    users.insert().values(
                        id=uid, issuer=issuer, subject=subject, email=email, name=name, is_admin=admin, created_at=now
                    )
                )
            else:
                uid = row.id
                values: dict[str, Any] = {"email": email or row.email, "name": name or row.name}
                if admin:
                    values["is_admin"] = True
                conn.execute(users.update().where(users.c.id == uid).values(**values))
            return dict(conn.execute(sa.select(users).where(users.c.id == uid)).one()._mapping)

    def user(self, user_id: str) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(sa.select(users).where(users.c.id == user_id)).first()
        return dict(row._mapping) if row else None

    def user_by_email(self, email: str) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(sa.select(users).where(sa.func.lower(users.c.email) == email.lower())).first()
        return dict(row._mapping) if row else None

    # -- projects ----------------------------------------------------------------
    def create_project(self, name: str, store_url: str, db_schema: str | None = None) -> dict:
        if not PROJECT_NAME.match(name):
            raise ValueError("project names are lower-case letters, digits, '-' and '_', starting with a letter")
        schema = db_schema or "p_" + name.replace("-", "_")
        with self.engine.begin() as conn:
            if conn.execute(sa.select(projects).where(projects.c.name == name)).first():
                raise ValueError(f"project {name!r} exists")
            conn.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
            conn.execute(
                projects.insert().values(name=name, db_schema=schema, store_url=store_url, created_at=time.time())
            )
        return self.project(name)

    def project(self, name: str) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(sa.select(projects).where(projects.c.name == name)).first()
        return dict(row._mapping) if row else None

    def projects_for(self, user_id: str) -> list[dict]:
        """Projects a user can open, with their role in each."""
        u = self.user(user_id)
        with self.engine.connect() as conn:
            if u and u["is_admin"]:
                rows = conn.execute(sa.select(projects.c.name).order_by(projects.c.name)).all()
                return [{"name": r.name, "role": "admin"} for r in rows]
            rows = conn.execute(
                sa.select(memberships.c.project, memberships.c.role)
                .where(memberships.c.user_id == user_id)
                .order_by(memberships.c.project)
            ).all()
        return [{"name": r.project, "role": r.role} for r in rows]

    def set_member(self, project: str, user_id: str, role: str | None) -> None:
        if role is not None and role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        self._roles.pop((user_id, project), None)
        with self.engine.begin() as conn:
            conn.execute(memberships.delete().where(memberships.c.project == project, memberships.c.user_id == user_id))
            if role:
                conn.execute(memberships.insert().values(project=project, user_id=user_id, role=role))

    def members(self, project: str) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                sa.select(users.c.id, users.c.email, users.c.name, memberships.c.role)
                .join(memberships, memberships.c.user_id == users.c.id)
                .where(memberships.c.project == project)
                .order_by(users.c.email)
            ).all()
        return [dict(r._mapping) for r in rows]

    def role(self, user: dict, project: str) -> str | None:
        if user["is_admin"]:
            return "admin"
        hit = self._roles.get((user["id"], project))
        if hit and hit[0] > time.time() - AUTH_CACHE_SECONDS:
            return hit[1]
        with self.engine.connect() as conn:
            row = conn.execute(
                sa.select(memberships.c.role).where(
                    memberships.c.project == project, memberships.c.user_id == user["id"]
                )
            ).first()
        self._roles[(user["id"], project)] = (time.time(), row.role if row else None)
        return row.role if row else None

    # -- tokens ------------------------------------------------------------------
    def create_token(
        self,
        user_id: str,
        *,
        name: str | None = None,
        project: str | None = None,
        role: str | None = None,
        ttl: float | None = TOKEN_TTL,
    ) -> tuple[str, dict]:
        """Mint a token; returns the secret (shown once) and its record."""
        if (project is None) != (role is None):
            raise ValueError("a service token needs both a project and a role")
        if role is not None and role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        secret = new_secret()
        now = time.time()
        rec = {
            "id": uuid.uuid4().hex[:16],
            "digest": digest(secret),
            "user_id": user_id,
            "name": name,
            "project": project,
            "role": role,
            "created_at": now,
            "expires_at": now + ttl if ttl else None,
        }
        with self.engine.begin() as conn:
            conn.execute(tokens.insert().values(**rec))
        return secret, {k: v for k, v in rec.items() if k != "digest"}

    def authenticate(self, secret: str) -> tuple[dict, dict] | None:
        """``(user, token)`` for a live token, renewing a user token's expiry; else None.

        A hit is reused for ``AUTH_CACHE_SECONDS`` (revoking here clears it at once), so a
        busy client doesn't cost a database write per request."""
        now = time.time()
        key = digest(secret)
        hit = self._auth.get(key)
        if hit and hit[0] > now - AUTH_CACHE_SECONDS:
            return hit[1], hit[2]
        with self.engine.begin() as conn:
            tok = conn.execute(sa.select(tokens).where(tokens.c.digest == digest(secret))).first()
            if tok is None or tok.revoked_at or (tok.expires_at and tok.expires_at < now):
                return None
            values: dict[str, Any] = {"last_used_at": now}
            if tok.project is None and tok.expires_at:  # sliding expiry for user tokens
                values["expires_at"] = now + TOKEN_TTL
            conn.execute(tokens.update().where(tokens.c.id == tok.id).values(**values))
            user = conn.execute(sa.select(users).where(users.c.id == tok.user_id)).one()
        found = dict(user._mapping), dict(tok._mapping)
        self._auth[key] = (now, *found)
        return found

    def list_tokens(self, *, project: str | None = None, user_id: str | None = None) -> list[dict]:
        q = sa.select(tokens).where(tokens.c.revoked_at.is_(None))
        if project is not None:
            q = q.where(tokens.c.project == project)
        if user_id is not None:
            q = q.where(tokens.c.user_id == user_id)
        with self.engine.connect() as conn:
            return [{k: v for k, v in r._mapping.items() if k != "digest"} for r in conn.execute(q).all()]

    def token(self, token_id: str) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(sa.select(tokens).where(tokens.c.id == token_id)).first()
        return {k: v for k, v in row._mapping.items() if k != "digest"} if row else None

    def revoke_token(self, token_id: str) -> bool:
        self._auth = {k: v for k, v in self._auth.items() if v[2]["id"] != token_id}
        with self.engine.begin() as conn:
            res = conn.execute(
                tokens.update()
                .where(tokens.c.id == token_id, tokens.c.revoked_at.is_(None))
                .values(revoked_at=time.time())
            )
        return res.rowcount == 1

    def caller(self, user: dict, token: dict | None, project: str | None) -> Caller:
        """The caller's identity and role in ``project`` (a service token is held to its scope)."""
        if token is not None and token["project"] is not None:
            role = token["role"] if project == token["project"] else None
            label = token["name"] or user["name"] or user["email"] or user["id"]
            return Caller(user["id"], label, role, False, token["id"])
        role = self.role(user, project) if project else None
        label = user["name"] or user["email"] or user["id"]
        return Caller(user["id"], label, role, bool(user["is_admin"]), token["id"] if token else None)

    def service_user(self, name: str) -> dict:
        """A non-human identity for service tokens (CI)."""
        return self.upsert_user("service", name, None, name)

    # -- attribution and value previews -------------------------------------------
    def attribute(self, project: str, kind: str, ids: Iterable[str], caller: Caller) -> None:
        with self.engine.begin() as conn:
            for i in ids:
                conn.execute(
                    attribution.delete().where(
                        attribution.c.project == project, attribution.c.kind == kind, attribution.c.id == i
                    )
                )
                conn.execute(
                    attribution.insert().values(
                        project=project, kind=kind, id=i, user_id=caller.user_id, label=caller.label
                    )
                )

    def attributed(self, project: str, kind: str, ids: Iterable[str]) -> dict[str, dict]:
        ids = list(ids)
        if not ids:
            return {}
        with self.engine.connect() as conn:
            rows = conn.execute(
                sa.select(attribution).where(
                    attribution.c.project == project, attribution.c.kind == kind, attribution.c.id.in_(ids)
                )
            ).all()
        return {r.id: {"user_id": r.user_id, "label": r.label} for r in rows}

    def save_value_previews(self, project: str, previews: dict[str, str]) -> None:
        """Record previews for values that have none; an existing preview is never replaced."""
        from sqlalchemy.exc import IntegrityError

        for h, text in previews.items():
            try:
                with self.engine.begin() as conn:
                    conn.execute(value_previews.insert().values(project=project, hash=h, text=text))
            except IntegrityError:
                pass

    def value_preview(self, project: str, h: str) -> str | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                sa.select(value_previews.c.text).where(value_previews.c.project == project, value_previews.c.hash == h)
            ).first()
        return row.text if row else None

    def log_signing(self, project: str, caller: Caller, method: str, hashes: Iterable[str]) -> None:
        now = time.time()
        rows = [{"project": project, "user_id": caller.user_id, "method": method, "hash": h, "at": now} for h in hashes]
        if rows:
            with self.engine.begin() as conn:
                conn.execute(signings.insert(), rows)


# -- device logins and browser sessions ----------------------------------------------
DEVICE_TTL = 10 * 60
SESSION_TTL = 14 * 24 * 3600
_CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"  # no vowels (no accidental words), nothing easily misread


def _user_code() -> str:
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def start_device_login(control: Control, client_name: str | None) -> dict:
    device_code = secrets.token_urlsafe(32)
    now = time.time()
    with control.engine.begin() as conn:
        conn.execute(device_logins.delete().where(device_logins.c.expires_at < now))
        while True:
            code = _user_code()
            if not conn.execute(sa.select(device_logins).where(device_logins.c.user_code == code)).first():
                break
        conn.execute(
            device_logins.insert().values(
                device_code=digest(device_code),
                user_code=code,
                created_at=now,
                expires_at=now + DEVICE_TTL,
                client_name=client_name,
            )
        )
    return {"device_code": device_code, "user_code": code, "expires_in": DEVICE_TTL}


def device_login(control: Control, user_code: str) -> dict | None:
    with control.engine.connect() as conn:
        row = conn.execute(
            sa.select(device_logins).where(device_logins.c.user_code == user_code.strip().upper())
        ).first()
    return dict(row._mapping) if row and row.expires_at > time.time() else None


def approve_device_login(control: Control, user_code: str, user_id: str) -> bool:
    with control.engine.begin() as conn:
        res = conn.execute(
            device_logins.update()
            .where(
                device_logins.c.user_code == user_code.strip().upper(),
                device_logins.c.expires_at > time.time(),
                device_logins.c.user_id.is_(None),
            )
            .values(user_id=user_id)
        )
    return res.rowcount == 1


def poll_device_login(control: Control, device_code: str) -> dict:
    """``pending``, ``expired``, or ``ok`` with a freshly minted user token (handed over once)."""
    with control.engine.begin() as conn:
        row = conn.execute(sa.select(device_logins).where(device_logins.c.device_code == digest(device_code))).first()
        if row is None or row.expires_at < time.time():
            return {"status": "expired"}
        if row.user_id is None:
            return {"status": "pending"}
        conn.execute(device_logins.delete().where(device_logins.c.device_code == row.device_code))
    secret, rec = control.create_token(row.user_id, name=row.client_name or "cli")
    return {"status": "ok", "token": secret, "token_id": rec["id"]}


def create_session(control: Control, user_id: str) -> tuple[str, str]:
    """A browser session: returns the cookie secret and its CSRF token."""
    sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    now = time.time()
    with control.engine.begin() as conn:
        conn.execute(sessions.delete().where(sessions.c.expires_at < now))
        conn.execute(
            sessions.insert().values(
                id=digest(sid), user_id=user_id, csrf=csrf, created_at=now, expires_at=now + SESSION_TTL
            )
        )
    return sid, csrf


def session(control: Control, sid: str) -> dict | None:
    with control.engine.connect() as conn:
        row = conn.execute(sa.select(sessions).where(sessions.c.id == digest(sid))).first()
    return dict(row._mapping) if row and row.expires_at > time.time() else None


def end_session(control: Control, sid: str) -> None:
    with control.engine.begin() as conn:
        conn.execute(sessions.delete().where(sessions.c.id == digest(sid)))


def join_domain_projects(control: Control, user: dict, domains: Iterable[str]) -> None:
    """Sign-ins from an allowed email domain join every project (as viewers) they aren't in."""
    email = (user.get("email") or "").lower()
    if not email or email.rsplit("@", 1)[-1] not in {d.lower() for d in domains}:
        return
    with control.engine.begin() as conn:
        names = [r.name for r in conn.execute(sa.select(projects.c.name)).all()]
        have = {
            r.project
            for r in conn.execute(sa.select(memberships.c.project).where(memberships.c.user_id == user["id"])).all()
        }
        for n in names:
            if n not in have:
                conn.execute(memberships.insert().values(project=n, user_id=user["id"], role="viewer"))
