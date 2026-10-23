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
        return cls(
            check_reads=check,
            project_root=Path.cwd().resolve(),
            home=root,
            db_url=db_url or os.environ.get("DENDRITE_DB_URL") or f"sqlite:///{root / 'lineage.db'}",
            store_url=store_url or os.environ.get("DENDRITE_STORE_URL") or str(root / "store"),
        )
