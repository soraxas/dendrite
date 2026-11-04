"""The hosted-mode client: metadata and blobs through a dendrite server.

``HttpMetadata`` stands in for ``db.Metadata``. Each allowlisted method becomes a call to the
server, which runs it on the project's database as the signed-in user. ``ServerStore`` is an
``ArtifactStore`` whose bytes go straight to and from the bucket through presigned URLs (or
through the server, for stores that can't presign). Everything above them (tasks, signatures,
cache verification, forks, pins, plans) is unchanged.

Credentials come from ``DENDRITE_TOKEN``, then ``~/.config/dendrite/credentials.toml``. With
neither, the first server call signs in: interactively (a terminal or a notebook) through the
device flow, otherwise it raises ``NotLoggedIn``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, BinaryIO

import httpx

from . import __version__
from .hosted import wire
from .store import ArtifactStore

BATCH = 1000
RETRIES = 4
UPLOADS_IN_PARALLEL = 8
KEEPALIVE = 2.0  # seconds an idle client connection is kept; must stay below the server's
INLINE_BYTES = 256 * 1024  # blobs up to this size go up inside the request that checks for them


class NotLoggedIn(RuntimeError):
    """No usable credentials for the configured server."""


class ServerError(RuntimeError):
    pass


class ServerConflict(ServerError):
    pass


class UploadFailed(ServerError):
    pass


# -- credentials -------------------------------------------------------------------
def credentials_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "dendrite" / "credentials.toml"


def load_credentials() -> dict[str, dict[str, str]]:
    import tomllib

    path = credentials_path()
    if not path.is_file():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def save_token(server: str, token: str | None, **extra: str) -> None:
    """Store (or with ``None`` remove) the token for ``server``, readable by this user only."""
    creds = load_credentials()
    if token is None:
        creds.pop(server, None)
    else:
        creds[server] = {"token": token, **extra}
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("# dendrite sign-ins, one table per server. Keep private.\n")
        for srv, entry in sorted(creds.items()):
            f.write(f"\n[{json.dumps(srv)}]\n")
            for k, v in entry.items():
                f.write(f"{k} = {json.dumps(str(v))}\n")
    os.replace(tmp, path)


def interactive() -> bool:
    if "ipykernel" in sys.modules:  # Jupyter
        return True
    try:
        return sys.stdin is not None and sys.stdin.isatty() and sys.stderr.isatty()
    except (ValueError, AttributeError):
        return False


def device_login(server: str, *, http: httpx.Client | None = None, project: str | None = None, out=None) -> str:
    """Sign in through the server's device flow; returns the new token (also stored)."""
    out = out or sys.stderr
    client = http or httpx.Client(base_url=server, timeout=30)
    r = client.post("/api/v1/auth/device", json={"client_name": f"dendrite CLI on {platform.node() or 'this machine'}"})
    r.raise_for_status()
    d = r.json()
    where = f"{server}" + (f" (project {project})" if project else "")
    print(f"\nSign in to {where}", file=out)
    print(f"  open    {d['verification_uri_complete']}", file=out)
    print(f"  code    {d['user_code']}\n", file=out)
    try:
        webbrowser.open(d["verification_uri_complete"])
    except Exception:  # noqa: BLE001 - no browser is fine, the URL is printed
        pass
    deadline = time.time() + d["expires_in"]
    while time.time() < deadline:
        time.sleep(d.get("interval", 2))
        p = client.post("/api/v1/auth/token", json={"device_code": d["device_code"]})
        res = p.json()
        if res["status"] == "ok":
            save_token(server, res["token"], token_id=res["token_id"])
            print("Signed in.", file=out)
            return res["token"]
        if res["status"] == "expired":
            break
    raise NotLoggedIn("the sign-in code expired before it was approved; run `dendrite login` again")


# -- the HTTP client ---------------------------------------------------------------
_ERRORS: dict[str, type[Exception]] = {
    "ValueError": ValueError,
    "KeyError": KeyError,
    "LookupError": LookupError,
    "TypeError": TypeError,
    "PermissionError": PermissionError,
    "Conflict": ServerConflict,
    "UploadError": UploadFailed,
}


