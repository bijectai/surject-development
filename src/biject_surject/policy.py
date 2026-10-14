"""Loading and validating ``surject.yaml`` policies.

The loader turns YAML into immutable dataclasses and compiles every regex
and glob up front, so the checker never parses anything at call time.
Every error names the exact location in the file that is wrong.
"""

from __future__ import annotations

import os
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Pattern, Tuple, Union

import yaml

DEFAULT_POLICY_FILE = "surject.yaml"

MODES = ("monitor", "enforce")
UNKNOWN_TOOL_ACTIONS = ("allow", "warn", "block")
TYPE_NAMES = ("string", "integer", "number", "boolean", "array", "object", "null")

_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h|d)?\s*$")
_DURATION_UNITS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, None: 1}


class PolicyError(ValueError):
    """Raised when a policy file is missing, malformed, or invalid."""

    def __init__(self, message: str, where: str = ""):
        self.where = where
        super().__init__(f"{where}: {message}" if where else message)


@dataclass(frozen=True)
class RateLimit:
    max: int
    per: float  # seconds


@dataclass(frozen=True)
class Glob:
    pattern: str
    regex: Pattern[str]


@dataclass(frozen=True)
class ParamRule:
    """Constraints on one tool parameter. Unset fields are not checked."""

    required: bool = False
    type: Optional[str] = None
    equals: Any = None
    has_equals: bool = False
    one_of: Optional[Tuple[Any, ...]] = None
    regex: Optional[Pattern[str]] = None
    not_regex: Optional[Pattern[str]] = None
    glob: Tuple[Glob, ...] = ()
    not_glob: Tuple[Glob, ...] = ()
    min: Optional[float] = None
    max: Optional[float] = None
    max_length: Optional[int] = None


@dataclass(frozen=True)
class ToolRule:
    blocked: bool = False
    params: Mapping[str, ParamRule] = field(default_factory=dict)
    rate_limit: Optional[RateLimit] = None


@dataclass(frozen=True)
class PathRules:
    """Rules on path-like arguments of *any* tool, whatever it's called."""

    protect: Tuple[Glob, ...] = ()     # no tool may touch these
    read_only: Tuple[Glob, ...] = ()   # tools may read but not change these
    extra_params: Tuple[str, ...] = ()  # more argument names to treat as paths


@dataclass(frozen=True)
class Policy:
    version: int = 1
    mode: str = "monitor"
    unknown_tools: str = "warn"
    tools: Mapping[str, ToolRule] = field(default_factory=dict)
    paths: PathRules = field(default_factory=PathRules)
    rate_limit: Optional[RateLimit] = None
    redact_names: Tuple[str, ...] = ()
    redact_patterns: Tuple[Pattern[str], ...] = ()
    source: Optional[str] = None
    # Project root (the policy file's directory). Absolute paths under it are
    # matched as if relative, so `./data/**` also catches `/abs/proj/data/x`.
    root: Optional[str] = None


# --------------------------------------------------------------------------
# Public entry points


def load_policy(path: Union[str, Path] = DEFAULT_POLICY_FILE) -> Policy:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise PolicyError(
            f"no policy file at {path} (run `surject init` to create one)"
        ) from None
    return parse_policy(text, source=str(path), root=str(path.resolve().parent))


def parse_policy(text: str, source: Optional[str] = None,
                 root: Optional[str] = None, home: Optional[str] = None) -> Policy:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PolicyError(f"invalid YAML: {exc}", source or "") from None
    if data is None:
        data = {}
    return policy_from_dict(data, source=source, root=root, home=home)


