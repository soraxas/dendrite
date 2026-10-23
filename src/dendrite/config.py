"""Configuration resolved from arguments or environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    home: Path
    db_url: str
    store_url: str
    # Undeclared-read guard (guard.py): "warn" | "error" | "off", and the directory it watches.
    check_reads: str = "warn"
    project_root: Path = Path(".")
    # Garbage collection skips blobs modified more recently than this (seconds): in-flight uploads.
    gc_grace: float = 3600.0

    @classmethod
    def resolve(
        cls,
        home: str | os.PathLike | None = None,
        db_url: str | None = None,
        store_url: str | None = None,
    ) -> "Settings":
        root = Path(home or os.environ.get("DENDRITE_HOME", ".dendrite")).expanduser().resolve()
        check = os.environ.get("DENDRITE_CHECK_READS", "warn").lower()
        if check not in ("warn", "error", "off"):
            raise ValueError(f"DENDRITE_CHECK_READS must be warn, error or off, not {check!r}")
        grace = os.environ.get("DENDRITE_GC_GRACE", "3600")
        try:
            gc_grace = float(grace)
        except ValueError:
            raise ValueError(f"DENDRITE_GC_GRACE must be a number of seconds, not {grace!r}") from None
        return cls(
            gc_grace=gc_grace,
            check_reads=check,
            project_root=Path.cwd().resolve(),
            home=root,
            db_url=db_url or os.environ.get("DENDRITE_DB_URL") or f"sqlite:///{root / 'lineage.db'}",
            store_url=store_url or os.environ.get("DENDRITE_STORE_URL") or str(root / "store"),
        )
