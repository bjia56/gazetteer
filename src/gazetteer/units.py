"""Units of a repository: one language rooted in one directory, as listed in ``gazetteer.toml``.

A monorepo is documented as several units. Each unit is extracted and queried on its own, in its own
directory, with its own language server. Afterwards its modules and symbols are rebased onto the repository
root (paths) and namespaced by unit (ids) so that the units can share one page without colliding.
"""

from __future__ import annotations

import re
import shlex
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from .layout import Layout, join_path
from .model import Module, Symbol

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

CONFIG_NAME = "gazetteer.toml"
UNIT_KEYS = frozenset(
    {"name", "lang", "path", "pkg_dir", "module_root", "import_prefix", "tests", "skip", "docs_dir", "server"}
)
TOP_KEYS = frozenset({"name", "unit"})
UNIT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


class ConfigError(ValueError):
    """``gazetteer.toml`` is malformed or names something that is not there."""


def unit_root(repo: Path, path: str) -> Path:
    return repo if path in ("", ".") else repo / path


@dataclass(frozen=True)
class Unit:
    """name:   prefix of every id of the unit (``name:pkg.mod``); "" leaves ids bare (a plain single-unit build).
    path:   directory of the unit, relative to the repository root.
    layout: where code, tests and docs are, relative to ``path``.
    server: language server command; None uses the build's or the language's default.
    """

    name: str
    path: str
    layout: Layout
    server: tuple[str, ...] | None = None

    def root(self, repo: Path) -> Path:
        return unit_root(repo, self.path)


@dataclass(frozen=True)
class Config:
    name: str | None = None
    units: tuple[Unit, ...] = field(default_factory=tuple)


def find_config(root: Path) -> Path | None:
    p = root / CONFIG_NAME
    return p if p.is_file() else None


def _strings(table: dict[str, Any], key: str, where: str) -> tuple[str, ...] | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{where}: {key} must be a list of strings")
    return tuple(value)


def _string(table: dict[str, Any], key: str, where: str) -> str | None:
    value = table.get(key)
    if value is not None and not isinstance(value, str):
        raise ConfigError(f"{where}: {key} must be a string")
    return value


def _unit_path(root: Path, raw: str, where: str) -> str:
    p = PurePosixPath(raw)
    if p.is_absolute() or ".." in p.parts:
        raise ConfigError(f"{where}: path {raw!r} must be relative to the repository and stay inside it")
    rel = p.as_posix()
    if not (root / rel).is_dir():
        raise ConfigError(f"{where}: path {raw!r} is not a directory under {root}")
    return rel


def _default_name(path: str, root: Path) -> str:
    return PurePosixPath(path).name if path != "." else root.resolve().name


def load_config(file: Path, root: Path) -> Config:
    """Read ``file``; every unit's layout is detected below its path unless the table overrides it."""
    try:
        raw = tomllib.loads(file.read_text())
    except (tomllib.TOMLDecodeError, OSError) as e:
        raise ConfigError(f"{file}: {e}") from e
    if unknown := set(raw) - TOP_KEYS:
        raise ConfigError(f"{file}: unknown key(s) {', '.join(sorted(unknown))}")
    entries = raw.get("unit")
    if not isinstance(entries, list) or not entries or not all(isinstance(e, dict) for e in entries):
        raise ConfigError(f"{file}: define at least one [[unit]]")
    units: list[Unit] = []
    for i, entry in enumerate(entries, 1):
        where = f"{file}: unit {i}"
        if unknown := set(entry) - UNIT_KEYS:
            raise ConfigError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
        path = _unit_path(root, _string(entry, "path", where) or ".", where)
        name = _string(entry, "name", where) or _default_name(path, root)
        where = f"{file}: unit {name!r}"
        if not UNIT_NAME.fullmatch(name):
            raise ConfigError(f"{where}: name must be letters, digits, '_', '.' or '-'")
        if any(u.name == name for u in units):
            raise ConfigError(f"{where}: two units share this name; set name = ... on one")
        lang = _string(entry, "lang", where) or "python"
        server_cmd = _string(entry, "server", where)
        fields = {k: _string(entry, k, where) for k in ("pkg_dir", "module_root", "import_prefix", "docs_dir")}
        tests, skip = _strings(entry, "tests", where), _strings(entry, "skip", where)
        try:
            layout = Layout.resolve(unit_root(root, path), lang, tests=tests, skip=skip, **fields)
        except ValueError as e:  # LayoutError, or an unknown language
            raise ConfigError(f"{where}: {e}") from e
        command = tuple(shlex.split(server_cmd)) if server_cmd else None
        units.append(Unit(name=name, path=path, layout=layout, server=command))
    return Config(name=_string(raw, "name", str(file)), units=tuple(units))


