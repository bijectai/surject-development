"""The checker contract documents (docs/KERNEL.md): round trips, and the
document-driven reference checker agreeing with the direct one."""

import json

import pytest

from biject_surject import ir
from biject_surject.checker import History, ToolCall, check, json_equal
from biject_surject.policy import PolicyError, parse_policy

ROOT = "/home/ada/proj"
HOME = "/home/ada"

POLICY = r"""
mode: enforce
unknown_tools: warn
tools:
  write_file:
    params:
      path: { not_glob: ["./data/raw/**", "/home/ada/proj/secrets/**"], required: true }
      mode: { one_of: [w, a] }
  http_get:
    params:
      url: { regex: "(?i)^https://api\\.example\\.com/" }
      body: { not_regex: "password", max_length: 50 }
      timeout: { type: number, min: 1, max: 30 }
    rate_limit: { max: 2, per: 60s }
  query:
    params:
      limit: { type: integer }
      env: { equals: null }
      flag: { equals: 1 }
  rm: block
paths:
  protect: ["**/.env", "~/.ssh/**"]
  read_only: ["./.git/**"]
  params: [notebook]
rate_limit: { max: 3, per: 1h }
"""

CALLS = [
    ("write_file", {"path": "out/x.csv", "mode": "w"}),
    ("write_file", {"path": "data/raw/x.csv"}),
    ("write_file", {"path": "/home/ada/proj/secrets/k"}),
    ("write_file", {"mode": "x"}),
    ("http_get", {"url": "HTTPS://API.example.com/v1", "timeout": 5}),
    ("http_get", {"url": "http://evil.example.com/", "timeout": 90, "body": "password=1"}),
    ("http_get", {"url": 3, "timeout": True, "body": ["x"] * 60}),
    ("query", {"limit": 3.0, "env": None, "flag": 1.0}),
    ("query", {"limit": 3.5, "env": 0, "flag": True}),
    ("rm", {"path": ".env"}),
    ("mystery", {"file_path": "~/.ssh/id_rsa"}),
    ("Edit", {"file_path": "/home/ada/proj/.git/config"}),
    ("Read", {"file_path": "/home/ada/proj/.git/config"}),
    ("Bash", {"command": "cat .env && rm -rf .git"}),
    ("save", {"notebook": ".git/x.ipynb"}),
    ("mcp__fs__edit_file", {"edits": [{"path": "a/.env"}, {"path": "ok.txt"}]}),
]


@pytest.fixture
def policy():
    return parse_policy(POLICY, root=ROOT, home=HOME)


def test_documents_are_plain_json(policy):
    doc = ir.policy_to_ir(policy)
    assert json.loads(json.dumps(doc)) == doc
    assert doc["format"] == "surject-policy/1"
    assert [p["name"] for p in doc["tools"]["write_file"]["params"]] == ["path", "mode"]
    assert doc["tools"]["http_get"]["params"][0]["regex"] == {
        "pattern": r"^https://api\.example\.com/", "ignore_case": True}
    assert doc["tools"]["query"]["params"][1]["equals"] == {"value": None}
    assert doc["tools"]["rm"]["blocked"] is True


def test_policy_round_trip_is_stable(policy):
    doc = ir.policy_to_ir(policy)
    assert ir.policy_to_ir(ir.policy_from_ir(doc, home=HOME)) == doc


@pytest.mark.parametrize("tool, params", CALLS)
def test_document_checker_agrees_with_direct_checker(policy, tool, params):
    call = ToolCall(tool, params, timestamp=1000.0)
    direct = ir.verdict_to_ir(check(policy, call, None, home=HOME))
    via_docs = ir.check_request(ir.policy_to_ir(policy),
                                ir.request_for_call(policy, call, None, home=HOME))
    assert ir.comparable(via_docs) == ir.comparable(direct)


def test_rate_limits_through_recent_counts(policy):
    history = History()
    doc = ir.policy_to_ir(policy)
    decisions = []
    for t in range(4):
        call = ToolCall("http_get", {"url": "https://api.example.com/x", "timeout": 5},
                        timestamp=1000.0 + t)
        req = ir.request_for_call(policy, call, history, home=HOME)
        decisions.append([v["rule"] for v in ir.check_request(doc, req)["violations"]])
        history.record(call)
    assert decisions == [[], [], ["tools.http_get.rate_limit"],
                         ["tools.http_get.rate_limit", "rate_limit"]]
    assert req["recent"] == {"tool": {"60.0": 3}, "all": {"3600.0": 3}}


def test_missing_counts_mean_not_checked(policy):
    req = {"format": "surject-request/1", "tool": "http_get",
           "params": {"url": "https://api.example.com/x"}, "home": HOME, "recent": {}}
    assert ir.check_request(ir.policy_to_ir(policy), req)["decision"] == "allow"


def test_verdicts_carry_labels(policy):
    call = ToolCall("mcp__fs__edit_file", {"edits": [{"path": "ok"}, {"path": "a/.env"}]})
    v = ir.verdict_to_ir(check(policy, call, home=HOME))
    assert [(x["rule"], x["param"], x["label"]) for x in v["violations"]] == [
        ("unknown_tools", None, None), ("paths.protect[0]", "edits", "edits[1].path")]


