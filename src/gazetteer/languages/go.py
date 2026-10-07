"""Go: layout detection, extraction through the parser helper, and test parsing.

A Go package is a directory of files, and documentation is per file here as it is for Python: each source file
is a module (id: its path without ``.go``) and its package, the directory, is the module's ``package`` field.
Symbol ids are ``<module id>.<Type.Method or Name>``. Types are ``class`` symbols (with ``decl`` saying
struct, interface, alias or type) and their methods, interface methods included, belong to them.
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

from ..layout import Layout, LayoutError
from ..model import Module, Symbol
from . import TestTable, register
from ._gotools import BATCH, gopls_path, helper_path, require_lsp, run_helper
from .common import link_subclasses

SNIPPET_MAX_LINES = 40
DEFAULT_SKIP = ("/vendor/", "/testdata/", "/node_modules/", ".pb.go", ".pb.gw.go")
MODULE_LINE = re.compile(r'^module\s+(?:"([^"]+)"|(\S+))', re.MULTILINE)


def module_path(go_mod: Path) -> str:
    m = MODULE_LINE.search(go_mod.read_text(errors="replace"))
    if not m:
        raise LayoutError(f"no module line in {go_mod}")
    return m.group(1) or m.group(2)


def _ignored_dir(path: Path) -> bool:
    """Directories the go tool does not descend into, and nested modules (units of their own)."""
    return path.name.startswith((".", "_")) or path.name in ("testdata", "vendor") or (path / "go.mod").exists()


def module_id(rel: str, layout: Layout) -> str:
    path = PurePosixPath(rel).relative_to(PurePosixPath(layout.module_root))
    return path.with_suffix("").as_posix()


def _new_symbol(raw: dict[str, Any], sid: str, mid: str, rel: str) -> Symbol:
    sym: Symbol = {
        "id": sid,
        "kind": raw["kind"],
        "name": raw["name"],
        "qual": raw["qual"],
        "module": mid,
        "file": rel,
        "line": raw["line"],
        "end": raw["end"],
        "col": raw["col"],
        "doc": raw["doc"],
        "h": raw["h"],
        "decorators": [],
        "private": raw["private"],
        "code": raw["code"],
        "sig": raw["sig"],
        "calls_in": [],
        "calls_in_x": [],
        "calls_out": [],
        "calls_out_x": [],
        "tests": {},
    }
    if raw["kind"] == "class":
        sym.update(decl=raw.get("decl", ""), bases=raw.get("bases", []), methods=[], subclasses=[])
    else:
        sym["class"] = None
    return sym


def assemble(files: list[dict[str, Any]], layout: Layout) -> tuple[dict[str, Module], dict[str, Symbol]]:
    """Turn the helper's per-file records into modules and symbols, linking methods, imports and subclasses."""
    modules: dict[str, Module] = {}
    symbols: dict[str, Symbol] = {}
    type_ids: dict[tuple[str, str], str] = {}  # (package dir, type name) -> symbol id
    methods: list[tuple[Symbol, str, str]] = []
    defs: dict[tuple[str, str], str] = {}  # (package dir, exported name) -> module id
    for f in sorted(files, key=lambda f: f["path"]):
        mid = module_id(f["path"], layout)
        mod: Module = {
            "id": mid,
            "path": f["path"],
            "loc": f["loc"],
            "doc": f["doc"],
            "symbols": [],
            "consts": f["consts"],
            "imports": [],
            "imported_by": [],
            "tests": {},
            "docs": [],
            "git": None,
            "cochange": [],
            "package": f["dir"],
        }
        modules[mid] = mod
        for name in f["defs"]:
            defs.setdefault((f["dir"], name), mid)
        for raw in f["symbols"]:
            sid = f"{mid}.{raw['qual']}"
            n = 1
            while sid in symbols:  # several init functions in a file
                n += 1
                sid = f"{mid}.{raw['qual']}#{n}"
            sym = _new_symbol(raw, sid, mid, f["path"])
            symbols[sid] = sym
            mod["symbols"].append(sid)
            if raw["kind"] == "class":
                type_ids.setdefault((f["dir"], raw["name"]), sid)
            elif raw["kind"] == "method":
                methods.append((sym, f["dir"], raw["class"]))
    for sym, dirname, type_name in methods:  # the type may be declared in another file of the package
        owner = type_ids.get((dirname, type_name))
        if owner:
            sym["class"] = owner
            symbols[owner]["methods"].append(sym["id"])
    for f in files:
        mid = module_id(f["path"], layout)
        found = {defs[(d, n)] for d, names in f["uses"].items() for n in names if (d, n) in defs}
        modules[mid]["imports"] = sorted(found - {mid})
        for target in modules[mid]["imports"]:
            modules[target]["imported_by"].append(mid)
    link_subclasses(symbols)
    return modules, symbols


