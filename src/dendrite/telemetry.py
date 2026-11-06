"""Run telemetry: console capture, live metrics, logged samples and environment snapshots.

Everything here is display only. It belongs to an execution (so a reused result brings its
logs along) and never enters a signature, a plan or a cache decision.

Console capture tees ``sys.stdout``/``sys.stderr`` through a proxy that looks up the executing
step in the ``_exec`` contextvar, so concurrent steps on different threads keep separate logs.
Output written straight to file descriptors (C code, subprocesses) is not seen; ``run`` streams
a subprocess through the proxy instead.
"""

from __future__ import annotations

import codecs
import hashlib
import json
import logging
import os
import platform
import re
import socket
import struct
import subprocess
import sys
import threading
import time
import zlib
from collections import deque
from importlib import metadata
from pathlib import Path
from typing import Any

LOG_INTERVAL = 0.5  # seconds between telemetry writes while a step runs; the final one always happens
CHUNK = 64 * 1024  # characters per stored log chunk
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
_SUBPROCESS_TIMEOUT = 2.0

_log = logging.getLogger(__name__)
_local = threading.local()  # .busy: this thread is inside capture (no re-entry)


def log_max() -> int:
    return int(os.environ.get("DENDRITE_LOG_MAX", 16 * 1024 * 1024))


def log_inline_max() -> int:
    """Logs larger than this move to the artifact store when the step ends (the database keeps small ones)."""
    return int(os.environ.get("DENDRITE_LOG_INLINE", 1024 * 1024))


def sample_history() -> int:
    return int(os.environ.get("DENDRITE_SAMPLE_HISTORY", 100))


