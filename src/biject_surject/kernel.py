"""Client for an external checker such as the Lean kernel (docs/KERNEL.md
section 7).

The checker is a separate program speaking one JSON object per line on
stdin/stdout. It's started once, on first use, and kept running. The
command comes from ``SURJECT_KERNEL`` (default ``surject-kernel``).

Any failure (can't start, error reply, malformed reply, timeout) raises
``KernelError``; the monitor decides what that means for the call (fall back
in monitor mode, block in enforce mode).
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import shlex
import subprocess
import threading
from typing import Any, Dict, List, Optional

from biject_surject import ir
from biject_surject.checker import History, ToolCall, Verdict
from biject_surject.policy import Policy

KERNEL_ENV = "SURJECT_KERNEL"
DEFAULT_COMMAND = "surject-kernel"
TIMEOUT_SECONDS = 5.0


class KernelError(RuntimeError):
    pass


def kernel_command() -> List[str]:
    raw = os.environ.get(KERNEL_ENV) or DEFAULT_COMMAND
    return shlex.split(raw, posix=os.name != "nt")


class ExternalChecker:
    def __init__(self, policy: Policy, command: Optional[List[str]] = None,
                 timeout: float = TIMEOUT_SECONDS):
        self.policy = policy
        self.command = command or kernel_command()
        self.timeout = timeout
        self.policy_doc = ir.policy_to_ir(policy)
        self.policy_id = hashlib.sha256(
            json.dumps(self.policy_doc, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        self.name: Optional[str] = None
        self._proc: Optional[subprocess.Popen] = None
        self._lines: "queue.Queue[Optional[str]]" = queue.Queue()
        self._lock = threading.Lock()

    # -- public ------------------------------------------------------------------

    def check(self, call: ToolCall, history: Optional[History] = None,
              home: Optional[str] = None) -> Verdict:
        request = ir.request_for_call(self.policy, call, history, home)
        with self._lock:
            self._ensure_started()
            reply = self._ask({"type": "check", "policy_id": self.policy_id, "request": request},
                              expect="verdict")
        try:
            return ir.verdict_from_ir(reply["verdict"], call, self.policy.mode)
        except (KeyError, TypeError, ValueError) as exc:
            raise KernelError(f"malformed verdict: {exc}") from None

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.stdin.close()  # type: ignore[union-attr]
            proc.wait(timeout=2)
        except Exception:
            proc.kill()

    # -- protocol ----------------------------------------------------------------

    def _ensure_started(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        self._proc = None
        try:
            proc = subprocess.Popen(
                self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1,
            )
        except OSError as exc:
            raise KernelError(f"can't start {self.command[0]!r}: {exc}") from None
        self._proc = proc
        self._lines = queue.Queue()
        threading.Thread(target=self._read, args=(proc, self._lines), daemon=True).start()
        hello = self._ask({"type": "hello", "contract": ir.CONTRACT_VERSION}, expect="hello")
        if hello.get("contract") != ir.CONTRACT_VERSION:
            self.close()
            raise KernelError(f"kernel speaks contract {hello.get('contract')!r}, "
                              f"expected {ir.CONTRACT_VERSION}")
        self.name = str(hello.get("checker") or "external")
        self._ask({"type": "load", "policy_id": self.policy_id, "policy": self.policy_doc},
                  expect="loaded")

    @staticmethod
    def _read(proc: subprocess.Popen, lines: "queue.Queue[Optional[str]]") -> None:
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                lines.put(line)
        finally:
            lines.put(None)  # EOF

    def _ask(self, message: Dict[str, Any], expect: str) -> Dict[str, Any]:
        proc = self._proc
        if proc is None:
            raise KernelError("kernel is not running")
        try:
            proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")  # type: ignore[union-attr]
            proc.stdin.flush()  # type: ignore[union-attr]
        except OSError as exc:
            self.close()
            raise KernelError(f"kernel stopped accepting input: {exc}") from None
        try:
            line = self._lines.get(timeout=self.timeout)
        except queue.Empty:
            self.close()
            raise KernelError(f"no reply within {self.timeout:g}s") from None
        if line is None:
            self.close()
            raise KernelError("kernel exited")
        try:
            reply = json.loads(line)
        except ValueError:
            self.close()
            raise KernelError("kernel sent a line that isn't JSON") from None
        if not isinstance(reply, dict):
            raise KernelError("kernel reply isn't a JSON object")
        if reply.get("type") == "error":
            raise KernelError(f"kernel error: {reply.get('message')}")
        if reply.get("type") != expect:
            raise KernelError(f"expected a {expect!r} reply, got {reply.get('type')!r}")
        return reply