def _raise(r: httpx.Response) -> None:
    try:
        err = r.json().get("error") or {}
    except ValueError:
        err = {}
    kind, msg = err.get("type", ""), err.get("message") or r.text[:500]
    if r.status_code == 401:
        raise NotLoggedIn(msg)
    if r.status_code == 426:
        raise ServerError(f"{msg}; upgrade dendrite (this is {__version__})")
    if kind in ("StorageBusy", "EvictRefused"):
        from . import storage

        raise getattr(storage, kind)(msg)
    if kind == "NotADraft":
        from .experiment import NotADraft

        raise NotADraft(msg)
    if kind == "IntegrityError":
        from sqlalchemy.exc import IntegrityError

        raise IntegrityError(msg, None, Exception(msg))
    raise _ERRORS.get(kind, ServerError)(msg)


class ServerClient:
    """One signed-in connection to a project on a dendrite server."""

    def __init__(
        self,
        server: str,
        project: str,
        *,
        token: str | None = None,
        http: httpx.Client | None = None,
        blob_http: httpx.Client | None = None,
    ):
        self.server, self.project = server.rstrip("/"), project
        headers = {"X-Dendrite-Version": __version__}
        # Drop idle connections well before the server does (its keep-alive is 30 s): reusing one
        # just as the server closes it loses the request, which then waits out the read timeout.
        self.http = http or httpx.Client(
            base_url=self.server,
            timeout=httpx.Timeout(120, connect=15),
            limits=httpx.Limits(max_connections=64, keepalive_expiry=KEEPALIVE),
        )
        self.http.headers.update(headers)
        # Presigned URLs carry their own authorization; never send the dendrite token with them.
        self.blob_http = blob_http or httpx.Client(
            timeout=httpx.Timeout(600, connect=15),
            follow_redirects=True,
            limits=httpx.Limits(keepalive_expiry=KEEPALIVE),
        )
        self._token = token
        self._lock = threading.Lock()

    # -- auth --------------------------------------------------------------------
    def token(self) -> str:
        with self._lock:
            if self._token is None:
                self._token = os.environ.get("DENDRITE_TOKEN") or load_credentials().get(self.server, {}).get("token")
            if self._token is None:
                if not interactive():
                    raise NotLoggedIn(
                        f"not signed in to {self.server}: run `dendrite login` (or set DENDRITE_TOKEN in CI)"
                    )
                self._token = device_login(self.server, http=self.http, project=self.project)
            return self._token

    def _relogin(self) -> bool:
        """After a 401: sign in again if a person is there to do it (never for DENDRITE_TOKEN)."""
        if os.environ.get("DENDRITE_TOKEN") or not interactive():
            return False
        with self._lock:
            save_token(self.server, None)
            self._token = device_login(self.server, http=self.http, project=self.project)
        return True

    # -- requests ----------------------------------------------------------------
    def request(
        self, method: str, path: str, body: Any = None, *, idempotent: bool = False, content: Any = None
    ) -> Any:
        """A JSON API call, retried on connection errors and 502-504 (writes carry an idempotency key)."""
        headers = {"Content-Type": "application/json"}
        if idempotent:  # the server remembers the reply, so a retried write takes effect once
            headers["Idempotency-Key"] = uuid.uuid4().hex
        data = wire.dumps(body) if body is not None else None
        relogged = False
        for attempt in range(RETRIES):
            headers["Authorization"] = f"Bearer {self.token()}"
            try:
                r = self.http.request(method, path, content=content if content is not None else data, headers=headers)
            except httpx.TransportError:  # incl. a keep-alive connection the server had closed
                if attempt == RETRIES - 1 or content is not None:
                    raise
                time.sleep(0.2 * 2**attempt)
                continue
            if r.status_code in (502, 503, 504) and attempt < RETRIES - 1 and content is None:
                time.sleep(0.2 * 2**attempt)
                continue
            if r.status_code == 401 and not relogged and self._relogin():
                relogged = True
                continue
            if r.status_code >= 400:
                _raise(r)
            return wire.loads(r.content) if r.content else None
        raise ServerError(f"{method} {path} failed after {RETRIES} attempts")

    def api(self, path: str) -> str:
        return f"/api/v1/projects/{self.project}/{path}"

    def rpc(self, method: str, args: tuple, kwargs: dict) -> Any:
        body = {"args": list(args), "kwargs": kwargs}
        return self.request("POST", self.api(f"rpc/{method}"), body, idempotent=method in wire.WRITES)["result"]

    def rpc_batch(self, calls: list[tuple[str, tuple, dict]]) -> list:
        body = {"calls": [{"method": m, "args": list(a), "kwargs": k} for m, a, k in calls]}
        return self.request("POST", self.api("rpc"), body)["results"]

    def close(self) -> None:
        self.http.close()
        self.blob_http.close()


