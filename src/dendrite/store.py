"""Content-addressed, immutable artifact storage.

Objects live at ``objects/<h[:2]>/<h[2:]>``. A directory is stored as a manifest blob
(canonical JSON list of ``[relpath, hash, size]``) whose hash is the directory's hash, so
identical files are de-duplicated across directories.
"""

from __future__ import annotations

import json
import os
import pickle
import shutil
import sys
import tempfile
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from .hashing import canonical_json, sha256_bytes, sha256_file

KINDS = ("file", "dir", "value")
PENDING = "pending"  # kind of a plan-mode placeholder: an output that has not been computed


class PlanIncomplete(RuntimeError):
    """A plan tried to read the contents of an output that would only exist after running."""

    def __init__(self, ref: ArtifactRef):
        self.ref = ref
        self.node = ref.hash.removeprefix("plan:").split(".", 1)[0]
        super().__init__(f"the plan depends on the output of {self.node!r}, which has not been computed")


def _writable_retry(func, path, _exc) -> None:
    # Store objects are read-only and checkouts hardlink them; Windows won't delete a read-only file.
    try:
        os.chmod(path, 0o644)
        func(path)
    except OSError:
        pass


def remove_tree(path: str | os.PathLike) -> None:
    """Delete a directory tree (or file) if it exists, including read-only files, ignoring the rest."""
    p = Path(path)
    if p.is_dir() and not p.is_symlink():
        if sys.version_info >= (3, 12):
            shutil.rmtree(p, onexc=_writable_retry)
        else:  # onerror passes (type, value, tb) where onexc passes the exception
            shutil.rmtree(p, onerror=_writable_retry)
    elif p.exists() or p.is_symlink():
        try:
            p.unlink()
        except PermissionError:
            _writable_retry(os.unlink, p, None)


@dataclass(frozen=True)
class ArtifactRef:
    """Lightweight, serialisable handle to an immutable artifact."""

    hash: str
    kind: str
    size: int
    filename: str | None = None

    @property
    def short(self) -> str:
        return self.hash[:12]

    def __str__(self) -> str:
        return f"{self.kind}:{self.short}"


