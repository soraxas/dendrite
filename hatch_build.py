"""Refuse to package a release without the built web UI (editable installs are exempt)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

UI_INDEX = Path("src/dendrite/ui/dist/index.html")
SKIP_ENV = "DENDRITE_SKIP_UI_CHECK"


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if version == "editable" or self.target_name not in {"sdist", "wheel"}:
            return
        if os.environ.get(SKIP_ENV, "").lower() in {"1", "true", "yes"}:
            return
        if not (Path(self.root) / UI_INDEX).is_file():
            raise RuntimeError(
                f"{UI_INDEX} is missing: run scripts/build-ui.sh before building a "
                f"{self.target_name}, or set {SKIP_ENV}=1 to package without the React UI"
            )
