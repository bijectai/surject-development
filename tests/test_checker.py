import pytest

from biject_surject.checker import ALLOW, BLOCK, FLAG, History, ToolCall, check
from biject_surject.policy import parse_policy

POLICY = """
tools:
  read_file:
    params:
      path: { glob: ["./data/**"], required: true }
  write_file:
    params:
      path: { not_glob: ["./data/raw/**", "**/*.env"] }
  http_get:
    params:
      url: { regex: "^https://api\\\\.example\\\\.com/" }
      timeout: { type: number, min: 1, max: 30 }
  query:
    params:
      table: { one_of: [users, orders] }
      limit: { type: integer, max: 1000 }
      sql: { max_length: 20 }
      env: { equals: staging }
  delete_file: block
"""


def run(text, name, params=None, history=None):
    return check(parse_policy(text), ToolCall(name, params or {}, timestamp=1000.0), history)


def rules(verdict):
    return [v.rule for v in verdict.violations]


def test_allowed_call():
    v = run(POLICY, "read_file", {"path": "./data/a.csv"})
    assert v.decision == ALLOW and v.ok and v.violations == ()


def test_glob_violation():
    v = run(POLICY, "read_file", {"path": "/etc/passwd"})
    assert v.decision == FLAG
    assert rules(v) == ["tools.read_file.params.path.glob"]


def test_traversal_is_normalized():
    v = run(POLICY, "write_file", {"path": "out/../data/raw/x.csv"})
    assert rules(v) == ["tools.write_file.params.path.not_glob[0]"]


def test_not_glob_reports_which_pattern():
    v = run(POLICY, "write_file", {"path": "config/prod.env"})
    assert rules(v) == ["tools.write_file.params.path.not_glob[1]"]


def test_required_param_missing():
    v = run(POLICY, "read_file", {})
    assert rules(v) == ["tools.read_file.params.path.required"]


def test_optional_param_missing_is_fine():
    assert run(POLICY, "write_file", {}).ok


def test_regex_and_numeric_bounds():
    v = run(POLICY, "http_get", {"url": "http://evil.example.com/", "timeout": 90})
    assert rules(v) == ["tools.http_get.params.url.regex",
                        "tools.http_get.params.timeout.max"]


def test_type_checks_do_not_treat_bool_as_number():
    v = run(POLICY, "http_get", {"url": "https://api.example.com/x", "timeout": True})
    assert "tools.http_get.params.timeout.type" in rules(v)


def test_one_of_equals_max_length():
    v = run(POLICY, "query", {"table": "secrets", "limit": 5,
                              "sql": "x" * 21, "env": "prod"})
    assert rules(v) == ["tools.query.params.table.one_of",
                        "tools.query.params.sql.max_length",
                        "tools.query.params.env.equals"]


def test_non_string_against_glob_or_regex():
    v = run(POLICY, "read_file", {"path": 42})
    assert rules(v) == ["tools.read_file.params.path.glob"]


def test_blocked_tool():
    v = run(POLICY, "delete_file", {"path": "x"})
    assert rules(v) == ["tools.delete_file"]
    assert v.decision == FLAG  # monitor mode never blocks


def test_enforce_mode_blocks():
    v = run("mode: enforce\n" + POLICY, "delete_file")
    assert v.decision == BLOCK


@pytest.mark.parametrize("setting, decision, severity", [
    ("allow", ALLOW, None),
    ("warn", FLAG, "warn"),
    ("block", BLOCK, "block"),
])
def test_unknown_tools(setting, decision, severity):
    v = run(f"mode: enforce\nunknown_tools: {setting}\n", "mystery_tool")
    assert v.decision == decision
    if severity:
        assert v.violations[0].rule == "unknown_tools"
        assert v.violations[0].severity == severity


def test_unknown_tool_warn_does_not_block_even_in_enforce():
    v = run("mode: enforce\nunknown_tools: warn\n", "mystery_tool")
    assert v.decision == FLAG


def test_messages_never_contain_param_values():
    secret = "ghp_THISISASECRETVALUE123"
    v = run(POLICY, "read_file", {"path": secret})
    v2 = run(POLICY, "query", {"env": secret, "sql": secret})
    for verdict in (v, v2):
        assert verdict.violations
        for viol in verdict.violations:
            assert secret not in viol.message


def test_per_tool_rate_limit():
    policy = parse_policy("tools: {ping: {rate_limit: {max: 2, per: 10s}}}")
    h = History()
    decisions = []
    for t in (0, 1, 2, 11.5, 12, 12.5):
        call = ToolCall("ping", {}, timestamp=t)
        decisions.append(check(policy, call, h).decision)
        h.record(call)
    # attempts count: t=2 is over; by t=11.5 only t=2 is in the window; t=12.5 is over again
    assert decisions == [ALLOW, ALLOW, FLAG, ALLOW, ALLOW, FLAG]


def test_global_rate_limit_counts_all_tools():
    policy = parse_policy("unknown_tools: allow\nrate_limit: {max: 2, per: 1m}")
    h = History()
    out = []
    for i, name in enumerate(["a", "b", "c"]):
        call = ToolCall(name, {}, timestamp=float(i))
        out.append(check(policy, call, h))
        h.record(call)
    assert [v.decision for v in out] == [ALLOW, ALLOW, FLAG]
    assert rules(out[2]) == ["rate_limit"]


def test_check_without_history_skips_rate_limits():
    policy = parse_policy("tools: {ping: {rate_limit: {max: 1, per: 1h}}}")
    assert check(policy, ToolCall("ping"), None).ok


def test_history_prunes_old_entries():
    h = History(horizon=10)
    h.record(ToolCall("a", timestamp=0))
    h.record(ToolCall("a", timestamp=100))
    assert h.count(since=-1, tool="a") == 1


def test_verdict_names_checker():
    v = run(POLICY, "read_file", {"path": "data/x"})
    assert v.checker.startswith("python-reference@")


@pytest.mark.parametrize("root, path, hit", [
    ("/home/u/proj", "/home/u/proj/data/raw/x.csv", True),
    ("/home/u/proj", "/home/u/proj/../proj/data/raw/x.csv", True),
    ("/home/u/proj", "/home/u/other/data/raw/x.csv", False),
    ("/home/u/proj", "/home/u/project/data/raw/x.csv", False),
    (r"C:\Users\u\proj", r"c:\users\u\proj\data\raw\x.csv", True),
    (r"C:\Users\u\proj", "C:/Users/u/proj/data/raw/x.csv", True),
])
def test_absolute_paths_inside_root_match_relative_rules(root, path, hit):
    policy = parse_policy("tools: {write_file: {params: {path: {not_glob: ['./data/raw/**']}}}}",
                          root=root)
    v = check(policy, ToolCall("write_file", {"path": path}))
    assert (not v.ok) is hit


def test_load_policy_sets_root_to_policy_dir(tmp_path):
    from biject_surject.policy import load_policy
    f = tmp_path / "surject.yaml"
    f.write_text("tools: {write_file: {params: {path: {not_glob: ['./data/raw/**']}}}}",
                 encoding="utf-8")
    policy = load_policy(f)
    v = check(policy, ToolCall("write_file", {"path": str(tmp_path / "data" / "raw" / "x.csv")}))
    assert rules(v) == ["tools.write_file.params.path.not_glob[0]"]