class Telemetry:
    """Buffered telemetry of one execution, flushed at most every ``LOG_INTERVAL`` seconds.

    The stored log keeps its first ``max_bytes / 2``; after that, output goes to a rolling tail
    of at most the other half (written to a single overwritten row while running), and the final
    flush writes a truncation marker followed by the tail.

    While the step runs, chunks go to the database so the UI can follow them. When it ends, a log
    larger than ``DENDRITE_LOG_INLINE`` is written to the artifact ``store`` as one file instead,
    and its chunks leave the database: big logs then live, and get cleaned up, like other blobs.
    """

    def __init__(self, db: Any, exec_id: str, max_bytes: int | None = None, store: Any = None):
        self.db = db
        self.store = store
        self.exec_id = exec_id
        self._kept: list[str] = []  # the head, again, for writing a big log to the store
        self.half = max(1, (log_max() if max_bytes is None else max_bytes) // 2)
        self._lock = threading.Lock()  # guards buffers
        self._flush_lock = threading.Lock()  # keeps flushes in order
        self._chunks: list[dict] = []  # ready to write
        self._stream: str | None = None  # stream of the chunk being built
        self._parts: list[str] = []
        self._size = 0  # characters in the chunk being built
        self._seq = 0
        self._head = 0  # bytes kept in the head (written or pending)
        self._tail: deque[str] = deque()
        self._tail_bytes = 0
        self._dropped = 0
        self._tail_dirty = False
        self._metrics: list[tuple[str, int, float]] = []
        self._last = time.monotonic()
        self.closed = False

    # -- buffering -------------------------------------------------------------------
    def write(self, stream: str, text: str) -> None:
        if not text or self.closed:
            return
        n = len(text.encode("utf-8", "replace"))
        with self._lock:
            if self._head + n <= self.half and not self._tail:
                self._head += n
                if stream != self._stream or self._size + len(text) > CHUNK:
                    self._seal()
                    self._stream = stream
                self._parts.append(text)
                self._size += len(text)
                if self.store is not None:
                    self._kept.append(text)
            else:
                self._tail.append(text)
                self._tail_bytes += n
                while self._tail_bytes > self.half and len(self._tail) > 1:
                    gone = len(self._tail.popleft().encode("utf-8", "replace"))
                    self._tail_bytes -= gone
                    self._dropped += gone
                self._tail_dirty = True
        self.maybe_flush()

    def metric(self, key: str, step: int, value: float) -> None:
        with self._lock:
            self._metrics.append((key, step, value))
        self.maybe_flush()

    def _seal(self) -> None:
        if self._parts:
            self._chunks.append(
                {"seq": self._seq, "stream": self._stream or "stdout", "t": time.time(), "text": "".join(self._parts)}
            )
            self._seq += 1
        self._parts, self._size = [], 0

    # -- writing ---------------------------------------------------------------------
    def maybe_flush(self) -> None:
        if time.monotonic() - self._last >= LOG_INTERVAL:
            self.flush()

    def flush(self, final: bool = False) -> None:
        """Write what is buffered. Telemetry never fails a step: errors are reported and dropped."""
        if self.db is None:
            return
        with self._flush_lock:
            with self._lock:
                self._last = time.monotonic()
                self._seal()
                chunks, self._chunks = self._chunks, []
                metric_values, self._metrics = self._metrics, []
                tail = None
                blob_text = None
                if final and self.store is not None and self._head + self._tail_bytes > log_inline_max():
                    marker = f"\n… {self._dropped} bytes truncated …\n" if self._dropped else ""
                    blob_text = "".join(self._kept) + marker + "".join(self._tail)
                    had_tail = bool(self._tail)
                if final and self._tail:
                    parts = [("stdout", "".join(self._tail))]
                    if self._dropped:
                        parts.insert(0, ("marker", f"\n… {self._dropped} bytes truncated …\n"))
                    for stream, text in parts:
                        chunks.append({"seq": self._seq, "stream": stream, "t": time.time(), "text": text})
                        self._seq += 1
                    self._tail.clear()
                    tail = ""  # drop the live tail row
                elif self._tail_dirty:
                    tail = "".join(self._tail)
                self._tail_dirty = False
                if final:
                    self.closed = True
            if blob_text is not None:
                try:
                    self._to_store(blob_text, metric_values, had_tail)
                    return
                except Exception as e:  # fall back to keeping it in the database
                    _log.warning("could not store the log of execution %s: %r", self.exec_id, e)
            if not (chunks or metric_values or tail is not None):
                return
            prev = getattr(_local, "busy", False)
            _local.busy = True  # the database layer may log; don't capture that into this log
            try:
                self.db.write_telemetry(self.exec_id, logs=chunks, tail=tail, metric_values=metric_values)
            except Exception as e:  # pragma: no cover - depends on the backend failing
                _log.warning("could not write telemetry for execution %s: %r", self.exec_id, e)
            finally:
                _local.busy = prev

    def _to_store(self, text: str, metric_values: list, had_tail: bool) -> None:
        import tempfile

        prev = getattr(_local, "busy", False)
        _local.busy = True
        try:
            with tempfile.TemporaryDirectory(prefix="dendrite-log-") as d:
                path = Path(d) / "console.log"
                path.write_text(text, encoding="utf-8")
                ref = self.store.put_path(path)
            if metric_values or had_tail:
                self.db.write_telemetry(self.exec_id, tail="" if had_tail else None, metric_values=metric_values)
            self.db.attach_log(self.exec_id, ref)  # replaces the chunks written while it ran
        finally:
            _local.busy = prev


# -- console capture --------------------------------------------------------------------
def _current() -> Telemetry | None:
    from . import runtime

    if getattr(_local, "busy", False):
        return None
    ctx = runtime._exec.get()
    return getattr(ctx, "telemetry", None) if ctx is not None else None


class _Tee:
    """Stands in for ``sys.stdout``/``sys.stderr``: writes through, and copies into the step's log."""

    def __init__(self, stream: Any, name: str):
        self._dendrite_stream = stream
        self._dendrite_name = name

    def write(self, s: str) -> int:
        n = self._dendrite_stream.write(s)
        t = _current()
        if t is not None and isinstance(s, str) and not (_FD.capturing(t) and _fd_backed(self._dendrite_stream)):
            t.write(self._dendrite_name, s)
        return n

    def writelines(self, lines: Any) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        self._dendrite_stream.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._dendrite_stream, name)