def policy_from_dict(data: Any, source: Optional[str] = None,
                     root: Optional[str] = None, home: Optional[str] = None) -> Policy:
    """``root`` is the project folder (relative path rules are relative to
    it); ``home`` is used for ``~`` and defaults to the current user's."""
    _expect(data, dict, "policy")
    root = normalize_path(root, home=home) if root else None
    ctx = _Ctx(root=root, home=home)
    _reject_unknown(data, {"version", "mode", "unknown_tools", "tools", "paths",
                           "rate_limit", "redact"}, "policy")

    version = data.get("version", 1)
    if version != 1:
        raise PolicyError(f"unsupported version {version!r} (expected 1)", "version")

    mode = data.get("mode", "monitor")
    _expect_choice(mode, MODES, "mode")

    unknown = data.get("unknown_tools", "warn")
    _expect_choice(unknown, UNKNOWN_TOOL_ACTIONS, "unknown_tools")

    tools_raw = data.get("tools") or {}
    _expect(tools_raw, dict, "tools")
    tools = {
        str(name): _parse_tool(spec, f"tools.{name}", ctx)
        for name, spec in tools_raw.items()
    }

    paths = _parse_paths(data.get("paths"), "paths", ctx)

    rate = data.get("rate_limit")
    rate_limit = _parse_rate_limit(rate, "rate_limit") if rate is not None else None

    redact_names, redact_patterns = _parse_redact(data.get("redact"), "redact")

    return Policy(
        version=version,
        mode=mode,
        unknown_tools=unknown,
        tools=tools,
        paths=paths,
        rate_limit=rate_limit,
        redact_names=redact_names,
        redact_patterns=redact_patterns,
        source=source,
        root=root,
    )


@dataclass(frozen=True)
class _Ctx:
    root: Optional[str]
    home: Optional[str]


def normalize_path(value: str, root: Optional[str] = None,
                   home: Optional[str] = None) -> str:
    """Normalize a path for matching, exactly as docs/KERNEL.md section 3:
    expand a leading ``~`` (not ``~user``) to ``home``; forward slashes;
    resolve relative paths against ``root``; POSIX normpath with leading
    slashes collapsed to one; and if the result is inside ``root``, make it
    relative to ``root``. Never touches the disk."""
    if value == "~" or value.startswith(("~/", "~\\")):
        value = (home if home is not None else os.path.expanduser("~")) + value[1:]
    value = value.replace("\\", "/")
    if root is not None:
        root = _normpath(root.replace("\\", "/")).rstrip("/")
        # Relative paths are relative to the project, so `../proj/data/x`
        # and `../.ssh/id_rsa` resolve to what they really point at.
        if not (value.startswith("/") or _DRIVE_RE.match(value)):
            value = (root or "") + "/" + value
    norm = _normpath(value)
    if norm == ".":
        return ""
    if root:
        # Windows drive paths are case-insensitive.
        fold = _DRIVE_RE.match(root) is not None
        a, b = (norm.casefold(), root.casefold()) if fold else (norm, root)
        if a == b:
            return ""
        if a.startswith(b + "/"):
            return norm[len(root) + 1:]
    return norm


def _normpath(value: str) -> str:
    """posixpath.normpath, but `//x` becomes `/x`: on POSIX systems they're
    the same file, so keeping `//` would let `//etc/passwd` dodge `/etc/**`."""
    norm = posixpath.normpath(value)
    return "/" + norm.lstrip("/") if norm.startswith("//") else norm


_DRIVE_RE = re.compile(r"^[A-Za-z]:/")


def compile_glob(pattern: str, root: Optional[str] = None,
                 home: Optional[str] = None) -> Glob:
    """``**`` matches across directories, ``*`` and ``?`` stay within one.
    ``dir/**`` also matches ``dir`` itself, so deleting or moving the whole
    folder is caught too. The pattern goes through the same normalization
    as values (including ``root``), so absolute patterns inside the project
    match project-relative values."""
    absolute = normalize_path(pattern, home=home)
    # Windows paths are case-insensitive: drive-letter patterns, and every
    # pattern in a project that lives on a drive.
    fold = bool(_DRIVE_RE.match(absolute) or (root and _DRIVE_RE.match(root)))
    norm = normalize_path(pattern, root, home)
    out = []
    i = 0
    while i < len(norm):
        c = norm[i]
        if norm.endswith("/**") and i == len(norm) - 3:
            out.append("(?:/.*)?")
            i += 3
        elif norm.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif norm.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    flags = re.IGNORECASE if fold else 0
    return Glob(pattern=pattern, regex=re.compile("^" + "".join(out) + "$", flags))


