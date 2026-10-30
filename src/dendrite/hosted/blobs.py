"""Brokered blob access for a project: what to upload, where, and when a blob counts as committed.

Clients upload and download straight to the bucket with presigned URLs, so blob bytes don't
pass through the server. Stores that can't presign (a local directory, ``memory://``, and GCS
for now) fall back to streaming through the server, which hashes them on the way in.

Upload of one missing blob, by store and ``verify_uploads``:

* S3/MinIO, ``checksum``: a presigned PUT to the blob's final key carrying its SHA-256, with
  ``If-None-Match: *`` so nothing is overwritten. Above ``multipart_threshold``: a multipart
  upload whose part URLs each carry that part's SHA-256. The store enforces both.
* S3/MinIO, ``hash``: the same, except that uploads whose whole-object SHA-256 the store didn't
  enforce (multipart, or ``store_enforces_sha256 = false``) go to a staging key. On commit the
  server hashes the staged object and copies it into place.
* any other store: ``proxy``, a PUT to the server, which hashes the body as it stores it.

A blob is committed once it exists at its content address; publish refuses any that isn't.
"""

from __future__ import annotations

import base64
import hashlib
import math
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..store import ArtifactStore, FsspecStore, _protocols
from .settings import ServerSettings

INLINE_VERIFY = 64 * 1024**2  # staged uploads up to this size are verified within the commit request


def b64(h: str) -> str:
    """S3's checksum encoding of a hex SHA-256."""
    return base64.b64encode(bytes.fromhex(h)).decode()


class UploadError(ValueError):
    status = 400


@dataclass
class Upload:
    id: str
    hash: str
    size: int
    key: str  # where the bytes go: the final object, or a staging key
    staged: bool
    multipart_id: str | None = None
    state: str = "uploading"  # uploading | verifying | committed | failed
    error: str | None = None
    preview: str | None = None
    created: float = field(default_factory=time.time)