def test_verdict_from_ir_round_trip(policy):
    call = ToolCall("rm", {"path": "x"})
    doc = ir.verdict_to_ir(check(policy, call, home=HOME))
    back = ir.verdict_from_ir(doc, call, policy.mode)
    assert ir.comparable(ir.verdict_to_ir(back)) == ir.comparable(doc)
    with pytest.raises(ValueError):
        ir.verdict_from_ir({**doc, "decision": "maybe"}, call, policy.mode)


def test_vocabulary_mismatch_is_rejected(policy):
    doc = ir.policy_to_ir(policy)
    doc["vocabulary"]["write_words"] = ["write"]
    with pytest.raises(PolicyError, match="vocabulary"):
        ir.policy_from_ir(doc)


def test_non_json_params_become_strings(policy):
    req = ir.request_for_call(policy, ToolCall("x", {"b": b"\x00", "n": float("nan"),
                                                    "o": object, "t": (1, 2)}))
    json.dumps(req)
    assert req["params"]["t"] == [1, 2] and isinstance(req["params"]["b"], str)


# -- the five semantics fixes made while writing the spec -------------------


def test_absolute_patterns_inside_root_match():
    p = parse_policy("paths: {protect: ['/home/ada/proj/secrets/**']}\nunknown_tools: allow",
                     root=ROOT, home=HOME)
    assert not check(p, ToolCall("Read", {"path": "/home/ada/proj/secrets/k"}), home=HOME).ok
    assert not check(p, ToolCall("Read", {"path": "secrets/k"}), home=HOME).ok


def test_windows_projects_match_case_insensitively():
    p = parse_policy("paths: {protect: ['./data/**']}\nunknown_tools: allow",
                     root="C:/Users/ada/proj")
    assert not check(p, ToolCall("Read", {"path": r"C:\Users\ada\proj\DATA\x"})).ok
    assert not check(p, ToolCall("Read", {"path": r"DATA\x"})).ok


def test_json_equality():
    assert json_equal(1, 1.0) and not json_equal(True, 1) and not json_equal(0, False)
    assert json_equal({"a": [1, None]}, {"a": [1.0, None]}) and not json_equal("1", 1)
    p = parse_policy("tools: {t: {params: {x: {equals: 1}, y: {one_of: [0]}}}}")
    v = check(p, ToolCall("t", {"x": True, "y": False}))
    assert [x.rule for x in v.violations] == ["tools.t.params.x.equals", "tools.t.params.y.one_of"]


def test_integer_is_by_value():
    p = parse_policy("tools: {t: {params: {x: {type: integer}}}}")
    assert check(p, ToolCall("t", {"x": 3.0})).ok
    assert not check(p, ToolCall("t", {"x": 3.5})).ok
    assert not check(p, ToolCall("t", {"x": True})).ok


@pytest.mark.parametrize("value, hit", [
    ("~/.ssh/id_rsa", True), ("~", False), (r"~\.ssh\id_rsa", True), ("~ada/.ssh/id_rsa", False),
])
def test_home_expansion(value, hit):
    p = parse_policy("paths: {protect: ['~/.ssh/**']}\nunknown_tools: allow", home=HOME)
    assert (not check(p, ToolCall("Read", {"path": value}), home=HOME).ok) is hit


# -- bypasses found while reviewing the conformance vectors ------------------


@pytest.mark.parametrize("value", ["../proj/data/x", "x/../../proj/data/y"])
def test_relative_paths_that_re_enter_the_project_are_caught(value):
    p = parse_policy("paths: {protect: ['./data/**']}\nunknown_tools: allow", root=ROOT, home=HOME)
    assert not check(p, ToolCall("Read", {"path": value}), home=HOME).ok


def test_relative_paths_that_leave_the_project_hit_absolute_rules():
    p = parse_policy("paths: {protect: ['~/.ssh/**']}\nunknown_tools: allow", root=ROOT, home=HOME)
    assert not check(p, ToolCall("Read", {"path": "../.ssh/id_rsa"}), home=HOME).ok
    assert not check(p, ToolCall("Bash", {"command": "cat ../.ssh/id_rsa"}), home=HOME).ok


@pytest.mark.parametrize("value", ["//etc/passwd", "///etc/passwd", "//etc//passwd"])
def test_leading_double_slash_does_not_dodge_absolute_rules(value):
    p = parse_policy("paths: {protect: ['/etc/**']}\nunknown_tools: allow", root=ROOT)
    assert not check(p, ToolCall("Read", {"path": value})).ok


def test_extra_param_names_match_in_any_case_style():
    for extra, arg in [("weirdArg", "weird_arg"), ("weird_arg", "weirdArg"), ("WeirdArg", "weirdarg")]:
        p = parse_policy(f"paths: {{protect: ['**/.env'], params: [{extra}]}}\nunknown_tools: allow")
        v = check(p, ToolCall("t", {arg: ".env"}))
        assert not v.ok or arg == "weirdarg"  # "weirdarg" is one word, not weird+arg


def test_deeply_nested_paths_are_found():
    p = parse_policy("paths: {protect: ['**/.env']}\nunknown_tools: allow")
    params = {"a": {"b": {"c": {"d": {"e": {"f": {"path": ".env"}}}}}}}
    assert not check(p, ToolCall("t", params)).ok
