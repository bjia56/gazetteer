#!/usr/bin/env python3
"""Build the Go tools that ship inside the platform wheels, for one target.

Writes gazetteer-gohelper and gopls (statically linked, so one build serves glibc and musl) plus the licenses
of every Go module linked into them. Used by the wheel build (hatch_build.py), by CI and by hand:

    python scripts/build_go.py                       # host platform into src/gazetteer/_bin
    python scripts/build_go.py --goos linux --goarch arm64 --out /tmp/bin

Needs a Go toolchain. gopls requires a newer Go than the helper; with GOTOOLCHAIN=auto (the default) the go
command fetches it.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GO = ROOT / "go"
LICENSE_NAMES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "COPYING.md", "NOTICE", "UNLICENSE")
GOPLS_PACKAGE = "golang.org/x/tools/gopls"


def go(args: list[str], cwd: Path, env: dict[str, str]) -> str:
    proc = subprocess.run(["go", *args], cwd=cwd, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f"go {' '.join(args)} (in {cwd}) failed:\n{proc.stderr.strip()}")
    return proc.stdout


def linked_modules(cwd: Path, package: str, env: dict[str, str]) -> dict[str, tuple[str, Path]]:
    """Every module with code linked into ``package`` for this target: path -> (version, directory)."""
    out = go(
        ["list", "-deps", "-f", "{{if .Module}}{{.Module.Path}}\t{{.Module.Version}}\t{{.Module.Dir}}{{end}}", package],
        cwd,
        env,
    )
    found: dict[str, tuple[str, Path]] = {}
    for line in out.splitlines():
        path, version, directory = line.split("\t")
        if directory:
            found[path] = (version, Path(directory))
    return found


def collect_licenses(out: Path, env: dict[str, str]) -> None:
    licenses = out / "licenses"
    shutil.rmtree(licenses, ignore_errors=True)
    licenses.mkdir(parents=True)
    notice = ["Go modules linked into the bundled binaries (gopls and gazetteer-gohelper):", ""]
    goroot = Path(go(["env", "GOROOT"], ROOT, env).strip())
    shutil.copyfile(goroot / "LICENSE", licenses / "go-standard-library.txt")
    notice.append(f"go standard library {go(['env', 'GOVERSION'], ROOT, env).strip()}  (go-standard-library.txt)")
    for path, (version, directory) in sorted(linked_modules(GO / "gopls", GOPLS_PACKAGE, env).items()):
        files = [directory / n for n in LICENSE_NAMES if (directory / n).is_file()]
        if not files:
            sys.exit(f"no license file found for {path} {version} in {directory}")
        name = path.replace("/", "_")
        for i, f in enumerate(files):
            shutil.copyfile(f, licenses / f"{name}{'' if i == 0 else f'.{i}'}.txt")
        notice.append(f"{path} {version}  ({name}.txt)")
    (licenses / "NOTICE.txt").write_text("\n".join(notice) + "\n")


def main() -> None:
    host = go(["env", "GOOS", "GOARCH"], ROOT, os.environ.copy()).split()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--goos", default=host[0])
    ap.add_argument("--goarch", default=host[1])
    ap.add_argument("--out", type=Path, default=ROOT / "src" / "gazetteer" / "_bin")
    ap.add_argument("--version", default="dev", help="version string compiled into the helper")
    ap.add_argument("--only", choices=("helper", "gopls"), help="build one tool (no license collection)")
    args = ap.parse_args()

    env = {**os.environ, "CGO_ENABLED": "0", "GOOS": args.goos, "GOARCH": args.goarch}
    exe = ".exe" if args.goos == "windows" else ""
    args.out.mkdir(parents=True, exist_ok=True)
    flags = ["-trimpath", "-ldflags"]
    if args.only != "gopls":
        print(f"gazetteer-gohelper for {args.goos}/{args.goarch}", flush=True)
        go(
            [
                "build",
                *flags,
                f"-s -w -X main.version={args.version}",
                "-o",
                str(args.out / f"gazetteer-gohelper{exe}"),
                ".",
            ],
            GO / "helper",
            env,
        )
    if args.only != "helper":
        print(f"gopls for {args.goos}/{args.goarch}", flush=True)
        go(["build", *flags, "-s -w", "-o", str(args.out / f"gopls{exe}"), GOPLS_PACKAGE], GO / "gopls", env)
    if args.only is None:
        collect_licenses(args.out, env)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