class GoLanguage:
    name = "go"
    file_exts: tuple[str, ...] = (".go",)
    index_files: tuple[str, ...] = ()
    default_skip: tuple[str, ...] = DEFAULT_SKIP

    @property
    def default_server(self) -> tuple[str, ...]:
        return (str(gopls_path() or "gopls"),)

    def require(self, lsp: bool) -> None:
        helper_path()
        if lsp:
            require_lsp()

    def is_test_path(self, rel: str) -> bool:
        return rel.endswith("_test.go")

    def detect(self, root: Path) -> Layout:
        """A Go unit is a directory with a go.mod; its module path is the import prefix."""
        root = Path(root)
        if not (root / "go.mod").is_file():
            raise LayoutError(
                f"no go.mod found in {root}; point the unit's path at the directory that has one "
                "(a repository with several Go modules is several units)"
            )
        return Layout(
            pkg_dir=".",
            module_root=".",
            import_prefix=module_path(root / "go.mod"),
            tests=(),
            skip=self.default_skip,
            lang="go",
        )

    def extract(self, root: Path, layout: Layout) -> tuple[dict[str, Module], dict[str, Symbol]]:
        prefix = layout.import_prefix or module_path(root / "go.mod")
        data = run_helper(
            "extract",
            "--root",
            root,
            "--dir",
            layout.pkg_dir,
            "--module",
            prefix,
            "--snippet-lines",
            str(SNIPPET_MAX_LINES),
            *[a for s in layout.skip for a in ("--skip", s)],
        )
        return assemble(data["files"], layout)

    def test_files(self, root: Path, layout: Layout) -> list[str]:
        found: list[str] = []
        start = root / layout.pkg_dir
        for dirpath, dirnames, filenames in os.walk(start):
            here = Path(dirpath)
            dirnames[:] = sorted(
                d
                for d in dirnames
                if not d.startswith((".", "_"))
                and d not in ("testdata", "vendor")
                and not (here / d / "go.mod").exists()
            )
            for name in sorted(filenames):
                rel = (here / name).relative_to(root).as_posix()
                if name.endswith("_test.go") and not layout.is_skipped(rel):
                    found.append(rel)
        return found

    def test_uses(self, root: Path, rels: list[str]) -> dict[str, list[tuple[str, set[str]]] | None]:
        out: dict[str, list[tuple[str, set[str]]] | None] = {}
        for i in range(0, len(rels), BATCH):
            res = run_helper("tests", "--root", root, *rels[i : i + BATCH])
            for rel, r in res.items():
                out[rel] = [(t["name"], set(t["uses"])) for t in r["tests"]] if r["ok"] else None
        return out

    def test_table(self, path: Path) -> TestTable:
        rel = path.name
        r = run_helper("table", "--root", path.parent, rel)[rel]
        if not r["ok"]:
            return {}, "syntax-error"
        return {k: (v["h"], set(v["uses"]), v["name"]) for k, v in r["funcs"].items()}, r["residual"]

    def coarse_names(self, path: Path) -> set[str]:
        return set(run_helper("names", "--root", path.parent, path.name)[path.name]["names"])


register(GoLanguage())
