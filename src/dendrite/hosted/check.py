"""``dendrite server check``: probe a deployment before people use it.

Checks the database, the bucket and the identity provider, the way the server will use them:
an upload through a presigned URL, the store rejecting wrong bytes and overwrites, and a
presigned download. It writes one small test object and deletes it afterwards.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable

import httpx
import sqlalchemy as sa

from ..store import open_store
from .blobs import Broker
from .settings import ServerSettings

OK, WARN, FAIL = "ok", "warn", "FAIL"


def run_checks(settings: ServerSettings, report: Callable[[str, str, str], None]) -> bool:
    """Run every check, calling ``report(status, name, detail)``; True when nothing failed."""
    failed = False

    def note(status: str, name: str, detail: str = "") -> None:
        nonlocal failed
        failed |= status == FAIL
        report(status, name, detail)

    try:
        engine = sa.create_engine(settings.database_url, pool_pre_ping=True)
        with engine.connect() as conn:
            version = conn.execute(sa.text("SHOW server_version")).scalar()
            limit = int(conn.execute(sa.text("SHOW max_connections")).scalar())
        engine.dispose()
        note(OK, "database", f"Postgres {version}")
        from .authz import PROJECT_OVERFLOW, PROJECT_POOL

        budget = settings.max_open_projects * (PROJECT_POOL + PROJECT_OVERFLOW) + 15
        note(
            OK if budget < limit else WARN,
            "connection budget",
            f"up to {budget} connections per server process; Postgres allows {limit}",
        )
    except Exception as e:  # noqa: BLE001 - report, don't crash
        note(FAIL, "database", str(e))

    try:
        store = open_store(f"{settings.store_url}/_check", settings.home / "check-cache", **settings.store_options)
        _check_store(store, settings, note)
    except Exception as e:  # noqa: BLE001
        note(FAIL, "store", str(e))

    a = settings.auth
    if a.provider == "dev":
        note(WARN, "sign-in", "dev provider: anyone on localhost signs in as any email (fine for trying it out)")
    elif a.provider == "github":
        note(
            OK if a.client_id and a.client_secret else FAIL,
            "sign-in",
            "GitHub OAuth app" if a.client_id else "client_id/client_secret missing",
        )
        note(OK, "callback URL", f"register {settings.base_url}/auth/callback in the GitHub OAuth app")
    else:
        try:
            d = httpx.get(f"{a.issuer.rstrip('/')}/.well-known/openid-configuration", timeout=10).json()
            missing = [k for k in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint") if k not in d]
            note(
                FAIL if missing else OK, "sign-in", f"OIDC issuer {a.issuer}" + (f" lacks {missing}" if missing else "")
            )
        except Exception as e:  # noqa: BLE001
            note(FAIL, "sign-in", f"OIDC discovery failed: {e}")
        note(OK, "callback URL", f"register {settings.base_url}/auth/callback as a redirect URI")
    if not settings.base_url.startswith("https://") and not settings.loopback:
        note(FAIL, "base_url", f"{settings.base_url} isn't HTTPS; session cookies and tokens would travel in clear")
    return not failed


def _check_store(store, settings: ServerSettings, note) -> None:
    probe = f"dendrite server check {uuid.uuid4().hex}".encode()
    h = hashlib.sha256(probe).hexdigest()
    from dataclasses import replace

    # Probe the bucket itself: uploads straight to their content address, as in checksum mode.
    direct = replace(settings, verify_uploads="checksum", store_enforces_sha256=True)
    broker = Broker(store, direct, control=_NoSessions(), project="_check")
    if not (broker.s3 or broker.gcs):
        store._put_object_bytes(probe)
        ok = store.read_bytes(h) == probe
        store.delete(h)
        note(OK if ok else FAIL, "store", f"{store.url}: writable; blobs stream through the server (no presigning)")
        return
    plan = broker.plan(h, len(probe))
    if broker.gcs:
        return _check_gcs(store, broker, plan, probe, h, note)
    try:
        wrong = httpx.put(plan["url"], content=b"not the declared bytes!", headers=plan["headers"], timeout=30)
        enforced = wrong.status_code >= 400
        if not enforced:
            store.delete(h)
        note(
            OK if enforced else WARN,
            "upload checksums",
            "the store rejects bytes that don't match their SHA-256"
            if enforced
            else 'the store accepted mismatched bytes: set verify_uploads = "hash" and store_enforces_sha256 = false',
        )
        plan = broker.plan(h, len(probe))
        right = httpx.put(plan["url"], content=probe, headers=plan["headers"], timeout=30)
        note(OK if right.status_code == 200 else FAIL, "presigned upload", f"HTTP {right.status_code}")
        again = httpx.put(plan["url"], content=probe, headers=plan["headers"], timeout=30)
        note(
            OK if again.status_code == 412 else WARN,
            "no overwrites",
            "conditional writes are enforced"
            if again.status_code == 412
            else f"the store allowed an overwrite (HTTP {again.status_code}); content addressing still keeps bytes identical",
        )
        url = store.signed_url(h, "check.txt", 60)
        got = httpx.get(url, timeout=30) if url else None
        note(
            OK if got is not None and got.content == probe else FAIL,
            "presigned download",
            "clients and browsers must reach the store's endpoint for these URLs",
        )
    finally:
        try:
            store.delete(h)
        except Exception:  # noqa: BLE001 - best effort clean-up
            pass


def _check_gcs(store, broker, plan, probe: bytes, h: str, note) -> None:
    """GCS checks uploads with CRC32C on commit (not a signed SHA-256), so probe what it enforces."""
    try:
        put = httpx.put(plan["url"], content=probe, headers=plan["headers"], timeout=30)
        note(OK if put.status_code == 200 else FAIL, "presigned upload", f"HTTP {put.status_code} {put.text[:120]}")
        info = store.fs.info(store._obj(h), refresh=True)
        note(
            OK if info.get("crc32c") else WARN,
            "upload checksums",
            "GCS records CRC32C; the server compares it with the client's on commit"
            if info.get("crc32c")
            else 'no CRC32C on the stored object: set verify_uploads = "hash"',
        )
        again = httpx.put(plan["url"], content=probe, headers=plan["headers"], timeout=30)
        note(
            OK if again.status_code == 412 else WARN,
            "no overwrites",
            "x-goog-if-generation-match is enforced"
            if again.status_code == 412
            else f"the store allowed an overwrite (HTTP {again.status_code})",
        )
        got = httpx.get(store.signed_url(h, "check.txt", 60), timeout=30)
        note(
            OK if got.content == probe else FAIL,
            "presigned download",
            "clients and browsers must reach storage.googleapis.com for these URLs"
            if got.content == probe
            else f"HTTP {got.status_code}: the server's credentials can't sign URLs (on Cloud Run, grant it "
            "roles/iam.serviceAccountTokenCreator on itself)",
        )
    except Exception as e:  # noqa: BLE001
        note(FAIL, "presigned upload", f"{e} (signing needs a key or roles/iam.serviceAccountTokenCreator)")
    finally:
        try:
            store.delete(h)
        except Exception:  # noqa: BLE001
            pass


class _NoSessions:
    """Upload sessions the check doesn't need to persist."""

    def save_upload(self, *_a, **_k) -> None: ...
