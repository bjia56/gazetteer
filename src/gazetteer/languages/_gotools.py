"""Finding and running the tools the Go plugin needs: the parser helper and gopls.

Lookup order for each tool: an environment variable, the copy bundled in the platform wheel
(``gazetteer/_bin``), then ``PATH``. In a source checkout the helper is also built from ``go/helper`` on demand.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import LanguageToolError

BIN_DIR = Path(__file__).resolve().parent.parent / "_bin"
CHECKOUT_GO = Path(__file__).resolve().parents[3] / "go"
HELPER = "gazetteer-gohelper"
GOPLS = "gopls"
HELPER_ENV = "GAZETTEER_GOHELPER"
GOPLS_ENV = "GAZETTEER_GOPLS"
BATCH = 200  # files per helper call, to stay well inside command line limits


def _exe(name: str) -> str:
    return name + (".exe" if os.name == "nt" else "")


def find(name: str, env: str) -> Path | None:
    if given := os.environ.get(env):
        path = Path(given)
        if not path.is_file():
            raise LanguageToolError(f"{env}={given} is not a file")
        return path
    bundled = BIN_DIR / _exe(name)
    if bundled.is_file():
        return bundled
    on_path = shutil.which(name)
    return Path(on_path) if on_path else None


def _build_helper() -> Path | None:
    """Build the helper from a source checkout; None when this is not one or there is no Go toolchain."""
    src = CHECKOUT_GO / "helper"
    go = shutil.which("go")
    if not (src / "go.mod").is_file() or go is None:
        return None
    out = CHECKOUT_GO / "bin" / _exe(HELPER)
    newest = max(p.stat().st_mtime for p in [*src.glob("*.go"), src / "go.mod"])
    if not out.is_file() or out.stat().st_mtime < newest:
        proc = subprocess.run([go, "build", "-o", str(out), "."], cwd=src, capture_output=True, text=True)
        if proc.returncode != 0:
            raise LanguageToolError(f"building {HELPER} failed:\n{proc.stderr.strip()}")
    return out


def helper_path() -> Path:
    found = find(HELPER, HELPER_ENV) or _build_helper()
    if found is None:
        raise LanguageToolError(
            f"{HELPER} not found. Install a gazetteer wheel for your platform (it bundles the helper), "
            f"put {HELPER} on PATH, or set {HELPER_ENV}; with a Go toolchain it builds from a source checkout."
        )
    return found


def gopls_path() -> Path | None:
    return find(GOPLS, GOPLS_ENV)


def require_lsp() -> None:
    if gopls_path() is None:
        raise LanguageToolError(
            f"{GOPLS} not found. Install a gazetteer wheel for your platform (it bundles gopls), put gopls on PATH, "
            f"or set {GOPLS_ENV}. Use --no-lsp to build without the call graph."
        )
    if shutil.which("go") is None:
        raise LanguageToolError(
            "gopls needs the Go toolchain to load packages, and `go` is not on PATH. "
            "Install Go, or use --no-lsp to build without the call graph."
        )


def run_helper(*args: str | Path) -> Any:
    proc = subprocess.run([str(helper_path()), *map(str, args)], capture_output=True)
    if proc.returncode != 0:
        raise LanguageToolError(f"{HELPER} {args[0]} failed: {proc.stderr.decode(errors='replace').strip()}")
    return json.loads(proc.stdout)