class ArtifactStore(ABC):
    """Blob backend. Subclasses implement raw object IO; packing logic is shared."""

    remote = False  # objects live off this machine, so reading one costs a download

    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)

    # -- raw object IO -------------------------------------------------------------
    @abstractmethod
    def has(self, h: str) -> bool: ...

    @abstractmethod
    def _put_object_file(self, h: str, src: Path) -> None:
        """Store ``src`` (already hashed to ``h``) if absent. Must be atomic per object."""

    @abstractmethod
    def _get_object_file(self, h: str, dest: Path) -> None:
        """Copy object ``h`` to local path ``dest``."""

    @abstractmethod
    def read_bytes(self, h: str) -> bytes: ...

    @abstractmethod
    def open_object(self, h: str) -> BinaryIO:
        """A seekable binary stream of object ``h``, read on demand."""

    def local_path(self, h: str) -> Path | None:
        """A local file holding object ``h`` if one exists already, without downloading it."""
        checkouts = self.cache_dir / "checkouts"
        p = checkouts / f"preview-{h}" / "data"
        if p.is_file():
            return p
        # A step's checkout of a file artifact: checkouts/<hash>/<filename>.
        files = list((checkouts / h).iterdir()) if (checkouts / h).is_dir() else []
        return files[0] if len(files) == 1 and files[0].is_file() else None

    def signed_url(self, h: str, filename: str, expires: int) -> str | None:
        """A time-limited URL a browser can fetch object ``h`` from directly, if the backend can sign."""
        return None

    @abstractmethod
    def delete(self, h: str) -> None: ...

    @abstractmethod
    def iter_objects(self) -> Iterator[tuple[str, int, float]]:
        """Every stored object as ``(hash, size, mtime)``; temp files are skipped."""

    def delete_many(self, hashes: Iterable[str]) -> None:
        for h in hashes:
            self.delete(h)

    def prefetch(self, refs: Iterable[ArtifactRef]) -> None:
        """Hint that ``refs`` are about to be loaded (a remote store batches their lookups)."""
        return None  # local stores have nothing to prepare

    def has_many(self, hashes: Iterable[str]) -> set[str]:
        """The subset of ``hashes`` that is stored."""
        return {h for h in set(hashes) if self.has(h)}

    def _put_missing(self, files: list[tuple[str, Path]]) -> None:
        """Store each ``(hash, path)`` the store doesn't already have."""
        present = self.has_many(h for h, _ in files)
        for h, f in {h: f for h, f in files}.items():
            if h not in present:
                self._put_object_file(h, f)

    @property
    @abstractmethod
    def url(self) -> str: ...

    def _put_object_bytes(self, data: bytes) -> str:
        h = sha256_bytes(data)
        if not self.has(h):
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.cache_dir, prefix=".put-")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                self._put_object_file(h, Path(tmp))
            finally:
                Path(tmp).unlink(missing_ok=True)
        return h

    # -- artifact API --------------------------------------------------------------
    def put_path(self, path: str | os.PathLike) -> ArtifactRef:
        p = Path(path)
        if p.is_dir():
            entries, files = [], []
            total = 0
            for f in sorted(x for x in p.rglob("*") if x.is_file()):
                h, size = sha256_file(f)
                files.append((h, f))
                entries.append([f.relative_to(p).as_posix(), h, size])
                total += size
            self._put_missing(files)
            manifest = canonical_json(entries).encode()
            return ArtifactRef(self._put_object_bytes(manifest), "dir", total, p.name)
        if p.is_file():
            h, size = sha256_file(p)
            self._put_missing([(h, p)])
            return ArtifactRef(h, "file", size, p.name)
        raise FileNotFoundError(f"cannot store {p}: no such file or directory")

    def put_value(self, value: Any) -> ArtifactRef:
        data = pickle.dumps(value, protocol=5)
        return ArtifactRef(self._put_object_bytes(data), "value", len(data), None)

    def manifest(self, ref: ArtifactRef) -> list[tuple[str, str, int]]:
        return [tuple(e) for e in json.loads(self._manifest_bytes(ref.hash))]

    def _manifest_bytes(self, h: str) -> bytes:
        return self.read_bytes(h)

    def verify(self, ref: ArtifactRef, deep: bool = True) -> bool:
        """Whether every blob of ``ref`` is present. ``deep=False`` checks only a directory's
        manifest, not each member: a display hint, never enough for a cache hit."""
        if not self.has(ref.hash):
            return False
        if ref.kind == "dir" and deep:
            members = {h for _, h, _ in self.manifest(ref)}
            return self.has_many(members) == members
        return True

    def load(self, ref: ArtifactRef) -> Any:
        """Materialise an artifact for use inside a node: a local Path, or the value."""
        if ref.kind == PENDING:
            raise PlanIncomplete(ref)
        if ref.kind == "value":
            return pickle.loads(self.read_bytes(ref.hash))
        dest = self.cache_dir / "checkouts" / ref.hash
        target = dest / (ref.filename or "data")
        if dest.exists():
            return target
        staging = self.cache_dir / "checkouts" / f".{ref.hash}.{uuid.uuid4().hex}"
        staging.mkdir(parents=True)
        try:
            root = staging / (ref.filename or "data")
            if ref.kind == "file":
                self._checkout(ref.hash, root)
            else:
                root.mkdir()
                for rel, h, _ in self.manifest(ref):
                    out = root / rel
                    out.parent.mkdir(parents=True, exist_ok=True)
                    self._checkout(h, out)
            try:
                os.replace(staging, dest)
            except OSError:  # another worker won the race
                if not dest.exists():
                    raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return target

    def _checkout(self, h: str, dest: Path) -> None:
        self._get_object_file(h, dest)

    def materialize_file(self, h: str) -> Path:
        """Check out a single blob for preview, without loading a directory's other members."""
        dest = self.cache_dir / "checkouts" / f"preview-{h}" / "data"
        if dest.is_file():
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{uuid.uuid4().hex}")
        try:
            self._checkout(h, tmp)
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)
        return dest

    def drop_checkouts(self, hashes: Iterable[str]) -> None:
        """Remove local checkouts of these artifacts (hardlinks would keep deleted bytes alive)."""
        for h in hashes:
            remove_tree(self.cache_dir / "checkouts" / h)
            remove_tree(self.cache_dir / "checkouts" / f"preview-{h}")


