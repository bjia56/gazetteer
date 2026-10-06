"""Minimal language-server client: JSON-RPC over stdio with a reader thread."""

from __future__ import annotations

import contextlib
import itertools
import json
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

DEFAULT_SERVER = ("pyright-langserver", "--stdio")


class LSP:
    def __init__(self, root: Path, cmd: tuple[str, ...] | list[str] = DEFAULT_SERVER) -> None:
        self.root = root.resolve()  # servers report resolved paths; a symlinked root returns empty results
        self.proc = subprocess.Popen(
            list(cmd), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        self.ids = itertools.count(1)
        self.pending: dict[int, dict[str, Any]] = {}
        self.cv = threading.Condition()
        self.wlock = threading.Lock()
        self.timeouts = 0
        self.opened: set[str] = set()
        threading.Thread(target=self._read, daemon=True).start()
        self.request(
            "initialize",
            {
                "processId": None,
                "rootUri": self.root.as_uri(),
                "capabilities": {
                    "workspace": {"configuration": True, "workspaceFolders": True},
                    "textDocument": {"callHierarchy": {}},
                },
                "workspaceFolders": [{"uri": self.root.as_uri(), "name": "root"}],
            },
            timeout=180,
        )
        self.notify("initialized", {})

    def _send(self, msg: dict[str, Any]) -> None:
        body = json.dumps(msg).encode()
        with self.wlock:
            assert self.proc.stdin is not None
            self.proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
            self.proc.stdin.flush()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        f = self.proc.stdout
        while True:
            length = 0
            while True:
                line = f.readline()
                if not line:
                    return
                line = line.strip()
                if not line:
                    break
                if line.lower().startswith(b"content-length:"):
                    length = int(line.split(b":")[1])
            msg = json.loads(f.read(length))
            if "id" in msg and "method" in msg:  # server-to-client request
                result: list[dict[str, Any]] | None = (
                    [{} for _ in msg["params"]["items"]] if msg["method"] == "workspace/configuration" else None
                )
                self._send({"jsonrpc": "2.0", "id": msg["id"], "result": result})
            elif "id" in msg:
                with self.cv:
                    self.pending[msg["id"]] = msg
                    self.cv.notify_all()

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: dict[str, Any], timeout: float = 1800) -> Any:
        i = next(self.ids)
        self._send({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
        end = time.time() + timeout
        with self.cv:
            while i not in self.pending:
                self.cv.wait(timeout=1)
                if time.time() > end:
                    self.timeouts += 1
                    return None
            return self.pending.pop(i).get("result")

    def open(self, rel: str) -> None:
        uri = (self.root / rel).as_uri()
        if uri not in self.opened:
            self.opened.add(uri)
            self.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": "python",
                        "version": 1,
                        "text": (self.root / rel).read_text(),
                    }
                },
            )

    def rel(self, uri: str) -> str | None:
        path = Path(urllib.parse.unquote(uri[len("file://") :]))
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return None

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.proc.kill()
