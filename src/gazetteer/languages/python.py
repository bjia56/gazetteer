"""Python: layout detection, ``ast`` extraction and test parsing."""

from __future__ import annotations

import ast
import collections
import hashlib
from pathlib import Path
from typing import Any

from ..layout import DEFAULT_SKIP, TEST_DIR_NAMES, Layout, LayoutError
from ..lsp import DEFAULT_SERVER
from ..model import Module, Symbol
from . import TestTable, register

SNIPPET_MAX_LINES = 40
FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


def unparse(node: ast.AST | None) -> str:
    try:
        return ast.unparse(node) if node is not None else ""
    except Exception:  # noqa: BLE001 - unparse can fail on exotic nodes; a blank is fine for display
        return ""


def signature(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    ret = unparse(fn.returns)
    prefix = "async " if isinstance(fn, ast.AsyncFunctionDef) else ""
    return f"{prefix}{fn.name}({unparse(fn.args)})" + (f" -> {ret}" if ret else "")


def name_column(lines: list[str], node: ast.AST, name: str) -> int:
    """Column of the identifier in a ``def``/``class`` line, which language servers need as the position."""
    line = lines[node.lineno - 1]  # type: ignore[attr-defined]
    i = line.find(name, node.col_offset)  # type: ignore[attr-defined]
    return i if i >= 0 else node.col_offset  # type: ignore[attr-defined]


def snippet(lines: list[str], start: int, end: int) -> str:
    chunk = lines[start - 1 : end]
    if len(chunk) > SNIPPET_MAX_LINES:
        chunk = chunk[:SNIPPET_MAX_LINES] + [f"    # ... {end - start + 1 - SNIPPET_MAX_LINES} more lines"]
    return "\n".join(chunk)


def module_id(rel: str, layout: Layout) -> str:
    parts = list(Path(rel).relative_to(layout.module_root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imports(tree: ast.AST, mid: str, is_package: bool, prefix: str) -> list[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name.startswith(prefix))
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = mid.split(".") if mid else []
                if not is_package:
                    parts = parts[:-1]
                parts = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                base = ".".join(parts + ([base] if base else []))
            if base.startswith(prefix):
                found.add(base)
                found.update(f"{base}.{a.name}" for a in node.names)
    return sorted(found)


def _new_symbol(node: Any, kind: str, qual: str, mid: str, rel: str, lines: list[str], cls: str | None) -> Symbol:
    end = getattr(node, "end_lineno", node.lineno)
    sym: Symbol = {
        "id": f"{mid}.{qual}",
        "kind": kind,
        "name": node.name,
        "qual": qual,
        "module": mid,
        "file": rel,
        "line": node.lineno,
        "end": end,
        "col": name_column(lines, node, node.name),
        "doc": ast.get_docstring(node) or "",
        "h": hashlib.sha1("\n".join(lines[node.lineno - 1 : end]).encode()).hexdigest()[:12],
        "decorators": [unparse(d) for d in node.decorator_list],
        "private": node.name.startswith("_") and not node.name.startswith("__"),
        "code": snippet(lines, node.lineno, end),
        "calls_in": [],
        "calls_in_x": [],
        "calls_out": [],
        "calls_out_x": [],
        "tests": {},
    }
    if kind == "class":
        sym["bases"] = [unparse(b) for b in node.bases]
        sym["sig"] = f"class {node.name}" + (f"({', '.join(sym['bases'])})" if sym["bases"] else "")
        sym["methods"] = []
        sym["subclasses"] = []
    else:
        sym["sig"] = signature(node)
        sym["class"] = cls
    return sym


def _extract_module(
    path: Path, rel: str, text: str, tree: ast.Module, layout: Layout
) -> tuple[Module, list[Symbol], list[str]]:
    lines = text.split("\n")
    mid = module_id(rel, layout)
    mod: Module = {
        "id": mid,
        "path": rel,
        "loc": len(lines),
        "doc": ast.get_docstring(tree) or "",
        "symbols": [],
        "consts": [],
        "imports": [],
        "imported_by": [],
        "tests": {},
        "docs": [],
        "git": None,
        "cochange": [],
        "package": mid.rsplit(".", 1)[0] if "." in mid else mid,
    }
    found: list[Symbol] = []

    def add(node: Any, kind: str, qual: str, cls: str | None = None) -> Symbol:
        sym = _new_symbol(node, kind, qual, mid, rel, lines, cls)
        found.append(sym)
        return sym

    for node in tree.body:
        if isinstance(node, FUNCTION_NODES):
            add(node, "function", node.name)
        elif isinstance(node, ast.ClassDef):
            cls = add(node, "class", node.name)
            for sub in node.body:
                if isinstance(sub, FUNCTION_NODES):
                    cls["methods"].append(add(sub, "method", f"{node.name}.{sub.name}", cls["id"])["id"])
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    mod["consts"].append({"name": t.id, "line": node.lineno, "value": unparse(node.value)[:90]})
    mod["symbols"] = [s["id"] for s in found]
    return mod, found, _imports(tree, mid, path.name == "__init__.py", layout.import_prefix)


def _link_imports(modules: dict[str, Module], raw_imports: dict[str, list[str]]) -> None:
    for mid, imports in raw_imports.items():
        found: set[str] = set()
        for imp in imports:
            parts = imp.split(".")
            while parts:
                cand = ".".join(parts)
                if cand in modules and cand != mid:
                    found.add(cand)
                    break
                parts.pop()
        modules[mid]["imports"] = sorted(found)
        for target in found:
            modules[target]["imported_by"].append(mid)


def _link_subclasses(symbols: dict[str, Symbol]) -> None:
    by_name: dict[str, list[str]] = collections.defaultdict(list)
    for s in symbols.values():
        if s["kind"] == "class":
            by_name[s["name"]].append(s["id"])
    for s in symbols.values():
        if s["kind"] != "class":
            continue
        for base in s["bases"]:
            for target in by_name.get(base.split(".")[-1].split("[")[0], []):
                if target != s["id"]:
                    symbols[target]["subclasses"].append(s["id"])


def _used_names(node: ast.AST) -> set[str]:
    return {x.id for x in ast.walk(node) if isinstance(x, ast.Name)} | {
        x.attr for x in ast.walk(node) if isinstance(x, ast.Attribute)
    }


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


class PythonLanguage:
    name = "python"
    file_exts: tuple[str, ...] = (".py",)
    index_files: tuple[str, ...] = ("__init__.py",)
    default_skip: tuple[str, ...] = DEFAULT_SKIP
    default_server: tuple[str, ...] = DEFAULT_SERVER

    def detect(self, root: Path) -> Layout:
        """Find the one importable package under ``root`` (``src/<pkg>`` or ``<pkg>``)."""
        root = Path(root)
        tests = tuple(d for d in TEST_DIR_NAMES if (root / d).is_dir())
        found: list[tuple[str, str, str]] = []
        src = root / "src"
        if src.is_dir():
            found = [
                (f"src/{d.name}", "src", d.name)
                for d in sorted(src.iterdir())
                if d.is_dir() and (d / "__init__.py").exists()
            ]
        if not found:
            found = [
                (d.name, ".", d.name)
                for d in sorted(root.iterdir())
                if d.is_dir()
                and (d / "__init__.py").exists()
                and d.name not in TEST_DIR_NAMES
                and not d.name.startswith((".", "_"))
            ]
        if not found:
            raise LayoutError(
                f"no Python package found under {root}; pass --pkg-dir, --module-root and --import-prefix"
            )
        if len(found) > 1:
            names = ", ".join(f[2] for f in found)
            raise LayoutError(f"several packages found ({names}); pass --pkg-dir to choose one")
        pkg_dir, module_root, prefix = found[0]
        return Layout(
            pkg_dir=pkg_dir, module_root=module_root, import_prefix=prefix, tests=tests, skip=self.default_skip
        )

    def extract(self, root: Path, layout: Layout) -> tuple[dict[str, Module], dict[str, Symbol]]:
        """Parse every module under ``layout.pkg_dir``; returns (modules, symbols) keyed by dotted id."""
        modules: dict[str, Module] = {}
        symbols: dict[str, Symbol] = {}
        raw_imports: dict[str, list[str]] = {}
        for path in sorted((root / layout.pkg_dir).rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            if layout.is_skipped(rel):
                continue
            text = path.read_text(errors="replace")
            try:
                tree = ast.parse(text)
            except (SyntaxError, ValueError):
                continue
            mod, found, imports = _extract_module(path, rel, text, tree, layout)
            modules[mod["id"]] = mod
            raw_imports[mod["id"]] = imports
            symbols.update({s["id"]: s for s in found})

        _link_imports(modules, raw_imports)
        _link_subclasses(symbols)
        return modules, symbols

    def test_files(self, root: Path, layout: Layout) -> list[str]:
        return [p.relative_to(root).as_posix() for t in layout.tests for p in sorted((root / t).rglob("*.py"))]

    def test_uses(self, path: Path) -> list[tuple[str, set[str]]] | None:
        try:
            tree = ast.parse(path.read_text(errors="replace"))
        except (SyntaxError, ValueError):
            return None
        return [
            (node.name, _used_names(node))
            for node in ast.walk(tree)
            if isinstance(node, FUNCTION_NODES) and node.name.startswith("test")
        ]

    def test_table(self, path: Path) -> TestTable:
        """Qualified function name -> (hash, names used, bare name); plus a hash of the code outside functions."""
        text = path.read_text(errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return {}, "syntax-error"
        funcs: dict[str, tuple[str, set[str], str]] = {}

        def walk(body: list[ast.stmt], prefix: str = "") -> None:
            for n in body:
                if isinstance(n, FUNCTION_NODES):
                    funcs[prefix + n.name] = (_sha1(ast.dump(n).encode()), _used_names(n), n.name)
                    walk(n.body, prefix + n.name + ".")  # nested helpers are call-hierarchy items of their own
                elif isinstance(n, ast.ClassDef):
                    walk(n.body, prefix + n.name + ".")
                else:
                    for field in ("body", "orelse", "finalbody", "handlers"):
                        for child in getattr(n, field, None) or []:
                            if isinstance(child, (*FUNCTION_NODES, ast.ClassDef)):
                                walk([child], prefix)
                            elif hasattr(child, "body"):
                                walk(child.body, prefix)

        walk(tree.body)

        class Strip(ast.NodeTransformer):
            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                return None  # functions compare individually; adding or removing one is not a residual change

            visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

        residual = _sha1(ast.dump(Strip().visit(ast.parse(text))).encode())
        return funcs, residual

    def coarse_names(self, path: Path) -> set[str]:
        names: set[str] = set()
        try:
            for node in ast.walk(ast.parse(path.read_text(errors="replace"))):
                if isinstance(node, ast.Name):
                    names.add(node.id)
                elif isinstance(node, ast.Attribute):
                    names.add(node.attr)
                elif isinstance(node, ast.alias):
                    names.add(node.name.split(".")[-1])
        except SyntaxError:
            pass
        return names


register(PythonLanguage())
