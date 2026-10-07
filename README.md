# gazetteer

A static code docs generator for Python and Go repositories, and for monorepos that mix them.
It builds from one commit.
The output is one HTML page with client-side search: modules, classes, functions, signatures,
docstrings, callers and callees, the tests that reach each symbol, the markdown docs that name it,
and change history from git.

## Install

```bash
pip install .            # from a checkout; no runtime dependencies
pip install pyright      # language server for the call graph (any server with call hierarchy works)
```

Go support comes with two tools, `gazetteer-gohelper` (reads Go source) and `gopls` (the call graph). The
platform wheels bundle both, so on Linux, macOS and Windows `pip install gazetteer` is enough. gopls loads
packages through the `go` command, so the call graph needs a Go toolchain on `PATH`; without one, build with
`--no-lsp`. A source install finds the tools on `PATH`, or see [Development](#development).

## Use

```bash
gazetteer build path/to/repo --out site/
open site/index.html
```

`gazetteer build` writes `site/data.json` and `site/index.html` (data embedded as gzip + base64).
The package layout is detected from `src/<pkg>/` or `<pkg>/`, with `tests/` or `test/` as tests and
`docs/*.md` as docs. Override with `--pkg-dir`, `--module-root`, `--import-prefix`, `--tests`,
`--skip`, `--docs-dir`.

| Option | Effect |
| --- | --- |
| `--config FILE` | Units file for a monorepo. Default `gazetteer.toml` in the root, if present. See [Monorepos](#monorepos). |
| `--lang NAME` | Language of the code. Default `python`. |
| `--server CMD` | Language server command. Default `pyright-langserver --stdio`. |
| `--no-lsp` | Skip the call graph. Build uses only `ast`, docs and git. |
| `--workers N` | Parallel queries (default 8). |
| `--tests-by-name TREE` | Link tests by the names they use instead of through the server. Approximate, much faster. |
| `--git-dir DIR` | Repository for history, when `root` is an export of it. |

## Go

```bash
gazetteer build path/to/go/module --lang go --out site/
```

A Go unit is a directory with a `go.mod`; the module path is the import prefix. Nested modules are units of
their own. It reads `*.go` outside `vendor/`, `testdata/` and directories starting with `.` or `_`, and leaves
out generated files (`// Code generated ... DO NOT EDIT.`), files with `//go:build ignore`, and `*.pb.go`.

Each source file is a module and its directory is its package. Ids are `<path without .go>.<Name>`, with
`Type.Method` for methods, for example `engine/clamp.Engine.Reset`.

- Types are listed as classes (a `decl` field says struct, interface, alias or type), with their methods,
  interface methods included, even when a method is declared in another file of the package.
  Embedded types are the bases.
- A file "imports" the files that declare the names it takes from a package of the module.
- `*_test.go` files are tests, not documented code. Tests that call a symbol are linked to it through gopls,
  or by name with `--tests-by-name`.
- gopls reports an interface method's callers through its implementations, so those appear on the method.
- Files with build constraints are read as written, whatever the platform, so a name declared in several
  platform files is listed once per file. Methods attach to the first such type by path.
- Calls into dependencies that are not in the module cache are missing from the call graph.

Tool lookup, for each of `gazetteer-gohelper` and `gopls`: the environment variable (`GAZETTEER_GOHELPER`,
`GAZETTEER_GOPLS`), the copy bundled in the wheel, then `PATH`. In a source checkout with Go installed the
helper is built from `go/helper` on first use.

## Monorepos

Put a `gazetteer.toml` in the repository root and list the units to document. A unit is one language
rooted in one directory.

```toml
name = "acme"                  # project name on the page (default: directory name)

[[unit]]
path = "services/api"          # directory of the unit, relative to the root (default ".")
name = "api"                   # default: the last component of path

[[unit]]
path = "tools/importer"
lang = "python"                # default
pkg_dir = "src/importer"       # any of pkg_dir, module_root, import_prefix, tests, skip, docs_dir
server = "pyright-langserver --stdio"   # optional per-unit language server

[[unit]]
path = "services/worker"       # a directory with a go.mod
lang = "go"
```

Anything a unit does not set is detected the same way as for a single package, but below its own `path`.
Each unit is read, queried and given its own language server in its own directory, then merged:

- ids are prefixed with the unit name (`api:pkg.mod.func`), so equal module names in two units do not collide;
- file paths are relative to the repository root;
- call graphs, test links and doc links (`<unit>/docs`) stay inside a unit;
- `--shard` and `--facts-dir` keep one facts directory per unit, and `--prev-data` rebuilds only the units that changed.

With a config file the layout options (`--lang`, `--pkg-dir`, ...) belong in the file. Without one, a
build is a single unit and ids stay bare.

## How it works

1. **Parse.** `ast` gives symbols, signatures, docstrings, decorators, imports and a hash of each symbol's source.
2. **Resolve.** The language server's call hierarchy gives callers, callees and the tests that reach each symbol.
   Calls inside lambdas and nested functions count for the enclosing symbol.
3. **Link.** Markdown docs are matched to the modules and symbols they name.
4. **Read history.** `git log` gives change dates, authors and files that change together.

## Incremental builds

Pass the previous output and the tree it was built from:

```bash
gazetteer build new-tree --prev-data old/data.json --prev-root old-tree --out new/
```

Only symbols a diff can affect are queried again: symbols whose source changed, their callers and
callees, names used in changed test functions, and callers of same-named symbols when a symbol
appears or disappears. Everything else is carried over.

## Large repositories

Split the call-graph stage over processes. Each shard writes JSONL facts and can resume:

```bash
for i in 0 1 2 3; do gazetteer build tree --shard $i/4 --facts-dir facts & done; wait
gazetteer build tree --facts-dir facts --out site/
```

## Known limits

- Python and Go. A language is a plugin (`src/gazetteer/languages`); there are no call edges between units.
- Calls made through callbacks or framework registration can show no callers. Decorated symbols
  with no callers are flagged as entry points.
- The call graph needs a language server that supports call hierarchy. Pyright is the default.
- The language server needs to resolve imports. Use a virtualenv or `pyrightconfig.json` for
  third-party packages.
- Keeping tests out of the server's project is much cheaper. Use `--tests-by-name` for that.

## Development

```bash
pip install -e '.[dev]'
python scripts/build_go.py     # builds gazetteer-gohelper and gopls into src/gazetteer/_bin (needs Go)
ruff check . && mypy && pytest
(cd go/helper && go vet ./... && go test ./...)
```

The Go tests in `tests/test_go.py` skip when the helper or gopls is missing. gopls is pinned in
`go/gopls/go.mod`; bump it with `go get golang.org/x/tools/gopls@<version> && go mod tidy` in that directory.
Building it needs the Go version its `go.mod` names, which the go command downloads when needed.

Platform wheels are built by `.github/workflows/wheels.yml`. By hand:

```bash
GAZETTEER_GO_TARGET=linux/arm64 GAZETTEER_WHEEL_PLATFORM=manylinux_2_17_aarch64 python -m build --wheel
```

The wheel then holds both tools for that target and the licenses of the Go modules linked into them
(`gazetteer/_bin/licenses`). Without those variables the wheel is pure Python.
