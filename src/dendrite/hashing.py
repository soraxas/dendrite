"""Canonical serialisation and content hashing."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
from typing import Any, BinaryIO

CHUNK = 1 << 20


def _default(o: Any) -> Any:
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return dataclasses.asdict(o)
    if hasattr(o, "model_dump"):
        return o.model_dump()
    raise TypeError(
        f"value {o!r} of type {type(o).__name__} is not JSON-serialisable and cannot be "
        "part of an execution signature; pass it as an artifact instead"
    )


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_default)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_json(obj: Any) -> str:
    return sha256_bytes(canonical_json(obj).encode())


def sha256_stream(f: BinaryIO) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    while chunk := f.read(CHUNK):
        h.update(chunk)
        size += len(chunk)
    return h.hexdigest(), size


def sha256_file(path: Path) -> tuple[str, int]:
    with open(path, "rb") as f:
        return sha256_stream(f)