class LocalStore(ArtifactStore):
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root).expanduser().resolve()
        super().__init__(self.root / "cache")
        (self.root / "objects").mkdir(parents=True, exist_ok=True)

    @property
    def url(self) -> str:
        return self.root.as_uri()

    def _obj(self, h: str) -> Path:
        return self.root / "objects" / h[:2] / h[2:]

    def has(self, h: str) -> bool:
        return self._obj(h).is_file()

    def _put_object_file(self, h: str, src: Path) -> None:
        dest = self._obj(h)
        if dest.exists():
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".tmp-")
        os.close(fd)
        try:
            shutil.copyfile(src, tmp)
            os.chmod(tmp, 0o444)
            os.replace(tmp, dest)
        finally:
            Path(tmp).unlink(missing_ok=True)

    def _get_object_file(self, h: str, dest: Path) -> None:
        shutil.copyfile(self._obj(h), dest)

    def _checkout(self, h: str, dest: Path) -> None:
        # Objects are read-only, so a hardlink is a safe zero-copy checkout.
        try:
            os.link(self._obj(h), dest)
        except OSError:
            shutil.copyfile(self._obj(h), dest)

    def read_bytes(self, h: str) -> bytes:
        return self._obj(h).read_bytes()

    def open_object(self, h: str) -> BinaryIO:
        return open(self._obj(h), "rb")

    def local_path(self, h: str) -> Path | None:
        p = self._obj(h)
        return p if p.is_file() else None

    def delete(self, h: str) -> None:
        p = self._obj(h)
        if p.exists():
            os.chmod(p, 0o644)
            p.unlink()

    def iter_objects(self) -> Iterator[tuple[str, int, float]]:
        for prefix in sorted((self.root / "objects").iterdir()):
            if not prefix.is_dir() or len(prefix.name) != 2:
                continue
            for f in prefix.iterdir():
                if f.name.startswith("."):  # in-progress writes
                    continue
                st = f.stat()
                yield prefix.name + f.name, st.st_size, st.st_mtime


