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

    @classmethod
    def resolve(
        cls,
        home: str | os.PathLike | None = None,
        db_url: str | None = None,
        store_url: str | None = None,
    ) -> "Settings":
        root = Path(home or os.environ.get("DENDRITE_HOME", ".dendrite")).expanduser().resolve()
        return cls(
            home=root,
            db_url=db_url or os.environ.get("DENDRITE_DB_URL") or f"sqlite:///{root / 'lineage.db'}",
            store_url=store_url or os.environ.get("DENDRITE_STORE_URL") or str(root / "store"),
        )
