"""Configuration resolved from arguments, environment variables or a project's ``dendrite.toml``.

Which backend a ``Lab`` uses, first match wins:

1. explicit ``Lab(db_url=…, store_url=…)`` (direct) or ``Lab(server=…)`` (hosted)
2. ``DENDRITE_DB_URL`` / ``DENDRITE_STORE_URL`` (direct)
3. ``DENDRITE_SERVER`` with ``DENDRITE_PROJECT`` (hosted)
4. the nearest ``dendrite.toml`` above the working directory, ``[server] url`` and ``project``
5. local: SQLite and a store under ``DENDRITE_HOME``

``dendrite.toml`` never holds credentials, so it is safe to commit.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_FILE = "dendrite.toml"


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
    # Hosted mode: the dendrite server and project, and where that choice came from.
    server: str | None = None
    project: str | None = None
    source: str = "local"

    @property
    def hosted(self) -> bool:
        return self.server is not None

    @classmethod
    def resolve(
        cls,
        home: str | os.PathLike | None = None,
        db_url: str | None = None,
        store_url: str | None = None,
        server: str | None = None,
        project: str | None = None,
    ) -> Settings:
        root = Path(home or os.environ.get("DENDRITE_HOME", ".dendrite")).expanduser().resolve()
        check = os.environ.get("DENDRITE_CHECK_READS", "warn").lower()
        if check not in ("warn", "error", "off"):
            raise ValueError(f"DENDRITE_CHECK_READS must be warn, error or off, not {check!r}")
        grace = os.environ.get("DENDRITE_GC_GRACE", "3600")
        try:
            gc_grace = float(grace)
        except ValueError:
            raise ValueError(f"DENDRITE_GC_GRACE must be a number of seconds, not {grace!r}") from None
        local = {
            "gc_grace": gc_grace,
            "check_reads": check,
            "project_root": Path.cwd().resolve(),
            "home": root,
            "db_url": f"sqlite:///{root / 'lineage.db'}",
            "store_url": str(root / "store"),
        }
        if db_url or store_url:
            return cls(**{**local, **_direct(db_url, store_url)}, source="arguments")
        if server:
            return cls(
                **local, server=_url(server), project=_need_project(project, "Lab(server=…)"), source="arguments"
            )
        env_db, env_store = os.environ.get("DENDRITE_DB_URL"), os.environ.get("DENDRITE_STORE_URL")
        if env_db or env_store:
            return cls(**{**local, **_direct(env_db, env_store)}, source="environment")
        if os.environ.get("DENDRITE_SERVER"):
            return cls(
                **local,
                server=_url(os.environ["DENDRITE_SERVER"]),
                project=_need_project(project or os.environ.get("DENDRITE_PROJECT"), "DENDRITE_SERVER"),
                source="environment",
            )
        found = find_project_file()
        if found is not None:
            path, conf = found
            srv = conf.get("server") or {}
            if srv.get("url"):
                return cls(
                    **local,
                    server=_url(srv["url"]),
                    project=_need_project(project or srv.get("project"), str(path)),
                    source=str(path),
                )
        return cls(**local)


def _direct(db_url: str | None, store_url: str | None) -> dict[str, str]:
    return {k: v for k, v in (("db_url", db_url), ("store_url", store_url)) if v}


def _url(server: str) -> str:
    server = server.strip().rstrip("/")
    if "://" not in server:
        raise ValueError(f"server must be a URL such as https://lineage.example.com, not {server!r}")
    return server


def _need_project(project: str | None, where: str) -> str:
    if not project:
        raise ValueError(f"{where} names a server but no project; set DENDRITE_PROJECT or `project` in {PROJECT_FILE}")
    return project


def find_project_file(start: Path | None = None) -> tuple[Path, dict[str, Any]] | None:
    """The nearest ``dendrite.toml`` at or above ``start`` (the working directory), parsed."""
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        path = d / PROJECT_FILE
        if path.is_file():
            with open(path, "rb") as f:
                return path, tomllib.load(f)
    return None


def write_project_file(directory: Path, server: str, project: str) -> Path:
    """Write ``dendrite.toml`` naming the team server and project (no credentials)."""
    path = directory / PROJECT_FILE
    path.write_text(
        "# Which dendrite server this repo's pipelines record to. Safe to commit: no credentials.\n"
        "# Sign in with `dendrite login`; CI sets DENDRITE_TOKEN.\n"
        f'[server]\nurl = "{_url(server)}"\nproject = "{project}"\n'
    )
    return path