# -- metadata ------------------------------------------------------------------------
PROGRESS_FLUSH = 2.0  # seconds between batched progress updates


class HttpMetadata:
    """``Metadata`` over a dendrite server: same methods, same results, same errors."""

    url = "remote"

    def __init__(self, client: ServerClient):
        self.client = client
        self._progress: dict[str, tuple] = {}
        self._queued: list[tuple[tuple, dict]] = []
        self._plock = threading.Lock()
        self._flock = threading.RLock()
        self._flusher: threading.Thread | None = None
        self._stop = threading.Event()

    def __getattr__(self, name: str):
        if name.startswith("_") or name not in wire.REMOTE:
            raise AttributeError(f"{name} isn't available on a remote project")

        def call(*args: Any, **kwargs: Any) -> Any:
            self._flush()  # buffered writes land before anything that follows
            return self.client.rpc(name, args, kwargs)

        call.__name__ = name
        return call

    # Progress arrives many times a second per step; send the latest value per execution every
    # few seconds as one batch instead of a request each time.
    def set_progress(self, exec_id: str, done: float, total: float | None, unit: str | None) -> None:
        with self._plock:
            self._progress[exec_id] = (exec_id, done, total, unit)
            if self._flusher is None:
                self._flusher = threading.Thread(target=self._flush_loop, daemon=True, name="dendrite-progress")
                self._flusher.start()

    # A pipeline body enqueues every submitted step at once; send those "queued" rows as one
    # batch. Any other call flushes them first, so this client always reads its own writes.
    def upsert_node_run(self, exp_id: str, node: str, **values: Any) -> None:
        if values.get("status") != "queued":
            self._flush()  # waits for any flush in flight, so the queued row lands first
            self.client.rpc("upsert_node_run", (exp_id, node), values)
            return
        with self._plock:
            self._queued.append(((exp_id, node), values))

    def _flush(self) -> None:
        # Held while sending: a call that follows waits until earlier writes have landed, so a
        # step's later state can never race (or be overwritten by) its buffered "queued" row.
        with self._flock:
            with self._plock:
                progress, self._progress = list(self._progress.values()), {}
                queued, self._queued = self._queued, []
            calls = [("upsert_node_run", args, kw) for args, kw in queued]
            calls += [("set_progress", args, {}) for args in progress]
            for i in range(0, len(calls), 500):
                self.client.rpc_batch(calls[i : i + 500])

    def _flush_loop(self) -> None:
        while not self._stop.wait(PROGRESS_FLUSH):
            try:
                self._flush()
            except Exception:  # noqa: BLE001 - progress is display-only; the next flush retries
                pass

    def dispose(self) -> None:
        self._stop.set()
        try:
            self._flush()
        finally:
            self.client.close()


# -- blobs ---------------------------------------------------------------------------
INLINE_PER_REQUEST = 8 * 1024**2


def _chunks(items: list[dict]) -> Iterator[list[dict]]:
    """At most ``BATCH`` items and ``INLINE_PER_REQUEST`` inline bytes per request."""
    chunk: list[dict] = []
    inline = 0
    for item in items:
        size = len(item.get("data") or b"")
        if chunk and (len(chunk) >= BATCH or inline + size > INLINE_PER_REQUEST):
            yield chunk
            chunk, inline = [], 0
        chunk.append(item)
        inline += size
    if chunk:
        yield chunk


