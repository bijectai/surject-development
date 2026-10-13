"""Secret redaction for everything Surject prints or writes.

Redaction is mandatory and cannot be turned off. It errs toward hiding: a
false positive costs a little readability, a false negative leaks a secret.
Policies can add names and patterns but never remove the built-in ones.

Redacted values become ``[REDACTED:<reason>]`` so a report still shows that
something was there and why it was hidden.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Iterable, List, Mapping, Optional, Pattern, Tuple

MAX_DEPTH = 20

# Words that make a parameter/key name sensitive. Names are split into words
# on non-alphanumerics and camelCase, so `apiKey`, `api_key` and `X-Api-Key`
# all yield "key", while `keyword` does not.
SENSITIVE_WORDS = frozenset({
    "token", "tokens", "secret", "secrets", "password", "passwd", "pwd",
    "passphrase", "pass", "apikey", "key", "keys", "auth", "authorization",
    "cookie", "cookies", "credential", "credentials", "session", "sessionid",
    "jwt", "bearer", "signature", "sig", "otp", "pin", "ssn", "private",
    "dsn", "conn", "connection", "salt",
})

# (reason, pattern). Order matters: specific shapes before generic ones.
_BUILTIN_PATTERNS: List[Tuple[str, str]] = [
    ("private-key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)"),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"),
    ("bearer", r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    ("basic-auth", r"(?i)\bbasic\s+[A-Za-z0-9+/=]{8,}"),
    ("api-key", r"\bsk-[A-Za-z0-9_-]{16,}"),
    ("api-key", r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    ("api-key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("api-key", r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    ("api-key", r"\bAIza[0-9A-Za-z_-]{35}"),
    ("api-key", r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}"),
    ("api-key", r"\bglpat-[A-Za-z0-9_-]{20,}"),
    ("api-key", r"\bnpm_[A-Za-z0-9]{30,}"),
    ("api-key", r"\bhf_[A-Za-z0-9]{30,}"),
    ("url-credentials", r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)"),
    ("email", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
]

# `password=hunter2`, `"api_key": "abc"`, `TOKEN: xyz` inside free text:
# keep the label, hide the value.
_ASSIGNMENT = re.compile(
    r"""(?ix)
    (?P<label>[A-Za-z0-9_.-]*(?:pass(?:word|wd|phrase)?|pwd|secret|token|api[_-]?key|
        access[_-]?key|private[_-]?key|auth|credential|cookie|session)[A-Za-z0-9_.-]*)
    (?P<sep>["']?\s*[:=]\s*["']?)
    (?P<value>[^\s"',;&}]+)
    """
)

_ENTROPY_CANDIDATE = re.compile(r"[A-Za-z0-9+/_=-]{32,}")
_CAMEL = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")


class Redactor:
    def __init__(self, extra_names: Iterable[str] = (),
                 extra_patterns: Iterable[Pattern[str]] = ()):
        self.extra_names = frozenset(n.lower() for n in extra_names)
        self.patterns: List[Tuple[str, Pattern[str]]] = [
            (reason, re.compile(p)) for reason, p in _BUILTIN_PATTERNS
        ] + [("policy-pattern", p) for p in extra_patterns]

    @classmethod
    def for_policy(cls, policy: Any) -> "Redactor":
        return cls(getattr(policy, "redact_names", ()),
                   getattr(policy, "redact_patterns", ()))

    # -- names -------------------------------------------------------------

    def is_sensitive_name(self, name: Any) -> bool:
        if not isinstance(name, str):
            return False
        lowered = name.lower()
        if lowered in self.extra_names:
            return True
        words = {w.lower() for part in re.split(r"[^A-Za-z0-9]+", name)
                 for w in _CAMEL.findall(part)}
        words.add(re.sub(r"[^a-z0-9]", "", lowered))  # "apikey", "sessionid"
        return bool(words & (SENSITIVE_WORDS | self.extra_names))

    # -- values ------------------------------------------------------------

    def value(self, value: Any, name: Optional[str] = None, _depth: int = 0) -> Any:
        """Return a redacted copy of ``value`` safe to print or store."""
        if name is not None and self.is_sensitive_name(name):
            return "[REDACTED:name]" if value not in (None, "") else value
        if _depth >= MAX_DEPTH:
            return "[TRUNCATED]"
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, (bool, int, float)) or value is None:
            return value
        if isinstance(value, (bytes, bytearray, memoryview)):
            return f"[{len(bytes(value))} bytes]"
        if isinstance(value, Mapping):
            return {
                self.text(str(k)): self.value(v, name=str(k), _depth=_depth + 1)
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple, set, frozenset)):
            return [self.value(v, _depth=_depth + 1) for v in value]
        # Unknown object: its repr may embed secrets, so scan that.
        try:
            rendered = repr(value)
        except Exception:
            return f"[unrepresentable {type(value).__name__}]"
        return self.text(rendered)

    def text(self, text: str) -> str:
        """Redact secrets inside free text (messages, reprs, file contents)."""
        for reason, pattern in self.patterns:
            text = pattern.sub(f"[REDACTED:{reason}]", text)
        text = _ASSIGNMENT.sub(_redact_assignment, text)
        text = _ENTROPY_CANDIDATE.sub(_redact_if_high_entropy, text)
        return text


def _redact_assignment(m: "re.Match[str]") -> str:
    if m.group("value").startswith("[REDACTED"):
        return m.group(0)
    return f"{m.group('label')}{m.group('sep')}[REDACTED:assignment]"


def _redact_if_high_entropy(m: "re.Match[str]") -> str:
    s = m.group(0)
    if "REDACTED" in s or not _looks_random(s):
        return s
    return "[REDACTED:high-entropy]"


def _looks_random(s: str) -> bool:
    classes = sum([
        any(c.islower() for c in s),
        any(c.isupper() for c in s),
        any(c.isdigit() for c in s),
    ])
    # Mixed-class and high Shannon entropy. Lowercase hex (git SHAs,
    # checksums) has only two classes and stays visible.
    return classes >= 3 and _entropy(s) >= 4.0


def _entropy(s: str) -> float:
    counts = Counter(s)
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in counts.values())
