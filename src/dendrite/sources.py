"""On-disk status of source snapshots, and guarded write-back of edited source."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from .hashing import sha256_bytes


class SourceConflict(Exception):
    """The file no longer matches what the editor was opened against."""


def disk_status(snap: dict[str, Any]) -> dict[str, Any]:
    """``same`` when the snapshot text occurs exactly once in its file, else ``changed``/``missing``."""
    file = snap.get("file")
    if not file or not Path(file).is_file():
        return {"status": "missing", "file_sha": None}
    data = Path(file).read_bytes()
    text = data.decode("utf-8", errors="replace")
    status = "same" if text.count(snap["source"]) == 1 else "changed"
    return {"status": status, "file_sha": sha256_bytes(data)}


def write_source(snap: dict[str, Any], new_source: str, file_sha: str) -> dict[str, Any]:
    """Replace the snapshot's text in its recorded file with ``new_source``.

    Only the recorded path is ever written, only if its hash still equals ``file_sha`` and the
    snapshot text occurs exactly once; the write is atomic and preserves the file mode.
    """
    file = snap.get("file")
    if not file or not Path(file).is_file():
        raise SourceConflict("the source file no longer exists")
    path = Path(file)
    data = path.read_bytes()
    if sha256_bytes(data) != file_sha:
        raise SourceConflict("the file changed on disk since it was opened")
    text = data.decode("utf-8")
    if text.count(snap["source"]) != 1:
        raise SourceConflict("the function no longer matches the recorded version")
    if not new_source.endswith("\n") and snap["source"].endswith("\n"):
        new_source += "\n"
    updated = text.replace(snap["source"], new_source, 1).encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(updated)
        os.chmod(tmp, path.stat().st_mode & 0o7777)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return {"file_sha": sha256_bytes(updated)}