class _Handler(logging.Handler):
    """Copies log records into the executing step's log unless they already print through a tee."""

    def __init__(self) -> None:
        super().__init__(logging.NOTSET)
        self.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        t = _current()
        if t is None or _prints_to_console(record.name, _FD.capturing(t)):
            return
        try:
            t.write("log", self.format(record) + "\n")
        except Exception:  # pragma: no cover
            self.handleError(record)


def _fd_backed(stream: Any) -> bool:
    """Whether writes to ``stream`` end up on file descriptor 1 or 2."""
    try:
        return stream.fileno() in (1, 2)
    except (AttributeError, OSError, ValueError):
        return False


def _prints_to_console(name: str, fd_capturing: bool) -> bool:
    """Whether a record from logger ``name`` is already captured on its way to the console."""
    c: logging.Logger | None = logging.getLogger(name)
    while c is not None:
        for h in c.handlers:
            stream = getattr(h, "stream", None) if isinstance(h, logging.StreamHandler) else None
            if isinstance(stream, _Tee) or (fd_capturing and stream is not None and _fd_backed(stream)):
                return True
        if not c.propagate:
            break
        c = c.parent
    return False


def _fd_capture_enabled() -> bool:
    # A notebook kernel forwards fds itself; Windows consoles don't take dup2 well.
    return os.environ.get("DENDRITE_CAPTURE_FD", "1") != "0" and os.name == "posix" and "ipykernel" not in sys.modules


def _flush_c_stdio() -> None:
    """Push out what C code buffered in stdio, so it lands while the pipes are still in place."""
    try:
        import ctypes

        ctypes.CDLL(None).fflush(None)
    except Exception:  # pragma: no cover - no libc to call
        pass


class _FdSession:
    """Routes file descriptors 1 and 2 through pipes into one step's log (and on to the terminal)."""

    def __init__(self, tel: Telemetry):
        self.tel = tel
        self.saved: dict[int, int] = {}
        self.readers: list[threading.Thread] = []

    def start(self) -> None:
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:  # pragma: no cover
                pass
        _flush_c_stdio()
        try:
            for fd, name in ((1, "stdout"), (2, "stderr")):
                saved = os.dup(fd)
                r, w = os.pipe()
                os.dup2(w, fd)
                os.close(w)
                self.saved[fd] = saved
                t = threading.Thread(target=self._pump, args=(r, saved, name), daemon=True, name=f"dendrite-fd{fd}")
                t.start()
                self.readers.append(t)
        except OSError:
            self.stop()
            raise

    def _pump(self, r: int, out: int, name: str) -> None:
        decode = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while True:
                data = os.read(r, 65536)
                if not data:
                    break
                view = memoryview(data)
                while view:  # the terminal (or whatever fd 1/2 was) still gets everything
                    try:
                        view = view[os.write(out, view) :]
                    except OSError:
                        break
                text = decode.decode(data)
                if text:
                    self.tel.write(name, text)
            rest = decode.decode(b"", final=True)
            if rest:
                self.tel.write(name, rest)
        finally:
            os.close(r)

    def stop(self) -> None:
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:  # pragma: no cover
                pass
        _flush_c_stdio()
        for fd, saved in self.saved.items():
            os.dup2(saved, fd)  # drops the last write end of the pipe: its reader sees EOF
        for t in self.readers:
            t.join(timeout=5)
        for saved in self.saved.values():
            os.close(saved)
        self.saved.clear()
        self.readers.clear()


