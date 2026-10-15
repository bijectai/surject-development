"""The checker contract's JSON documents (docs/KERNEL.md).

``policy_to_ir`` / ``policy_from_ir`` convert between a loaded ``Policy`` and
the ``surject-policy/1`` document an external checker (the Lean kernel)
receives. ``request_for_call`` builds a ``surject-request/1``, and
``verdict_to_ir`` / ``verdict_from_ir`` convert verdicts.

``check_request`` is the Python reference checker driven purely by the
documents: it is what the conformance vectors are generated from and
checked against, and what an external checker must agree with.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Mapping, Optional

from biject_surject import paths as _paths
from biject_surject.checker import (
    CHECKER_ID, History, ToolCall, Verdict, Violation, check,
)
from biject_surject.policy import ParamRule, Policy, PolicyError, RateLimit, policy_from_dict

POLICY_FORMAT = "surject-policy/1"
REQUEST_FORMAT = "surject-request/1"
VERDICT_FORMAT = "surject-verdict/1"
CONTRACT_VERSION = 1


def vocabulary() -> Dict[str, List[str]]:
    """The word lists of KERNEL.md section 6, sorted."""
    return {
        "path_param_names": sorted(_paths.PATH_PARAM_NAMES),
        "path_param_suffixes": sorted(_paths.PATH_PARAM_SUFFIXES),
        "write_words": sorted(_paths.WRITE_WORDS),
        "read_words": sorted(_paths.READ_WORDS),
        "command_param_names": sorted(_paths.COMMAND_PARAM_NAMES),
        "write_commands": sorted(_paths.WRITE_COMMANDS),
        "write_git": sorted(_paths.WRITE_GIT),
        "in_place_programs": sorted(_paths.IN_PLACE_FLAGS),
        "null_targets": sorted(_paths._NULL_TARGETS),
    }


# -- policy ------------------------------------------------------------------


def policy_to_ir(policy: Policy) -> Dict[str, Any]:
    return {
        "format": POLICY_FORMAT,
        "mode": policy.mode,
        "unknown_tools": policy.unknown_tools,
        "root": policy.root,
        "tools": {
            name: {
                "blocked": rule.blocked,
                "params": [_param_to_ir(pname, prule) for pname, prule in rule.params.items()],
                "rate_limit": _rate_to_ir(rule.rate_limit),
            }
            for name, rule in policy.tools.items()
        },
        "rate_limit": _rate_to_ir(policy.rate_limit),
        "paths": {
            "protect": [g.pattern for g in policy.paths.protect],
            "read_only": [g.pattern for g in policy.paths.read_only],
            "extra_params": list(policy.paths.extra_params),
        },
        "vocabulary": vocabulary(),
    }


def policy_from_ir(doc: Mapping[str, Any], home: Optional[str] = None) -> Policy:
    """Load a ``surject-policy/1`` document, validating it the same way a
    ``surject.yaml`` is validated."""
    if doc.get("format") != POLICY_FORMAT:
        raise PolicyError(f"expected format {POLICY_FORMAT}, got {doc.get('format')!r}")
    if doc.get("vocabulary") != vocabulary():
        raise PolicyError("policy vocabulary differs from this checker's (see KERNEL.md section 6)")
    tools: Dict[str, Any] = {}
    for name, t in doc["tools"].items():
        if t["blocked"]:
            tools[name] = "block"
            continue
        spec: Dict[str, Any] = {"params": {p["name"]: _param_from_ir(p) for p in t["params"]}}
        if t["rate_limit"]:
            spec["rate_limit"] = _rate_from_ir(t["rate_limit"])
        tools[name] = spec
    data: Dict[str, Any] = {
        "mode": doc["mode"],
        "unknown_tools": doc["unknown_tools"],
        "tools": tools,
        "paths": {
            "protect": list(doc["paths"]["protect"]),
            "read_only": list(doc["paths"]["read_only"]),
            "params": list(doc["paths"]["extra_params"]),
        },
    }
    if doc.get("rate_limit"):
        data["rate_limit"] = _rate_from_ir(doc["rate_limit"])
    return policy_from_dict(data, root=doc.get("root"), home=home)


def _param_to_ir(name: str, r: ParamRule) -> Dict[str, Any]:
    return {
        "name": name,
        "required": r.required,
        "type": r.type,
        "equals": {"value": r.equals} if r.has_equals else None,
        "one_of": list(r.one_of) if r.one_of is not None else None,
        "regex": _regex_to_ir(r.regex),
        "not_regex": _regex_to_ir(r.not_regex),
        "glob": [g.pattern for g in r.glob],
        "not_glob": [g.pattern for g in r.not_glob],
        "min": r.min,
        "max": r.max,
        "max_length": r.max_length,
    }


def _param_from_ir(p: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if p["required"]:
        out["required"] = True
    if p["type"] is not None:
        out["type"] = p["type"]
    if p["equals"] is not None:
        out["equals"] = p["equals"]["value"]
    if p["one_of"] is not None:
        out["one_of"] = list(p["one_of"])
    for key in ("regex", "not_regex"):
        if p[key] is not None:
            prefix = "(?i)" if p[key]["ignore_case"] else ""
            out[key] = prefix + p[key]["pattern"]
    for key in ("glob", "not_glob"):
        if p[key]:
            out[key] = list(p[key])
    for key in ("min", "max", "max_length"):
        if p[key] is not None:
            out[key] = p[key]
    return out


def _regex_to_ir(rx: Any) -> Optional[Dict[str, Any]]:
    if rx is None:
        return None
    pattern = rx.pattern
    ignore_case = pattern.startswith("(?i)")
    return {"pattern": pattern[4:] if ignore_case else pattern, "ignore_case": ignore_case}


def _rate_to_ir(r: Optional[RateLimit]) -> Optional[Dict[str, Any]]:
    return {"max": r.max, "per_seconds": float(r.per)} if r else None


def _rate_from_ir(r: Mapping[str, Any]) -> Dict[str, Any]:
    return {"max": r["max"], "per": float(r["per_seconds"])}


# -- request -----------------------------------------------------------------


def window_key(seconds: float) -> str:
    return repr(float(seconds))


def request_for_call(policy: Policy, call: ToolCall, history: Optional[History] = None,
                     home: Optional[str] = None) -> Dict[str, Any]:
    recent: Dict[str, Dict[str, int]] = {"tool": {}, "all": {}}
    if history is not None:
        tool = policy.tools.get(call.name)
        if tool is not None and tool.rate_limit is not None:
            w = tool.rate_limit.per
            recent["tool"][window_key(w)] = history.count(call.timestamp - w, call.name)
        if policy.rate_limit is not None:
            w = policy.rate_limit.per
            recent["all"][window_key(w)] = history.count(call.timestamp - w)
    return {
        "format": REQUEST_FORMAT,
        "tool": call.name,
        "params": _jsonable(dict(call.params)),
        "home": home if home is not None else os.path.expanduser("~"),
        "recent": recent,
    }


def _jsonable(value: Any) -> Any:
    """Params as JSON. Values JSON can't hold (bytes, objects) become their
    repr, which only ever matters to rules that inspect them as strings."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else repr(value)
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return repr(value)


