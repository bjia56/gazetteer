# gazetteer

A static code docs generator for Python repositories. It builds from one commit.
The output is one HTML page with client-side search: modules, classes, functions, signatures,
docstrings, callers and callees, the tests that reach each symbol, the markdown docs that name it,
and change history from git.

## Install

```bash
pip install .            # from a checkout; no runtime dependencies
pip install pyright      # language server for the call graph (any server with call hierarchy works)
```

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
| `--server CMD` | Language server command. Default `pyright-langserver --stdio`. |
| `--no-lsp` | Skip the call graph. Build uses only `ast`, docs and git. |
| `--workers N` | Parallel queries (default 8). |
| `--tests-by-name TREE` | Link tests by the names they use instead of through the server. Approximate, much faster. |
| `--git-dir DIR` | Repository for history, when `root` is an export of it. |

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

- Python only.
- Calls made through callbacks or framework registration can show no callers. Decorated symbols
  with no callers are flagged as entry points.
- The call graph needs a language server that supports call hierarchy. Pyright is the default.
- The language server needs to resolve imports. Use a virtualenv or `pyrightconfig.json` for
  third-party packages.
- Keeping tests out of the server's project is much cheaper. Use `--tests-by-name` for that.

## Development

```bash
pip install -e '.[dev]'
ruff check . && mypy && pytest
```

## License

MIT. See [LICENSE](LICENSE).