class FsspecStore(ArtifactStore):
    """Any fsspec filesystem: ``s3://bucket/prefix``, ``gs://…``, ``memory://…``.

    For S3-compatible services such as MinIO pass ``storage_options``, e.g.
    ``{"endpoint_url": "http://localhost:9000", "key": ..., "secret": ...}``, or set the
    standard AWS environment variables.
    """

    def __init__(self, url: str, cache_dir: str | os.PathLike, **storage_options: Any):
        import fsspec

        super().__init__(Path(cache_dir))
        self._url = url.rstrip("/")
        self.fs, self.base = fsspec.core.url_to_fs(self._url, **storage_options)
        self.base = self.base.rstrip("/")
        self.remote = not ({"file", "local"} & _protocols(self.fs))

    @property
    def url(self) -> str:
        return self._url

    def _obj(self, h: str) -> str:
        return f"{self.base}/objects/{h[:2]}/{h[2:]}"

    def has(self, h: str) -> bool:
        # Not fs.exists(): s3fs answers that from cached directory listings, which miss blobs
        # written since (another process's upload, a commit). One HEAD, never cached.
        try:
            self.fs.info(self._obj(h), refresh=True)
            return True
        except FileNotFoundError:
            return False

    def _put_object_file(self, h: str, src: Path) -> None:
        # Object PUTs are atomic on S3-style stores; content addressing makes races benign.
        self.fs.put_file(str(src), self._obj(h))

    def _get_object_file(self, h: str, dest: Path) -> None:
        self.fs.get_file(self._obj(h), str(dest))

    def read_bytes(self, h: str) -> bytes:
        return self.fs.cat_file(self._obj(h))

    def open_object(self, h: str) -> BinaryIO:
        return self.fs.open(self._obj(h), "rb")

    def has_many(self, hashes: Iterable[str]) -> set[str]:
        wanted = set(hashes)
        if len(wanted) <= 512:  # a shard of a big bucket lists slower than a few hundred HEADs
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(16) as pool:
                return {h for h, ok in zip(wanted, pool.map(self.has, wanted), strict=True) if ok}
        # Many hashes: one listing per two-character shard instead of a request per object.
        found: set[str] = set()
        for shard in {h[:2] for h in wanted}:
            path = f"{self.base}/objects/{shard}"
            try:
                names = self.fs.ls(path, detail=False, refresh=True)
            except FileNotFoundError:
                continue
            finally:
                self.fs.invalidate_cache(path)  # a cached listing would hide later uploads
            found |= {shard + n.rstrip("/").rsplit("/", 1)[-1] for n in names}
        return wanted & found

    signer = None  # set by a server for stores fsspec can't sign for (GCS, see hosted/gcs.py)

    def signed_url(self, h: str, filename: str, expires: int) -> str | None:
        if self.signer is not None:
            return self.signer(self._obj(h), "GET", expires, filename=filename)
        kwargs: dict[str, Any] = {}
        if "s3" in _protocols(self.fs):  # name the download; S3 passes these to GetObject
            kwargs["ResponseContentDisposition"] = content_disposition(filename)
        try:
            return self.fs.sign(self._obj(h), expiration=expires, **kwargs)
        except NotImplementedError:
            return None

    def delete(self, h: str) -> None:
        if self.fs.exists(self._obj(h)):
            self.fs.rm_file(self._obj(h))

    def delete_many(self, hashes: Iterable[str]) -> None:
        paths = [self._obj(h) for h in hashes]
        for i in range(0, len(paths), 1000):  # S3 DeleteObjects takes at most 1000 keys
            try:
                self.fs.rm(paths[i : i + 1000])
            except FileNotFoundError:
                for p in paths[i : i + 1000]:
                    if self.fs.exists(p):
                        self.fs.rm_file(p)

    def iter_objects(self) -> Iterator[tuple[str, int, float]]:
        root = f"{self.base}/objects"
        if not self.fs.exists(root):
            return
        listing = self.fs.find(root, detail=True)
        self.fs.invalidate_cache(root)  # keep later existence checks off this snapshot
        for path, info in listing.items():
            parts = path.rstrip("/").split("/")
            if len(parts) < 2 or len(parts[-2]) != 2 or parts[-1].startswith("."):
                continue
            yield parts[-2] + parts[-1], int(info.get("size") or 0), _mtime(info)

    def _manifest_bytes(self, h: str) -> bytes:
        # Manifests are immutable by content, so a local copy saves a round trip per directory.
        local = self.cache_dir / "manifests" / h
        if local.is_file():
            return local.read_bytes()
        data = self.read_bytes(h)
        local.parent.mkdir(parents=True, exist_ok=True)
        tmp = local.with_name(f".{h}.{uuid.uuid4().hex}")
        tmp.write_bytes(data)
        os.replace(tmp, local)
        return data


def _protocols(fs: Any) -> set[str]:
    p = fs.protocol
    return {p} if isinstance(p, str) else set(p)


def content_disposition(filename: str, kind: str = "attachment") -> str:
    from urllib.parse import quote

    return f"{kind}; filename*=utf-8''{quote(filename)}"


def _mtime(info: dict[str, Any]) -> float:
    """Modification time from an fsspec info dict. Unknown counts as *now*, so garbage
    collection's grace period errs towards keeping a blob that may still be uploading."""
    import time
    from datetime import datetime

    for key in ("mtime", "LastModified", "last_modified", "created", "updated", "timeCreated"):
        v = info.get(key)
        if v is None:
            continue
        if hasattr(v, "timestamp"):
            return v.timestamp()
        try:
            return float(v)
        except (TypeError, ValueError):
            pass
        try:
            return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
    return time.time()


def open_store(url: str, cache_dir: str | os.PathLike, **storage_options: Any) -> ArtifactStore:
    if "://" not in url:
        return LocalStore(url)
    if url.startswith("file://"):
        return LocalStore(url.removeprefix("file://"))
    return FsspecStore(url, cache_dir, **storage_options)
