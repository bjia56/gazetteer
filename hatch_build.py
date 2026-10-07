"""Build hook: bundle the Go tools into platform wheels.

A wheel is built for one platform when GAZETTEER_GO_TARGET (a Go ``os/arch``, e.g. ``linux/arm64``) and
GAZETTEER_WHEEL_PLATFORM (the wheel platform tag, e.g. ``manylinux_2_17_aarch64``) are set: gazetteer-gohelper
and gopls are compiled for that target into ``src/gazetteer/_bin`` and the wheel is tagged for the platform.
Without them (a plain ``pip install`` from a checkout or the sdist) the wheel is pure Python, and the
plugin finds the tools on PATH or builds the helper itself in a checkout.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class GoToolsHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    @property
    def bin_dir(self) -> Path:
        return Path(self.root) / "src" / "gazetteer" / "_bin"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        target = os.environ.get("GAZETTEER_GO_TARGET")
        if self.target_name != "wheel" or not target:
            return
        platform = os.environ.get("GAZETTEER_WHEEL_PLATFORM")
        if not platform:
            raise RuntimeError("GAZETTEER_GO_TARGET needs GAZETTEER_WHEEL_PLATFORM, the wheel platform tag")
        goos, _, goarch = target.partition("/")
        shutil.rmtree(self.bin_dir, ignore_errors=True)
        subprocess.run(
            [
                sys.executable,
                str(Path(self.root) / "scripts" / "build_go.py"),
                "--goos",
                goos,
                "--goarch",
                goarch,
                "--out",
                str(self.bin_dir),
                "--version",
                self.metadata.version,
            ],
            check=True,
        )
        build_data["pure_python"] = False
        build_data["infer_tag"] = False
        build_data["tag"] = f"py3-none-{platform}"

    def finalize(self, version: str, build_data: dict[str, Any], artifact_path: str) -> None:
        if self.target_name == "wheel" and os.environ.get("GAZETTEER_GO_TARGET"):
            shutil.rmtree(self.bin_dir, ignore_errors=True)