# ------------------------------------------------------------- rebase and namespace


def _remap(
    modules: dict[str, Module],
    symbols: dict[str, Symbol],
    fid: Callable[[str], str],
    fpath: Callable[[str], str],
    unit: str | None,
) -> tuple[dict[str, Module], dict[str, Symbol]]:
    """Copies of ``modules`` and ``symbols`` with every id and file path mapped; ``unit`` tags each (None clears)."""

    def ids(xs: list[str]) -> list[str]:
        return [fid(x) for x in xs]

    def tests(d: dict[str, Any]) -> dict[str, Any]:
        return {fpath(k): v for k, v in d.items()}

    def docs(ds: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{**d, "file": fpath(d["file"])} for d in ds]

    def tag(d: dict[str, Any]) -> dict[str, Any]:
        d = {k: v for k, v in d.items() if k != "unit"}
        return {**d, "unit": unit} if unit else d

    mods: dict[str, Module] = {}
    for m in modules.values():
        new = tag(m)
        new.update(
            id=fid(m["id"]),
            path=fpath(m["path"]),
            symbols=ids(m["symbols"]),
            imports=ids(m["imports"]),
            imported_by=ids(m["imported_by"]),
            tests=tests(m["tests"]),
            docs=docs(m["docs"]),
        )
        mods[new["id"]] = new
    syms: dict[str, Symbol] = {}
    for s in symbols.values():
        new = tag(s)
        new.update(
            id=fid(s["id"]),
            module=fid(s["module"]),
            file=fpath(s["file"]),
            calls_in=ids(s["calls_in"]),
            calls_out=ids(s["calls_out"]),
            calls_in_x=[{**x, "at": _at(x["at"], fpath)} for x in s["calls_in_x"]],
            tests=tests(s["tests"]),
        )
        if "docs" in s:  # only once docs are linked
            new["docs"] = docs(s["docs"])
        if s.get("class"):
            new["class"] = fid(s["class"])
        if "methods" in s:
            new["methods"] = ids(s["methods"])
            new["subclasses"] = ids(s["subclasses"])
        syms[new["id"]] = new
    return mods, syms


def _at(at: str, fpath: Callable[[str], str]) -> str:
    file, _, line = at.rpartition(":")
    return f"{fpath(file)}:{line}"


def to_repo(
    unit: Unit, modules: dict[str, Module], symbols: dict[str, Symbol]
) -> tuple[dict[str, Module], dict[str, Symbol]]:
    """Unit-local ids and paths to repository-wide ones. A bare unit at the root is returned unchanged."""
    if not unit.name and unit.path in ("", "."):
        return modules, symbols
    prefix = f"{unit.name}:" if unit.name else ""
    return _remap(modules, symbols, lambda i: prefix + i, lambda p: join_path(unit.path, p), unit.name)


def from_repo(
    unit: Unit, modules: dict[str, Module], symbols: dict[str, Symbol]
) -> tuple[dict[str, Module], dict[str, Symbol]]:
    """The part of previous (repository-wide) data that belongs to ``unit``, back in unit-local terms."""
    if not unit.name and unit.path in ("", "."):
        return modules, symbols
    prefix = f"{unit.name}:" if unit.name else ""
    cut = 0 if unit.path in ("", ".") else len(unit.path) + 1

    def keep(d: dict[str, Any]) -> bool:
        return bool(d.get("unit", "") == unit.name)

    return _remap(
        {k: m for k, m in modules.items() if keep(m)},
        {k: s for k, s in symbols.items() if keep(s)},
        lambda i: i[len(prefix) :],
        lambda p: p[cut:],
        None,
    )