class _CountsHistory:
    """A History stand-in that answers from a request's ``recent`` counts."""

    def __init__(self, recent: Mapping[str, Mapping[str, int]], now: float):
        self.recent = recent
        self.now = now

    def count(self, since: float, tool: Optional[str] = None) -> Optional[int]:
        table = self.recent.get("all" if tool is None else "tool", {})
        return table.get(window_key(self.now - since))


# -- verdict -----------------------------------------------------------------


def verdict_to_ir(v: Verdict, evidence: Any = None) -> Dict[str, Any]:
    return {
        "format": VERDICT_FORMAT,
        "decision": v.decision,
        "checker": v.checker,
        "violations": [
            {"rule": x.rule, "severity": x.severity, "param": x.param,
             "label": x.label, "message": x.message}
            for x in v.violations
        ],
        "evidence": evidence,
    }


def verdict_from_ir(doc: Mapping[str, Any], call: ToolCall, mode: str) -> Verdict:
    if doc.get("format") != VERDICT_FORMAT:
        raise ValueError(f"expected format {VERDICT_FORMAT}, got {doc.get('format')!r}")
    if doc["decision"] not in ("allow", "flag", "block"):
        raise ValueError(f"bad decision {doc['decision']!r}")
    violations = tuple(
        Violation(rule=str(x["rule"]),
                  message=str(x.get("message") or f"broke rule {x['rule']}"),
                  severity=str(x["severity"]), param=x.get("param"), label=x.get("label"))
        for x in doc["violations"]
    )
    return Verdict(call=call, decision=doc["decision"], violations=violations,
                   mode=mode, checker=str(doc.get("checker") or "unknown"))


def comparable(doc: Mapping[str, Any]) -> Dict[str, Any]:
    """The parts of a verdict conformance compares (KERNEL.md section 1.3)."""
    return {
        "decision": doc["decision"],
        "violations": [[x["rule"], x["severity"], x.get("param"), x.get("label")]
                       for x in doc["violations"]],
    }


# -- the reference checker, driven by documents ------------------------------


def check_request(policy_doc: Mapping[str, Any], request: Mapping[str, Any]) -> Dict[str, Any]:
    if request.get("format") != REQUEST_FORMAT:
        raise ValueError(f"expected format {REQUEST_FORMAT}, got {request.get('format')!r}")
    home = request["home"]
    policy = policy_from_ir(policy_doc, home=home)
    call = ToolCall(request["tool"], request["params"], timestamp=0.0)
    history = _CountsHistory(request.get("recent") or {}, now=0.0)
    verdict = check(policy, call, history, home=home)  # type: ignore[arg-type]
    return verdict_to_ir(verdict)


def dumps(doc: Mapping[str, Any]) -> str:
    return json.dumps(doc, ensure_ascii=False, sort_keys=False)


__all__ = [
    "CHECKER_ID", "CONTRACT_VERSION", "POLICY_FORMAT", "REQUEST_FORMAT", "VERDICT_FORMAT",
    "check_request", "comparable", "policy_from_ir", "policy_to_ir", "request_for_call",
    "verdict_from_ir", "verdict_to_ir", "vocabulary",
]