class Broker:
    def __init__(self, store: ArtifactStore, settings: ServerSettings):
        self.store, self.settings = store, settings
        self.s3 = isinstance(store, FsspecStore) and "s3" in _protocols(store.fs)
        # In memory: an upload must be committed through the server process that planned it.
        self.uploads: dict[str, Upload] = {}
        self._lock = threading.Lock()
        self._expired_at = time.time()

    # -- S3 plumbing -------------------------------------------------------------
    def _split(self, path: str) -> tuple[str, str]:
        bucket, key, _ = self.store.fs.split_path(path)
        return bucket, key

    def _final(self, h: str) -> str:
        return self.store._obj(h)

    def _staging(self, upload_id: str) -> str:
        return f"{self.store.base}/uploads/{upload_id}"

    def _sign(self, path: str, method: str, **params: Any) -> str:
        return self.store.fs.url(path, expires=self.settings.url_seconds, client_method=method, **params)

    def _call(self, method: str, **kw: Any) -> Any:
        return self.store.fs.call_s3(method, **kw)

    # -- planning ----------------------------------------------------------------
    def missing(self, items: list[dict]) -> list[dict]:
        """Plans for the blobs the project lacks; blobs it has are skipped (deduplication)."""
        if time.time() - self._expired_at > 3600:  # now and then, forget abandoned uploads
            self._expired_at = time.time()
            self.expire()
        present = self.store.has_many(i["hash"] for i in items)
        return [
            self.plan(i["hash"], int(i["size"]))
            for i in {i["hash"]: i for i in items}.values()
            if i["hash"] not in present
        ]

    def plan(self, h: str, size: int) -> dict:
        if not self.s3:
            return {"hash": h, "method": "proxy"}
        multipart = size > self.settings.multipart_threshold
        staged = self.settings.verify_uploads == "hash" and (multipart or not self.settings.store_enforces_sha256)
        up = Upload(uuid.uuid4().hex, h, size, "", staged)
        up.key = self._staging(up.id) if staged else self._final(h)
        with self._lock:
            self.uploads[up.id] = up
        if not multipart:
            headers = {"x-amz-checksum-sha256": b64(h)}
            params: dict[str, Any] = {"ChecksumSHA256": b64(h)}
            if not staged:
                headers["If-None-Match"] = "*"
                params["IfNoneMatch"] = "*"
            return {
                "hash": h,
                "method": "put",
                "upload": up.id,
                "url": self._sign(up.key, "put_object", **params),
                "headers": headers,
            }
        bucket, key = self._split(up.key)
        up.multipart_id = self._call("create_multipart_upload", Bucket=bucket, Key=key, ChecksumAlgorithm="SHA256")[
            "UploadId"
        ]
        part = max(self.settings.part_size, math.ceil(size / 9_000))
        return {"hash": h, "method": "multipart", "upload": up.id, "part_size": part, "parts": math.ceil(size / part)}

    def part_urls(self, upload_id: str, parts: list[dict]) -> list[dict]:
        """Presigned URLs for parts, each bound to the SHA-256 the client computed for it."""
        up = self._upload(upload_id)
        out = []
        for p in parts:
            n, checksum = int(p["n"]), str(p["checksum"])
            url = self._sign(up.key, "upload_part", UploadId=up.multipart_id, PartNumber=n, ChecksumSHA256=checksum)
            out.append({"n": n, "url": url, "headers": {"x-amz-checksum-sha256": checksum}})
        return out

    def _upload(self, upload_id: str) -> Upload:
        up = self.uploads.get(upload_id)
        if up is None:
            raise UploadError("unknown or expired upload; plan it again")
        return up

    # -- committing --------------------------------------------------------------
    def commit(self, upload_id: str, parts: list[dict] | None = None, preview: str | None = None) -> dict:
        up = self._upload(upload_id)
        up.preview = preview if preview is not None else up.preview
        if up.state != "uploading":
            return self._state(up)
        bucket, key = self._split(up.key)
        if up.multipart_id:
            if not parts:
                raise UploadError("a multipart commit needs its parts")
            self._call(
                "complete_multipart_upload",
                Bucket=bucket,
                Key=key,
                UploadId=up.multipart_id,
                MultipartUpload={
                    "Parts": [
                        {"PartNumber": int(p["n"]), "ETag": p["etag"], "ChecksumSHA256": p["checksum"]}
                        for p in sorted(parts, key=lambda p: int(p["n"]))
                    ]
                },
            )
        try:
            head = self._call("head_object", Bucket=bucket, Key=key, ChecksumMode="ENABLED")
        except FileNotFoundError:
            if self.store.has(up.hash):  # a concurrent upload of the same content won
                up.state = "committed"
                return self._state(up)
            raise UploadError("nothing was uploaded") from None
        if int(head.get("ContentLength", -1)) != up.size:
            self._fail(up, f"uploaded {head.get('ContentLength')} bytes, expected {up.size}")
        elif not up.multipart_id and head.get("ChecksumSHA256") not in (None, b64(up.hash)):
            self._fail(up, "uploaded bytes don't match their hash")
        elif up.staged and up.size <= INLINE_VERIFY:
            self._verify_staged(up)  # small enough to check within this request
        elif up.staged:
            up.state = "verifying"  # the client polls until the server has hashed it
            threading.Thread(target=self._verify_staged, args=(up,), daemon=True).start()
        else:
            up.state = "committed"
        return self._state(up)

    def _verify_staged(self, up: Upload) -> None:
        try:
            digest = hashlib.sha256()
            with self.store.fs.open(up.key, "rb", block_size=8 * 1024**2) as f:
                while chunk := f.read(8 * 1024**2):
                    digest.update(chunk)
            if digest.hexdigest() != up.hash:
                self._fail(up, "uploaded bytes don't match their hash")
                return
            if not self.store.has(up.hash):
                self.store.fs.copy(up.key, self._final(up.hash))
            self.store.fs.rm_file(up.key)
            up.state = "committed"
        except Exception as e:  # report it to the polling client
            self._fail(up, f"verification failed: {e}")

    def _fail(self, up: Upload, why: str) -> None:
        up.state, up.error = "failed", why
        try:
            self.store.fs.rm_file(up.key)
        except FileNotFoundError:
            pass

    def _state(self, up: Upload) -> dict:
        out = {"hash": up.hash, "state": up.state}
        if up.error:
            out["error"] = up.error
        if up.state in ("committed", "failed"):
            with self._lock:
                self.uploads.pop(up.id, None)
        return out

    def expire(self, older_than: float = 24 * 3600) -> None:
        """Forget uploads never committed, and delete their staged bytes."""
        cutoff = time.time() - older_than
        for up in [u for u in self.uploads.values() if u.created < cutoff and u.state == "uploading"]:
            self._fail(up, "expired")
            self.uploads.pop(up.id, None)

    # -- proxied uploads (stores that can't presign) --------------------------------
    def accept(self, h: str, chunks, tmp_dir) -> None:
        """Store a streamed body under ``h`` if it hashes to ``h``."""
        import os
        import tempfile
        from pathlib import Path

        tmp_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=tmp_dir, prefix=".up-")
        digest = hashlib.sha256()
        try:
            with os.fdopen(fd, "wb") as f:
                for chunk in chunks:
                    digest.update(chunk)
                    f.write(chunk)
            if digest.hexdigest() != h:
                raise UploadError("uploaded bytes don't match their hash")
            if not self.store.has(h):
                self.store._put_object_file(h, Path(tmp))
        finally:
            Path(tmp).unlink(missing_ok=True)

    # -- downloads ---------------------------------------------------------------
    def download_urls(self, hashes: list[str]) -> dict[str, str | None]:
        """Presigned GETs for present blobs (None for a missing one); ``proxy`` when the store can't sign."""
        present = self.store.has_many(hashes)
        out: dict[str, str | None] = {}
        for h in hashes:
            if h not in present:
                out[h] = None
            elif self.s3:
                out[h] = self.store.signed_url(h, h, self.settings.url_seconds)
            else:
                out[h] = "proxy"
        return out
