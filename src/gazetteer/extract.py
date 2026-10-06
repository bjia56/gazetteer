"""Static facts from the source: modules, classes, functions, signatures, docstrings and imports."""

from __future__ import annotations

import ast
import collections
import hashlib
from pathlib import Path
from typing import Any

from .layout import Layout

Module = dict[str, Any]
Symbol = dict[str, Any]

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


def extract_modules(root: Path, layout: Layout) -> tuple[dict[str, Module], dict[str, Symbol]]:
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