def parse_duration(value: Any, where: str) -> float:
    if isinstance(value, bool):
        raise PolicyError("expected a duration like 60s, 5m or 1h", where)
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str) and _DURATION_RE.match(value):
        num, unit = _DURATION_RE.match(value).groups()
        seconds = float(num) * _DURATION_UNITS[unit]
    else:
        raise PolicyError(f"expected a duration like 60s, 5m or 1h, got {value!r}", where)
    if seconds <= 0:
        raise PolicyError("duration must be positive", where)
    return seconds


# --------------------------------------------------------------------------
# Section parsers


def _parse_tool(spec: Any, where: str, ctx: "_Ctx") -> ToolRule:
    if spec is None or spec == "allow":
        return ToolRule()
    if spec == "block":
        return ToolRule(blocked=True)
    if not isinstance(spec, dict):
        raise PolicyError("expected allow, block, or a mapping with params/rate_limit", where)
    _reject_unknown(spec, {"params", "rate_limit"}, where)

    params_raw = spec.get("params") or {}
    _expect(params_raw, dict, f"{where}.params")
    params = {
        str(name): _parse_param(rule, f"{where}.params.{name}", ctx)
        for name, rule in params_raw.items()
    }
    rate = spec.get("rate_limit")
    rate_limit = _parse_rate_limit(rate, f"{where}.rate_limit") if rate is not None else None
    return ToolRule(params=params, rate_limit=rate_limit)


_PARAM_KEYS = {"required", "type", "equals", "one_of", "regex", "not_regex", "glob",
               "not_glob", "min", "max", "max_length"}


def _parse_param(rule: Any, where: str, ctx: "_Ctx") -> ParamRule:
    _expect(rule, dict, where)
    _reject_unknown(rule, _PARAM_KEYS, where)
    kw: Dict[str, Any] = {}

    if "required" in rule:
        _expect(rule["required"], bool, f"{where}.required")
        kw["required"] = rule["required"]
    if "type" in rule:
        _expect_choice(rule["type"], TYPE_NAMES, f"{where}.type")
        kw["type"] = rule["type"]
    if "equals" in rule:
        kw["equals"] = rule["equals"]
        kw["has_equals"] = True
    if "one_of" in rule:
        _expect(rule["one_of"], list, f"{where}.one_of")
        kw["one_of"] = tuple(rule["one_of"])
    for key in ("regex", "not_regex"):
        if key in rule:
            kw[key] = _compile_rule_regex(rule[key], f"{where}.{key}")
    for key in ("glob", "not_glob"):
        if key in rule:
            kw[key] = tuple(
                compile_glob(p, ctx.root, ctx.home)
                for p in _string_list(rule[key], f"{where}.{key}")
            )
    for key in ("min", "max"):
        if key in rule:
            val = rule[key]
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise PolicyError("expected a number", f"{where}.{key}")
            kw[key] = val
    if "max_length" in rule:
        val = rule["max_length"]
        if isinstance(val, bool) or not isinstance(val, int) or val < 0:
            raise PolicyError("expected a non-negative integer", f"{where}.max_length")
        kw["max_length"] = val

    if kw.get("min") is not None and kw.get("max") is not None and kw["min"] > kw["max"]:
        raise PolicyError("min is greater than max", where)
    return ParamRule(**kw)


def _parse_rate_limit(spec: Any, where: str) -> RateLimit:
    _expect(spec, dict, where)
    _reject_unknown(spec, {"max", "per"}, where)
    if "max" not in spec or "per" not in spec:
        raise PolicyError("needs both max and per, e.g. { max: 30, per: 60s }", where)
    mx = spec["max"]
    if isinstance(mx, bool) or not isinstance(mx, int) or mx < 1:
        raise PolicyError("max must be a positive integer", f"{where}.max")
    return RateLimit(max=mx, per=parse_duration(spec["per"], f"{where}.per"))


