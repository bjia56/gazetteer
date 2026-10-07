"""Command line: ``gazetteer build`` writes the page."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .build import BuildOptions, build, build_shard, parse_server
from .languages import LanguageToolError, language_names
from .layout import Layout, LayoutError
from .pack import pack
from .units import CONFIG_NAME, Config, ConfigError, find_config, load_config


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="gazetteer", description="Static code docs generator for Python repositories.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build data.json and a single-file index.html")
    b.add_argument("root", type=Path, help="repository checkout to document")
    b.add_argument("--out", type=Path, default=Path("gazetteer-out"), help="output directory")
    b.add_argument("--name", help="project name shown on the page (default: directory name)")
    b.add_argument("--git-dir", type=Path, help="repository for git history (default: root)")
    b.add_argument("--lang", choices=language_names(), help="language of the code (default: python)")
    b.add_argument(
        "--config",
        type=Path,
        help=f"units file for a monorepo (default: {CONFIG_NAME} in root, if present); "
        "the layout options below then belong in the file",
    )
    b.add_argument(
        "--server",
        help="language server command with call hierarchy (default: the language's own, e.g. pyright for python)",
    )
    b.add_argument("--no-lsp", action="store_true", help="skip the call graph (AST, docs and git only)")
    b.add_argument("--workers", type=int, default=8, help="parallel language-server queries")
    b.add_argument("--pkg-dir", help="package directory to document (default: detected)")
    b.add_argument("--module-root", help="directory module names are relative to (default: detected)")
    b.add_argument("--import-prefix", help="top-level package name (default: detected)")
    b.add_argument("--tests", action="append", help="test directory, repeatable (default: tests/, test/)")
    b.add_argument("--skip", action="append", help="path substring to leave out, repeatable")
    b.add_argument("--docs-dir", help="directory of markdown docs (default: docs)")
    b.add_argument(
        "--tests-by-name",
        type=Path,
        help="tree holding the tests; link them by name instead of through the language server",
    )
    b.add_argument("--shard", help="i/N: query one shard into --facts-dir and exit")
    b.add_argument("--facts-dir", type=Path, help="shard facts: written with --shard, merged otherwise")
    b.add_argument("--prev-data", type=Path, help="data.json of the previous build, for an incremental build")
    b.add_argument("--prev-root", type=Path, help="tree that --prev-data was built from")

    return ap


LAYOUT_FLAGS = ("lang", "pkg_dir", "module_root", "import_prefix", "tests", "skip", "docs_dir", "server")


def _layout(args: argparse.Namespace) -> Layout:
    return Layout.resolve(
        args.root,
        args.lang or "python",
        pkg_dir=args.pkg_dir,
        module_root=args.module_root,
        import_prefix=args.import_prefix,
        tests=tuple(args.tests) if args.tests is not None else None,
        skip=tuple(args.skip) if args.skip is not None else None,
        docs_dir=args.docs_dir,
    )


def _config(args: argparse.Namespace) -> Config | None:
    file = args.config or find_config(args.root)
    if file is None:
        return None
    given = [f"--{f.replace('_', '-')}" for f in LAYOUT_FLAGS if getattr(args, f) is not None]
    if given:
        raise ConfigError(f"{', '.join(given)} cannot be combined with {file}; set them per unit in the file")
    return load_config(file, args.root)


def _run_build(args: argparse.Namespace) -> int:
    try:
        config = _config(args)
        layout = None if config else _layout(args)
    except (LayoutError, ConfigError) as e:
        print(f"gazetteer: {e}", file=sys.stderr)
        return 2
    opts = BuildOptions(
        root=args.root,
        layout=layout,
        units=config.units if config else (),
        server=parse_server(args.server) if args.server else None,
        git_dir=args.git_dir,
        name=args.name or (config.name if config else None),
        use_lsp=not args.no_lsp,
        workers=args.workers,
        tests_by_name=args.tests_by_name,
        facts_dir=args.facts_dir,
        prev_data=args.prev_data,
        prev_root=args.prev_root,
    )
    if args.shard:
        if not args.facts_dir:
            print("gazetteer: --shard needs --facts-dir", file=sys.stderr)
            return 2
        i, n = (int(x) for x in args.shard.split("/"))
        try:
            print("shard done", build_shard(opts, i, n))
        except LanguageToolError as e:
            print(f"gazetteer: {e}", file=sys.stderr)
            return 2
        return 0
    if bool(args.prev_data) != bool(args.prev_root):
        print("gazetteer: --prev-data and --prev-root go together", file=sys.stderr)
        return 2
    try:
        data = build(opts)
    except LanguageToolError as e:
        print(f"gazetteer: {e}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    data_path = args.out / "data.json"
    data_path.write_text(json.dumps(data, separators=(",", ":")))
    size = pack(data_path, args.out / "index.html")
    st = data["stats"]
    units = f"{len(config.units)} units, " if config else ""
    print(
        f"{units}{st['modules']} modules, {st['symbols']} symbols in {st['build_seconds']}s; "
        f"wrote {args.out / 'index.html'} ({size / 1e6:.2f} MB)"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return _run_build(args)


if __name__ == "__main__":
    raise SystemExit(main())
