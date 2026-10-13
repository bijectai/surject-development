import asyncio
import io
import json

import pytest

import biject_surject as surject
from biject_surject.checker import ALLOW, BLOCK, FLAG
from biject_surject.policy import parse_policy
from biject_surject.redact import Redactor
from biject_surject.reporter import Reporter, render_markdown

SECRET = "sk-proj-AbCdEfGhIjKlMnOpQrStUvWx1234"

POLICY = """
mode: {mode}
unknown_tools: warn
tools:
  write_file:
    params:
      path: {{ not_glob: ["./data/raw/**"] }}
  delete_file: block
"""


def make(tmp_path, mode="monitor"):
    policy = parse_policy(POLICY.format(mode=mode), source="surject.yaml", root=str(tmp_path))
    stream = io.StringIO()
    reporter = Reporter(root=tmp_path, redactor=Redactor.for_policy(policy),
                        stream=stream, policy_source=policy.source)
    return surject.Monitor(policy, reporter), stream


def everything_written(tmp_path, stream):
    """All text Surject produced: terminal, event log, reports."""
    parts = [stream.getvalue()]
    for f in (tmp_path / ".surject").rglob("*"):
        if f.is_file():
            parts.append(f.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_allowed_call_is_silent(tmp_path):
    m, stream = make(tmp_path)
    assert m.check("write_file", {"path": "out/x.csv"}).decision == ALLOW
    assert stream.getvalue() == ""
    assert not (tmp_path / ".surject").exists()


def test_violation_alerts_logs_and_reports(tmp_path):
    m, stream = make(tmp_path)
    v = m.check("write_file", {"path": "data/raw/x.csv"})
    assert v.decision == FLAG
    out = stream.getvalue()
    assert "[surject] FLAGGED write_file (monitor mode, call allowed)" in out
    assert "tools.write_file.params.path.not_glob[0]" in out

    events = (tmp_path / ".surject" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(events) == 1
    event = json.loads(events[0])
    assert event["tool"] == "write_file" and event["decision"] == FLAG
    assert event["violations"][0]["param"] == "path"

    reports = list((tmp_path / ".surject" / "reports").glob("*.md"))
    assert len(reports) == 1
    md = reports[0].read_text(encoding="utf-8")
    assert md.startswith("# Surject caught `write_file`")
    assert "data/raw/x.csv" in md
    assert "allowed to run (monitor mode)" in md


def test_reports_have_unique_names(tmp_path):
    m, _ = make(tmp_path)
    for _ in range(3):
        m.check("delete_file", {"path": "x"})
    assert len(list((tmp_path / ".surject" / "reports").glob("*.md"))) == 3


def test_secrets_never_reach_any_output(tmp_path):
    m, stream = make(tmp_path, mode="enforce")
    with pytest.raises(surject.PolicyViolation) as exc:
        m.enforce("delete_file", {"path": f"/tmp/{SECRET}", "api_key": "hunter2",
                                  "headers": {"Authorization": f"Bearer {SECRET}"}})
    m.check(f"tool_{SECRET}", {"note": f"token={SECRET}"})
    written = everything_written(tmp_path, stream) + str(exc.value)
    assert SECRET not in written
    assert "hunter2" not in written
    assert "[REDACTED:" in written


def test_enforce_blocks_via_guard(tmp_path):
    m, stream = make(tmp_path, mode="enforce")
    ran = []

    @m.guard
    def delete_file(path):
        ran.append(path)

    with pytest.raises(surject.PolicyViolation) as exc:
        delete_file("data/x")
    assert ran == []
    assert exc.value.verdict.decision == BLOCK
    assert "BLOCKED delete_file" in stream.getvalue()


def test_monitor_mode_guard_lets_call_run(tmp_path):
    m, stream = make(tmp_path)

    @m.guard
    def delete_file(path):
        return f"deleted {path}"

    assert delete_file("data/x") == "deleted data/x"
    assert "FLAGGED delete_file" in stream.getvalue()


def test_guard_binds_positional_and_keyword_args(tmp_path):
    m, _ = make(tmp_path)
    seen = []
    real_check = m.check
    m.check = lambda name, params=None: (seen.append((name, params)),  # type: ignore
                                         real_check(name, params))[1]

    @m.guard(name="write_file")
    def save(path, content="", *rest, **extra):
        return None

    save("a.csv", "hi", 1, 2, mode="w")
    assert seen == [("write_file", {"path": "a.csv", "content": "hi",
                                    "rest": [1, 2], "mode": "w"})]


def test_guard_on_methods_skips_self(tmp_path):
    m, stream = make(tmp_path)

    class Tools:
        @m.guard
        def write_file(self, path):
            return path

    assert Tools().write_file("data/raw/y") == "data/raw/y"
    assert '"path": "data/raw/y"' in stream.getvalue()
    assert "self" not in stream.getvalue().split("params", 1)[1]


def test_guard_async(tmp_path):
    m, _ = make(tmp_path, mode="enforce")

    @m.guard
    async def delete_file(path):
        return "ran"

    @m.guard
    async def write_file(path):
        return "ran"

    assert asyncio.run(write_file("ok.txt")) == "ran"
    with pytest.raises(surject.PolicyViolation):
        asyncio.run(delete_file("x"))


def test_internal_error_fails_open_in_monitor_mode(tmp_path, capsys):
    m, _ = make(tmp_path)

    def boom(_):
        raise RuntimeError(f"broken with {SECRET}")
    m.reporter.report = boom  # type: ignore

    @m.guard
    def delete_file(path):
        return "ran"

    assert delete_file("x") == "ran"
    err = capsys.readouterr().err
    assert "internal error" in err and SECRET not in err


def test_internal_error_fails_closed_in_enforce_mode(tmp_path, capsys):
    m, _ = make(tmp_path, mode="enforce")

    def boom(_):
        raise RuntimeError("broken")
    m.reporter.report = boom  # type: ignore

    @m.guard
    def write_file(path):
        return "ran"

    with pytest.raises(surject.PolicyViolation, match="could not check"):
        write_file("ok.txt")


def test_rate_limits_apply_across_calls(tmp_path):
    policy = parse_policy("tools: {ping: {rate_limit: {max: 2, per: 1h}}}", root=str(tmp_path))
    m = surject.Monitor(policy, Reporter(root=tmp_path, stream=io.StringIO()))
    assert [m.check("ping").decision for _ in range(3)] == [ALLOW, ALLOW, FLAG]


def test_module_level_guard_uses_nearest_policy(tmp_path, monkeypatch):
    (tmp_path / "surject.yaml").write_text("mode: enforce\ntools: {rm: block}\n", encoding="utf-8")
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    surject.set_default_monitor(None)
    try:
        @surject.guard
        def rm(path):
            return "ran"

        with pytest.raises(surject.PolicyViolation):
            rm("x")
        assert (tmp_path / ".surject" / "events.jsonl").exists()
    finally:
        surject.set_default_monitor(None)


def test_module_level_without_policy_checks_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    surject.set_default_monitor(None)
    try:
        assert surject.check("anything", {"x": 1}).decision == ALLOW
        assert "no surject.yaml found" in capsys.readouterr().err
    finally:
        surject.set_default_monitor(None)


def test_markdown_report_is_self_contained():
    event = {
        "id": "x", "time": "2026-10-13T12:00:00.000Z", "tool": "write_file",
        "decision": BLOCK, "mode": "enforce", "checker": "python-reference@0.0.1",
        "policy": "surject.yaml", "params": {"path": "data/raw/x.csv"},
        "violations": [{"rule": "tools.write_file.params.path.not_glob[0]",
                        "message": "'path' must not be inside ./data/raw/**",
                        "severity": "block", "param": "path"}],
    }
    md = render_markdown(event)
    for needle in ("blocked; the call did not run", "python-reference@0.0.1",
                   "must not be inside ./data/raw/**", "redacted automatically"):
        assert needle in md


@pytest.mark.parametrize("source", [
    r"C:\Users\someone\proj\surject.yaml", "/home/someone/proj/surject.yaml", "surject.yaml",
])
def test_reports_never_include_the_full_policy_path(tmp_path, source):
    policy = parse_policy("tools: {rm: block}", source=source, root=str(tmp_path))
    stream = io.StringIO()
    m = surject.Monitor(policy, Reporter(root=tmp_path, stream=stream, policy_source=source))
    m.check("rm", {"path": "x"})
    written = everything_written(tmp_path, stream)
    assert "someone" not in written
    assert "`surject.yaml`" in written
