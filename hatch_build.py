"""Build hook: package the web UI with dendrite when it can be had.

A wheel built from a checkout or a git URL needs the bundle in ``src/dendrite/ui/dist``.
When it's missing and ``npm`` is available, the hook builds it (``ui/``). Otherwise the
package is built without it: pipelines, the CLI and hosted mode work, and ``dendrite ui``
serves a page explaining how to build the UI. Laptops that record to a team server don't
need it, because the server serves the UI.

``DENDRITE_REQUIRE_UI=1`` (releases) fails instead of packaging without the UI.
``DENDRITE_SKIP_UI_CHECK=1`` packages without the UI and doesn't try ``npm``.
Editable installs are always exempt.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

UI_INDEX = Path("src/dendrite/ui/dist/index.html")
SKIP_ENV = "DENDRITE_SKIP_UI_CHECK"
REQUIRE_ENV = "DENDRITE_REQUIRE_UI"


def _on(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes"}


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if version == "editable" or self.target_name not in {"sdist", "wheel"}:
            return
        root = Path(self.root)
        if (root / UI_INDEX).is_file() or _on(SKIP_ENV):
            return
        ui, npm = root / "ui", shutil.which("npm")
        if (ui / "package.json").is_file() and npm:
            print("dendrite: building the web UI (npm ci && npm run build)…", file=sys.stderr)
            try:
                subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=ui, check=True, stdout=sys.stderr)
                subprocess.run([npm, "run", "build"], cwd=ui, check=True, stdout=sys.stderr)
            except subprocess.CalledProcessError as e:
                print(f"dendrite: building the web UI failed ({e})", file=sys.stderr)
        if (root / UI_INDEX).is_file():
            return
        if _on(REQUIRE_ENV):
            raise RuntimeError(
                f"{UI_INDEX} is missing: run scripts/build-ui.sh before building a {self.target_name} "
                f"(or unset {REQUIRE_ENV} to package the API without a UI)"
            )
        print(
            f"dendrite: packaging without the web UI ({UI_INDEX} is missing and npm couldn't build it). "
            "Pipelines, the CLI and hosted mode work; `dendrite ui` needs scripts/build-ui.sh.",
            file=sys.stderr,
        )
