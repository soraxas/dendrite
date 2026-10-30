"""``server.toml``: how a hosted dendrite server is configured.

```toml
base_url = "https://lineage.example.com"   # what browsers and clients use to reach it
database_url = "postgresql+psycopg://dendrite:${PGPASSWORD}@db:5432/dendrite"
store_url = "s3://team-lineage/dendrite"   # each project gets <store_url>/<project>
home = "/var/lib/dendrite"                  # server-side cache and scratch
admins = ["you@example.com"]                # admins in every project
allowed_domains = ["example.com"]           # new sign-ins from these join every project as viewers
verify_uploads = "checksum"                 # or "hash": re-hash uploads the store didn't verify
store_enforces_sha256 = true                # false for stores that accept but don't check checksums
min_client = "0.1.0"
# gc_grace = 3600                           # seconds GC spares fresh blobs (default: DENDRITE_GC_GRACE or 1 h)
# max_open_projects = 16                    # projects kept open (mind Postgres's max_connections)

[store_options]                             # fsspec storage options, e.g. for MinIO
endpoint_url = "http://minio:9000"

[auth]
provider = "github"                         # "github", "oidc" (with `issuer`) or "dev" (localhost only)
client_id = "…"
client_secret = "${GITHUB_CLIENT_SECRET}"
```

``${NAME}`` is replaced by the environment variable ``NAME``, so secrets stay out of the file.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand(v: Any) -> Any:
    if isinstance(v, str):

        def env(m: re.Match) -> str:
            if m.group(1) not in os.environ:
                raise ValueError(f"server.toml refers to ${{{m.group(1)}}}, which isn't set")
            return os.environ[m.group(1)]

        return _VAR.sub(env, v)
    if isinstance(v, list):
        return [_expand(x) for x in v]
    if isinstance(v, dict):
        return {k: _expand(x) for k, x in v.items()}
    return v


@dataclass
class AuthSettings:
    provider: str = "dev"
    client_id: str | None = None
    client_secret: str | None = None
    issuer: str | None = None  # OIDC: discovery at <issuer>/.well-known/openid-configuration
    scopes: list[str] = field(default_factory=lambda: ["openid", "email", "profile"])


@dataclass
class ServerSettings:
    base_url: str
    database_url: str
    store_url: str
    home: Path = Path(".dendrite-server")
    admins: list[str] = field(default_factory=list)
    allowed_domains: list[str] = field(default_factory=list)
    verify_uploads: str = "checksum"
    store_enforces_sha256: bool = True
    min_client: str = "0.1.0"
    store_options: dict[str, Any] = field(default_factory=dict)
    auth: AuthSettings = field(default_factory=AuthSettings)
    # Uploads above this use S3 multipart (S3 caps a single PUT at 5 GB); parts are this big.
    multipart_threshold: int = 4 * 1024**3
    part_size: int = 64 * 1024**2
    url_seconds: int = 15 * 60
    # Idle connections stay open this long; clients drop theirs sooner (remote.KEEPALIVE).
    keep_alive_seconds: int = 30
    # Projects kept open; the least recently used close. Each holds up to 5 Postgres connections
    # (2 idle), so max_open_projects * 5 + 15 should stay under max_connections (100 by default);
    # `dendrite server check` reports it.
    max_open_projects: int = 16
    # Garbage collection skips blobs written within this many seconds (in-flight uploads).
    # Unset: DENDRITE_GC_GRACE, else an hour.
    gc_grace: float | None = None

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self.store_url = self.store_url.rstrip("/")
        self.home = Path(self.home).expanduser().resolve()
        if not self.database_url.startswith("postgresql"):
            raise ValueError("a dendrite server needs Postgres (database_url = postgresql+psycopg://…)")
        if self.verify_uploads not in ("checksum", "hash"):
            raise ValueError("verify_uploads must be 'checksum' or 'hash'")
        if isinstance(self.auth, dict):
            self.auth = AuthSettings(**self.auth)
        if self.auth.provider not in ("github", "oidc", "dev"):
            raise ValueError("auth.provider must be 'github', 'oidc' or 'dev'")
        if self.auth.provider == "oidc" and not self.auth.issuer:
            raise ValueError("auth.provider = 'oidc' needs auth.issuer")
        if self.auth.provider == "dev" and not self.loopback:
            raise ValueError("auth.provider = 'dev' signs anyone in by email; it only runs on localhost")

    @property
    def loopback(self) -> bool:
        return (urlparse(self.base_url).hostname or "") in ("localhost", "127.0.0.1", "::1")

    @property
    def secure_cookies(self) -> bool:
        return self.base_url.startswith("https://")

    def project_store(self, project: str) -> str:
        return f"{self.store_url}/{project}"

    @classmethod
    def load(cls, path: str | os.PathLike) -> ServerSettings:
        with open(path, "rb") as f:
            raw = _expand(tomllib.load(f))
        base = Path(path).resolve().parent
        if "home" in raw and not Path(raw["home"]).is_absolute():
            raw["home"] = str(base / raw["home"])
        if "://" not in raw.get("store_url", "://") and not Path(raw["store_url"]).is_absolute():
            raw["store_url"] = str(base / raw["store_url"])
        return cls(**raw)