def _parse_paths(spec: Any, where: str, ctx: "_Ctx") -> PathRules:
    if spec is None:
        return PathRules()
    _expect(spec, dict, where)
    _reject_unknown(spec, {"protect", "read_only", "params"}, where)
    return PathRules(
        protect=tuple(compile_glob(p, ctx.root, ctx.home) for p in
                      _string_list(spec.get("protect", []), f"{where}.protect")),
        read_only=tuple(compile_glob(p, ctx.root, ctx.home) for p in
                        _string_list(spec.get("read_only", []), f"{where}.read_only")),
        extra_params=tuple(_string_list(spec.get("params", []), f"{where}.params")),
    )


def _parse_redact(spec: Any, where: str) -> Tuple[Tuple[str, ...], Tuple[Pattern[str], ...]]:
    if spec is None:
        return (), ()
    _expect(spec, dict, where)
    _reject_unknown(spec, {"names", "patterns"}, where)
    names = tuple(n.lower() for n in _string_list(spec.get("names", []), f"{where}.names"))
    patterns = tuple(
        _compile_regex(p, f"{where}.patterns[{i}]")
        for i, p in enumerate(_string_list(spec.get("patterns", []), f"{where}.patterns"))
    )
    return names, patterns


# --------------------------------------------------------------------------
# Small validation helpers

_TYPE_LABELS = {dict: "a mapping", list: "a list", bool: "true or false", str: "a string"}


def _expect(value: Any, typ: type, where: str) -> None:
    if not isinstance(value, typ):
        raise PolicyError(f"expected {_TYPE_LABELS.get(typ, typ.__name__)}", where)


def _expect_choice(value: Any, choices: Tuple[str, ...], where: str) -> None:
    if value not in choices:
        raise PolicyError(f"expected one of {', '.join(choices)}, got {value!r}", where)


def _reject_unknown(mapping: Dict[Any, Any], allowed: set, where: str) -> None:
    extra = sorted(str(k) for k in mapping if k not in allowed)
    if extra:
        raise PolicyError(
            f"unknown key(s) {', '.join(extra)} (allowed: {', '.join(sorted(allowed))})",
            where,
        )


def _string_list(value: Any, where: str) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    raise PolicyError("expected a string or a list of strings", where)


# Rule regexes are limited to features a verified checker (the Lean kernel)
# can implement exactly as an automaton: no lookarounds, backreferences,
# conditionals, atomic groups or possessive repeats, and no flags except a
# leading (?i). Redaction patterns are exempt: they only affect output.
try:  # Python 3.11+
    import re._parser as _sre_parse  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - Python 3.9 / 3.10
    import sre_parse as _sre_parse  # type: ignore[no-redef]

_UNSUPPORTED_REGEX = {
    "ASSERT": "lookarounds aren't supported (use not_regex for \"must not match\")",
    "ASSERT_NOT": "lookarounds aren't supported (use not_regex for \"must not match\")",
    "GROUPREF": "backreferences aren't supported",
    "GROUPREF_EXISTS": "conditional groups aren't supported",
    "ATOMIC_GROUP": "atomic groups aren't supported",
    "POSSESSIVE_REPEAT": "possessive repeats aren't supported",
}


def _compile_rule_regex(value: Any, where: str) -> Pattern[str]:
    compiled = _compile_regex(value, where)
    parsed = _sre_parse.parse(value)
    extra_flags = parsed.state.flags & ~(re.IGNORECASE | re.UNICODE)
    if extra_flags:
        raise PolicyError("only the (?i) flag is supported in rule regexes", where)

    def walk(node: Any) -> None:
        if isinstance(node, _sre_parse.SubPattern):
            for op, av in node:
                name = getattr(op, "name", str(op))
                if name in _UNSUPPORTED_REGEX:
                    raise PolicyError(_UNSUPPORTED_REGEX[name], where)
                if name == "SUBPATTERN" and (av[1] or av[2]):
                    raise PolicyError("scoped flags like (?i:...) aren't supported; "
                                      "put (?i) at the start", where)
                walk(av)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    walk(parsed)
    return compiled


def _compile_regex(value: Any, where: str) -> Pattern[str]:
    if not isinstance(value, str):
        raise PolicyError("expected a regular expression string", where)
    try:
        return re.compile(value)
    except re.error as exc:
        raise PolicyError(f"invalid regular expression: {exc}", where) from None