def _b64_sha256(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


class ServerStore(ArtifactStore):
    """Blobs through a dendrite server: presigned transfers straight to the bucket."""

    remote = True

    def __init__(self, client: ServerClient, cache_dir: Path):
        super().__init__(cache_dir)
        self.client = client
        self._urls: dict[str, tuple[str, float]] = {}

    @property
    def url(self) -> str:
        return f"{self.client.server}/p/{self.client.project}"

    # -- presence ----------------------------------------------------------------
    def has(self, h: str) -> bool:
        return h in self.has_many([h])

    def has_many(self, hashes: Iterable[str]) -> set[str]:
        wanted = sorted(set(hashes))
        found: set[str] = set()
        for i in range(0, len(wanted), BATCH):
            res = self.client.request("POST", self.client.api("blobs/presence"), {"hashes": wanted[i : i + BATCH]})
            found |= set(res["present"])
        return found

    # -- uploads -----------------------------------------------------------------
    def _put_missing(self, files: list[tuple[str, Path]], previews: dict[str, str] | None = None) -> None:
        from concurrent.futures import ThreadPoolExecutor

        unique = {h: f for h, f in files}
        items = []
        for h, f in unique.items():
            size = f.stat().st_size
            item: dict[str, Any] = {"hash": h, "size": size}
            if size <= INLINE_BYTES:  # small: the bytes ride along, and the server stores them if missing
                item["data"] = f.read_bytes()
            if previews and h in previews:
                item["preview"] = previews[h]
            items.append(item)
        for chunk in _chunks(items):
            plans = self.client.request("POST", self.client.api("blobs/missing"), {"items": chunk})
            uploads = plans["uploads"]
            if len(uploads) <= 1:
                for plan in uploads:
                    self._upload(plan, unique[plan["hash"]])
                continue
            with ThreadPoolExecutor(UPLOADS_IN_PARALLEL) as pool:
                list(pool.map(lambda plan: self._upload(plan, unique[plan["hash"]]), uploads))

    def _put_object_file(self, h: str, src: Path) -> None:
        self._put_missing([(h, src)])

    def _put_object_bytes(self, data: bytes, preview: str | None = None) -> str:
        from .hashing import sha256_bytes

        h = sha256_bytes(data)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.cache_dir, prefix=".put-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            # One round trip: the server skips what it has, and small blobs travel inline.
            self._put_missing([(h, Path(tmp))], {h: preview} if preview is not None else None)
        finally:
            Path(tmp).unlink(missing_ok=True)
        return h

    def put_value(self, value: Any):
        import pickle

        from .store import ArtifactRef

        data = pickle.dumps(value, protocol=5)
        # The server never unpickles; it shows the text the producer recorded.
        try:
            text = repr(value)[:65536]
        except Exception:  # noqa: BLE001 - an unrepresentable value just has no preview
            text = None
        return ArtifactRef(self._put_object_bytes(data, text), "value", len(data), None)

    def prefetch(self, refs) -> None:
        self._prefetch(r.hash for r in refs if r.kind != "dir")

    def _upload(self, plan: dict, src: Path) -> None:
        method = plan["method"]
        if method == "proxy":
            with open(src, "rb") as f:
                self.client.request("PUT", plan["url"].removeprefix(self.client.server), content=f)
            return
        parts, extra = None, {}
        if method == "put":
            with open(src, "rb") as f:
                r = self.client.blob_http.put(plan["url"], content=f, headers=plan["headers"])
            if r.status_code not in (200, 412):  # 412: someone stored the same content first
                raise UploadFailed(f"upload of {plan['hash'][:12]} failed: {r.status_code} {r.text[:300]}")
            if plan.get("checksum") == "crc32c":  # GCS: the server compares it with what was stored
                from .hosted.gcs import crc32c_b64

                try:
                    extra["crc32c"] = crc32c_b64(src)
                except ImportError:  # google-crc32c isn't installed: the server checks the size only
                    pass
        else:
            parts = self._multipart(plan, src)
        self._commit(plan, parts, extra)

    def _multipart(self, plan: dict, src: Path) -> list[dict]:
        size, part = src.stat().st_size, plan["part_size"]
        sums = []
        with open(src, "rb") as f:
            for n in range(1, plan["parts"] + 1):
                f.seek((n - 1) * part)
                sums.append({"n": n, "checksum": _b64_sha256(f.read(part))})
        done = []
        with open(src, "rb") as f:
            for i in range(0, len(sums), 100):
                urls = self.client.request(
                    "POST", self.client.api("blobs/parts"), {"upload": plan["upload"], "parts": sums[i : i + 100]}
                )["parts"]
                for u in urls:
                    f.seek((u["n"] - 1) * part)
                    data = f.read(min(part, size - (u["n"] - 1) * part))
                    r = self.client.blob_http.put(u["url"], content=data, headers=u["headers"])
                    if r.status_code != 200:
                        raise UploadFailed(
                            f"part {u['n']} of {plan['hash'][:12]} failed: {r.status_code} {r.text[:300]}"
                        )
                    done.append(
                        {"n": u["n"], "etag": r.headers["etag"], "checksum": u["headers"]["x-amz-checksum-sha256"]}
                    )
        return done

    def _commit(self, plan: dict, parts: list[dict] | None, extra: dict | None = None) -> None:
        body: dict[str, Any] = {"upload": plan["upload"], **(extra or {})}
        if parts:
            body["parts"] = parts
        wait = 0.2
        while True:
            res = self.client.request("POST", self.client.api("blobs/commit"), body)
            if res["state"] == "committed":
                return
            if res["state"] == "failed":
                raise UploadFailed(f"upload of {plan['hash'][:12]} was rejected: {res.get('error')}")
            time.sleep(wait)  # the server is verifying a staged upload
            wait = min(wait * 2, 5.0)
            body.pop("parts", None)

    # -- downloads ---------------------------------------------------------------
    def _prefetch(self, hashes: Iterable[str]) -> None:
        need = sorted({h for h in hashes if h not in self._urls or self._urls[h][1] < time.time()})
        for i in range(0, len(need), BATCH):
            urls = self.client.request("POST", self.client.api("blobs/urls"), {"hashes": need[i : i + BATCH]})["urls"]
            for h, u in urls.items():
                if u is None:
                    self._urls.pop(h, None)
                else:
                    self._urls[h] = (u, time.time() + 600)  # well inside the URL's own expiry

    def _url(self, h: str) -> str:
        self._prefetch([h])
        hit = self._urls.pop(h, None)
        if hit is None:
            raise FileNotFoundError(f"blob {h[:12]} is not in the store")
        return hit[0]

    def _stream(self, h: str) -> Iterator[bytes]:
        url = self._url(h)
        if url.startswith(self.client.server + "/api/"):
            headers = {"Authorization": f"Bearer {self.client.token()}"}
            ctx = self.client.http.stream("GET", url.removeprefix(self.client.server), headers=headers)
        else:
            ctx = self.client.blob_http.stream("GET", url)
        with ctx as r:
            if r.status_code == 404:
                raise FileNotFoundError(f"blob {h[:12]} is not in the store")
            if r.status_code >= 400:
                raise ServerError(f"download of {h[:12]} failed: {r.status_code}")
            yield from r.iter_bytes(1024 * 1024)

    def _get_object_file(self, h: str, dest: Path) -> None:
        with open(dest, "wb") as f:
            for chunk in self._stream(h):
                f.write(chunk)

    def read_bytes(self, h: str) -> bytes:
        return b"".join(self._stream(h))

    def open_object(self, h: str) -> BinaryIO:
        f = tempfile.SpooledTemporaryFile(max_size=64 * 1024**2)
        for chunk in self._stream(h):
            f.write(chunk)
        f.seek(0)
        return f

    def _manifest_bytes(self, h: str) -> bytes:
        local = self.cache_dir / "manifests" / h
        if local.is_file():
            return local.read_bytes()
        data = self.read_bytes(h)
        local.parent.mkdir(parents=True, exist_ok=True)
        tmp = local.with_name(f".{h}.{uuid.uuid4().hex}")
        tmp.write_bytes(data)
        os.replace(tmp, local)
        return data

    def load(self, ref):
        if ref.kind == "dir" and not (self.cache_dir / "checkouts" / ref.hash).exists():
            self._prefetch(h for _, h, _ in self.manifest(ref))  # one request for every member's URL
        return super().load(ref)

    # -- administration (admins only; listing runs on the server) --------------------
    def delete(self, h: str) -> None:
        self.delete_many([h])

    def delete_many(self, hashes: Iterable[str]) -> None:
        hs = list(hashes)
        for i in range(0, len(hs), BATCH):
            self.client.request("POST", self.client.api("blobs/delete"), {"hashes": hs[i : i + BATCH]})
        self.drop_checkouts(hs)

    def iter_objects(self) -> Iterator[tuple[str, int, float]]:
        raise NotImplementedError("listing a hosted store runs on the server: use lab.storage() or lab.gc()")


def connect(server: str, project: str, home: Path, **client_options: Any) -> tuple[HttpMetadata, ServerStore]:
    client = ServerClient(server, project, **client_options)
    return HttpMetadata(client), ServerStore(client, home / "cache")