class _FdCapture:
    """Captures C-level and subprocess output into a step's log while it's the only step running.

    Output on file descriptors can't be told apart between concurrent steps, so while several
    run, only Python-level output (``sys.stdout``, ``logging``, ``dendrite.run``) is captured.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: list[Telemetry] = []
        self._session: _FdSession | None = None

    def enter(self, tel: Telemetry) -> None:
        with self._lock:
            self._active.append(tel)
            self._reconcile()

    def leave(self, tel: Telemetry) -> None:
        with self._lock:
            if tel in self._active:
                self._active.remove(tel)
            self._reconcile()

    def capturing(self, tel: Telemetry) -> bool:
        s = self._session
        return s is not None and s.tel is tel

    def _reconcile(self) -> None:
        want = self._active[0] if len(self._active) == 1 and _fd_capture_enabled() else None
        if self._session is not None and self._session.tel is not want:
            self._session.stop()
            self._session = None
        if want is not None and self._session is None:
            session = _FdSession(want)
            try:
                session.start()
                self._session = session
            except OSError as e:  # pragma: no cover - fds unavailable (closed, sandboxed)
                _log.debug("fd capture unavailable: %r", e)


_FD = _FdCapture()


def step_started(tel: Telemetry) -> None:
    install()
    _FD.enter(tel)


def step_finished(tel: Telemetry) -> None:
    _FD.leave(tel)


_install_lock = threading.Lock()
_handler: _Handler | None = None


def install() -> None:
    """Wrap the current ``sys.stdout``/``sys.stderr`` (again, if something replaced them)."""
    global _handler
    with _install_lock:
        if not isinstance(sys.stdout, _Tee) and sys.stdout is not None:
            sys.stdout = _Tee(sys.stdout, "stdout")
        if not isinstance(sys.stderr, _Tee) and sys.stderr is not None:
            sys.stderr = _Tee(sys.stderr, "stderr")
        if _handler is None:
            _handler = _Handler()
            logging.getLogger().addHandler(_handler)


def run(cmd: Any, *, check: bool = True, **kwargs: Any) -> subprocess.CompletedProcess:
    """Run a subprocess, streaming its stdout and stderr into the step's log (and the terminal).

    Returns a ``CompletedProcess`` whose ``stdout`` holds the combined output; raises
    ``CalledProcessError`` on a non-zero exit unless ``check=False``. Works outside steps too.
    """
    for k in ("stdout", "stderr", "text", "universal_newlines", "capture_output"):
        if k in kwargs:
            raise TypeError(f"dendrite.run() manages {k}; drop it")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs)
    assert proc.stdout is not None
    decode = codecs.getincrementaldecoder("utf-8")("replace")
    out: list[str] = []
    while True:
        data = proc.stdout.read1(8192) if hasattr(proc.stdout, "read1") else proc.stdout.read(8192)
        if not data:
            break
        text = decode.decode(data)
        if text:
            out.append(text)
            sys.stdout.write(text)
            sys.stdout.flush()
    tail = decode.decode(b"", final=True)
    if tail:
        out.append(tail)
        sys.stdout.write(tail)
    code = proc.wait()
    result = subprocess.CompletedProcess(proc.args, code, stdout="".join(out))
    if check and code:
        raise subprocess.CalledProcessError(code, proc.args, output=result.stdout)
    return result


# -- samples ----------------------------------------------------------------------------
def _png(arr: Any) -> bytes:
    """Encode an HxW / HxWx3 / HxWx4 uint8 array as PNG without Pillow."""
    h, w = arr.shape[:2]
    channels = 1 if arr.ndim == 2 else arr.shape[2]
    colour = {1: 0, 3: 2, 4: 6}[channels]
    raw = b"".join(b"\x00" + arr[y].tobytes() for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, colour, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def _as_uint8(arr: Any) -> Any:
    import numpy as np

    a = np.asarray(arr)
    if a.ndim == 3 and a.shape[2] == 1:
        a = a[:, :, 0]
    if a.ndim not in (2, 3) or (a.ndim == 3 and a.shape[2] not in (3, 4)):
        raise ValueError(f"log_image expects HxW, HxWx3 or HxWx4, got shape {a.shape}")
    if a.dtype != np.uint8:
        a = (np.clip(a.astype("float64"), 0.0, 1.0) * 255).round().astype(np.uint8)
    return np.ascontiguousarray(a)


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def image_file(image: Any, dest_dir: Path, stem: str) -> Path:
    """A file for ``image``: an existing image path as is, else a PNG written into ``dest_dir``."""
    if isinstance(image, (str, os.PathLike)):
        p = Path(image)
        if not p.is_file():
            raise FileNotFoundError(p)
        return p
    out = dest_dir / f"{_SAFE.sub('_', stem)}.png"
    if hasattr(image, "save") and callable(image.save):  # a PIL image
        image.save(out, format="PNG")
        return out
    out.write_bytes(_png(_as_uint8(image)))
    return out


def split_key(key: str) -> tuple[str, str]:
    """``title/series`` → (title, series); a key without ``/`` is both."""
    title, sep, series = key.partition("/")
    return (title, series) if sep and series else (key, key)


# -- environment ------------------------------------------------------------------------
_CRED = re.compile(r"(?<=://)[^/@]+@")
_env_cache: dict[tuple[str, str], dict[str, Any]] = {}
_git_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
GIT_TTL = 10.0  # seconds a git state is reused, so a wide fan-out doesn't run git per item
_env_lock = threading.Lock()


def _cmd(args: list[str], cwd: Path) -> str | None:
    try:
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _static_env(root: Path, env_tag: str) -> dict[str, Any]:
    gpus = _cmd(["nvidia-smi", "-L"], root)
    lock = root / "uv.lock"
    packages = sorted(
        {f"{d.metadata['Name']}=={d.version}" for d in metadata.distributions() if d.metadata.get("Name")},
        key=str.lower,
    )
    return {
        "python": {"version": platform.python_version(), "executable": sys.executable},
        "system": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "host": socket.gethostname(),
            "gpus": [g.strip() for g in gpus.splitlines() if g.strip()] if gpus else [],
        },
        "packages": packages,
        "uv_lock": hashlib.sha256(lock.read_bytes()).hexdigest() if lock.is_file() else None,
        "dendrite_env": env_tag or None,
    }


def _git(root: Path) -> dict[str, Any] | None:
    top = _cmd(["git", "rev-parse", "--show-toplevel"], root)
    if not top:
        return None
    commit = (_cmd(["git", "rev-parse", "HEAD"], root) or "").strip() or None
    branch = (_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], root) or "").strip() or None
    remote = (_cmd(["git", "config", "--get", "remote.origin.url"], root) or "").strip() or None
    status = _cmd(["git", "status", "--porcelain", "--untracked-files=no"], root)
    dirty = bool(status and status.strip())
    diff = None
    if dirty and os.environ.get("DENDRITE_CAPTURE_DIFF", "1") != "0":
        diff = _cmd(["git", "diff", "HEAD"], root)
        if diff and len(diff.encode()) > 1024 * 1024:
            diff = diff.encode()[: 1024 * 1024].decode("utf-8", "ignore") + "\n… diff truncated at 1 MiB …\n"
    return {
        "root": top.strip(),
        "commit": commit,
        "branch": branch,
        "remote": _CRED.sub("", remote) if remote else None,
        "dirty": dirty,
        "diff": diff,
    }


def snapshot(root: Path, env_tag: str = "") -> dict[str, Any]:
    """The environment a step runs in. Never records environment variables."""
    key = (str(root), env_tag)
    with _env_lock:
        static = _env_cache.get(key)
        if static is None:
            static = _env_cache[key] = _static_env(root, env_tag)
        hit = _git_cache.get(str(root))
    if hit and time.monotonic() - hit[0] < GIT_TTL:
        git = hit[1]
    else:
        git = _git(root)
        with _env_lock:
            _git_cache[str(root)] = (time.monotonic(), git)
    return {"git": git, **static}


def env_hash(snap: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(snap, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
