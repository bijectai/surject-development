"""A stdio MCP proxy: ``surject mcp -- <server command>``.

Sits between any MCP client (Claude Desktop, Claude Code, Cursor, ...) and
an MCP server, in any language. Messages are newline-delimited JSON-RPC
(the MCP stdio transport). Every ``tools/call`` request from the client is
checked before it reaches the server:

- allowed or flagged: forwarded unchanged;
- blocked (enforce mode): answered by the proxy with an ``isError`` tool
  result explaining why, so the model sees the reason and the server never
  sees the call.

Anything the proxy can't parse is forwarded untouched: the proxy must never
be the reason a client and server can't talk.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from typing import Any, BinaryIO, Dict, List, Optional, Tuple

from biject_surject.checker import BLOCK
from biject_surject.monitor import Monitor


class McpProxy:
    def __init__(self, monitor: Monitor, cmd: List[str],
                 client_in: Optional[BinaryIO] = None,
                 client_out: Optional[BinaryIO] = None):
        self.monitor = monitor
        self.cmd = cmd
        self.client_in = client_in or sys.stdin.buffer
        self.client_out = client_out or sys.stdout.buffer
        self._out_lock = threading.Lock()
        self.proc: Optional[subprocess.Popen] = None

    def run(self) -> int:
        self.proc = subprocess.Popen(
            self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None,
        )
        pump = threading.Thread(target=self._server_to_client, daemon=True)
        pump.start()
        try:
            self._client_to_server()
        finally:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
        code = self.proc.wait()
        pump.join(timeout=5)
        return code

    # -- directions ------------------------------------------------------------

    def _client_to_server(self) -> None:
        assert self.proc and self.proc.stdin
        for line in iter(self.client_in.readline, b""):
            reply, forward = self._intercept(line)
            if reply is not None:
                self._send_to_client(reply)
            if forward is None:
                continue
            try:
                self.proc.stdin.write(forward)
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError):
                break

    def _server_to_client(self) -> None:
        assert self.proc and self.proc.stdout
        for line in iter(self.proc.stdout.readline, b""):
            self._send_to_client(line)

    def _send_to_client(self, data: bytes) -> None:
        with self._out_lock:
            try:
                self.client_out.write(data)
                self.client_out.flush()
            except (BrokenPipeError, OSError):
                pass

    # -- checking ----------------------------------------------------------------

    def _intercept(self, line: bytes) -> Tuple[Optional[bytes], Optional[bytes]]:
        """Return (reply to the client, bytes to forward to the server)."""
        try:
            msg = json.loads(line)
        except ValueError:
            return None, line
        if isinstance(msg, list):  # JSON-RPC batch (older protocol versions)
            replies, keep = [], []
            for m in msg:
                r = self._check_message(m)
                (replies if r is not None else keep).append(r if r is not None else m)
            if not replies:
                return None, line
            return _encode(replies), (_encode(keep) if keep else None)
        reply = self._check_message(msg)
        if reply is not None:
            return _encode(reply), None
        return None, line

    def _check_message(self, msg: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(msg, dict) or msg.get("method") != "tools/call":
            return None
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        name = params.get("name")
        args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        verdict = self.monitor.check(str(name), args)
        if verdict.decision != BLOCK or "id" not in msg:
            return None
        reasons = "; ".join(self.monitor.redactor.text(v.message) for v in verdict.violations)
        text = f"surject blocked tool '{self.monitor.redactor.text(str(name))}': {reasons}"
        return {
            "jsonrpc": "2.0",
            "id": msg["id"],
            "result": {"content": [{"type": "text", "text": text}], "isError": True},
        }


def _encode(obj: Any) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
