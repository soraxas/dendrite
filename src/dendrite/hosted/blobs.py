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
Upload sessions live in the control database, so any server process can commit an upload.
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
STALE_VERIFY = 120  # seconds a verification may go without progress before another process takes over


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
    updated_at: float = field(default_factory=time.time)

    FIELDS = ("id", "hash", "size", "key", "staged", "multipart_id", "state", "error")

    def row(self) -> dict:
        return {k: getattr(self, k) for k in self.FIELDS}


class Broker:
    """One project's uploads. Sessions live in the control database (``control.uploads``), so
    a commit can reach a different server process than the one that planned the upload."""

    def __init__(self, store: ArtifactStore, settings: ServerSettings, control=None, project: str = ""):
        self.store, self.settings, self.control, self.project = store, settings, control, project
        protocols = _protocols(store.fs) if isinstance(store, FsspecStore) else set()
        self.s3 = "s3" in protocols
        self.gcs = bool({"gs", "gcs"} & protocols)
        if self.gcs:
            from .gcs import GcsSigner

            endpoint = settings.store_options.get("endpoint_url")
            self.gcs_signer = GcsSigner(settings.gcs_signing_key, endpoint)
            store.signer = lambda path, method, expires, filename=None: self.gcs_signer.sign(
                path, method, expires, filename=filename
            )
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

    # -- sessions ----------------------------------------------------------------
    def _upload(self, upload_id: str) -> Upload:
        row = self.control.upload(upload_id, self.project)
        if row is None:
            raise UploadError("unknown or expired upload; plan it again")
        return Upload(**{k: row[k] for k in (*Upload.FIELDS, "updated_at")})

    def _set(self, up: Upload, **values: Any) -> None:
        for k, v in values.items():
            setattr(up, k, v)
        self.control.update_upload(up.id, **values)

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
        if self.gcs:
            return self._plan_gcs(h, size)
        if not self.s3:
            return {"hash": h, "method": "proxy"}
        multipart = size > self.settings.multipart_threshold
        staged = self.settings.verify_uploads == "hash" and (multipart or not self.settings.store_enforces_sha256)
        up = Upload(uuid.uuid4().hex, h, size, "", staged)
        up.key = self._staging(up.id) if staged else self._final(h)
        if multipart:
            bucket, key = self._split(up.key)
            up.multipart_id = self._call("create_multipart_upload", Bucket=bucket, Key=key, ChecksumAlgorithm="SHA256")[
                "UploadId"
            ]
        self.control.save_upload(self.project, up.row())
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
        part = max(self.settings.part_size, math.ceil(size / 9_000))
        return {"hash": h, "method": "multipart", "upload": up.id, "part_size": part, "parts": math.ceil(size / part)}

    def _plan_gcs(self, h: str, size: int) -> dict:
        """One signed PUT (GCS takes up to 5 TiB in one), never overwriting an existing object.
        GCS checksums with CRC32C, not SHA-256: the client reports its CRC32C on commit, and with
        ``verify_uploads = "hash"`` the server also re-hashes from a staging key."""
        staged = self.settings.verify_uploads == "hash"
        up = Upload(uuid.uuid4().hex, h, size, "", staged)
        up.key = self._staging(up.id) if staged else self._final(h)
        self.control.save_upload(self.project, up.row())
        headers = {"x-goog-if-generation-match": "0"}
        url = self.gcs_signer.sign(up.key, "PUT", self.settings.url_seconds, headers=headers)
        return {"hash": h, "method": "put", "upload": up.id, "url": url, "headers": headers, "checksum": "crc32c"}

    def part_urls(self, upload_id: str, parts: list[dict]) -> list[dict]:
        """Presigned URLs for parts, each bound to the SHA-256 the client computed for it."""
        up = self._upload(upload_id)
        out = []
        for p in parts:
            n, checksum = int(p["n"]), str(p["checksum"])
            url = self._sign(up.key, "upload_part", UploadId=up.multipart_id, PartNumber=n, ChecksumSHA256=checksum)
            out.append({"n": n, "url": url, "headers": {"x-amz-checksum-sha256": checksum}})
        return out

    # -- committing --------------------------------------------------------------
    def commit(self, upload_id: str, parts: list[dict] | None = None, crc32c: str | None = None) -> dict:
        up = self._upload(upload_id)
        if up.state == "verifying":
            # The process hashing it may have died: if it has gone quiet, take the job over (once).
            if self.control.update_upload(up.id, when_state="verifying", stale_before=time.time() - STALE_VERIFY):
                threading.Thread(target=self._verify_staged, args=(up,), daemon=True).start()
            return self._state(up)
        if up.state != "uploading":
            return self._state(up)
        if self.gcs:
            return self._commit_gcs(up, crc32c)
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
                self._set(up, state="committed")
                return self._state(up)
            raise UploadError("nothing was uploaded") from None
        if int(head.get("ContentLength", -1)) != up.size:
            self._fail(up, f"uploaded {head.get('ContentLength')} bytes, expected {up.size}")
        elif not up.multipart_id and head.get("ChecksumSHA256") not in (None, b64(up.hash)):
            self._fail(up, "uploaded bytes don't match their hash")
        elif up.staged and up.size <= INLINE_VERIFY:
            self._verify_staged(up)  # small enough to check within this request
        elif up.staged:
            self._set(up, state="verifying")  # the client polls until the server has hashed it
            threading.Thread(target=self._verify_staged, args=(up,), daemon=True).start()
        else:
            self._set(up, state="committed")
        return self._state(up)

    def _commit_gcs(self, up: Upload, crc32c: str | None) -> dict:
        try:
            info = self.store.fs.info(up.key, refresh=True)
        except FileNotFoundError:
            if self.store.has(up.hash):  # a concurrent upload of the same content won
                self._set(up, state="committed")
                return self._state(up)
            raise UploadError("nothing was uploaded") from None
        if int(info.get("size", -1)) != up.size:
            self._fail(up, f"uploaded {info.get('size')} bytes, expected {up.size}")
        elif crc32c and info.get("crc32c") and info["crc32c"] != crc32c:
            self._fail(up, "the stored bytes' CRC32C doesn't match the client's: corrupted in transit")
        elif up.staged and up.size <= INLINE_VERIFY:
            self._verify_staged(up)
        elif up.staged:
            self._set(up, state="verifying")
            threading.Thread(target=self._verify_staged, args=(up,), daemon=True).start()
        else:
            self._set(up, state="committed")
        return self._state(up)

    def _verify_staged(self, up: Upload) -> None:
        try:
            digest = hashlib.sha256()
            with self.store.fs.open(up.key, "rb", block_size=8 * 1024**2) as f:
                while chunk := f.read(8 * 1024**2):
                    digest.update(chunk)
                    self.control.update_upload(up.id)  # still alive: keeps another process from taking over
            if digest.hexdigest() != up.hash:
                self._fail(up, "uploaded bytes don't match their hash")
                return
            if not self.store.has(up.hash):
                self.store.fs.copy(up.key, self._final(up.hash))
            self.store.fs.rm_file(up.key)
            self._set(up, state="committed")
        except Exception as e:  # report it to the polling client
            self._fail(up, f"verification failed: {e}")

    def _fail(self, up: Upload, why: str) -> None:
        self._set(up, state="failed", error=why)
        try:
            self.store.fs.rm_file(up.key)
        except FileNotFoundError:
            pass

    def _state(self, up: Upload) -> dict:
        out = {"hash": up.hash, "state": up.state}
        if up.error:
            out["error"] = up.error
        if up.state in ("committed", "failed"):
            self.control.delete_upload(up.id)
        return out

    def expire(self, older_than: float = 24 * 3600) -> None:
        """Forget uploads never committed, and delete their staged bytes."""
        for row in self.control.abandoned_uploads(self.project, time.time() - older_than):
            if row["staged"] or row["key"] != self._final(row["hash"]):
                try:
                    self.store.fs.rm_file(row["key"])
                except FileNotFoundError:
                    pass
            self.control.delete_upload(row["id"])

    # -- downloads ---------------------------------------------------------------
    def download_urls(self, hashes: list[str]) -> dict[str, str | None]:
        """Presigned GETs for present blobs (None for a missing one); ``proxy`` when the store can't sign."""
        present = self.store.has_many(hashes)
        out: dict[str, str | None] = {}
        for h in hashes:
            if h not in present:
                out[h] = None
            elif self.s3 or self.gcs:
                out[h] = self.store.signed_url(h, h, self.settings.url_seconds)
            else:
                out[h] = "proxy"
        return out
