import io
import json
import os
import subprocess
import sys

import pytest

from biject_surject.hooks import run_claude_code_hook

SECRET = "sk-proj-AbCdEfGhIjKlMnOpQrStUvWx"

POLICY = """
mode: {mode}
tools:
  Write:
    params:
      file_path: {{ not_glob: ["**/.env", "./data/raw/**"] }}
  Bash:
    params:
      command: {{ regex: "^(?!.*rm -rf)" }}
    rate_limit: {{ max: 2, per: 1h }}
"""


def write_policy(project, mode="enforce"):
    (project / "surject.yaml").write_text(POLICY.format(mode=mode), encoding="utf-8")


@pytest.fixture
def project(tmp_path):
    write_policy(tmp_path)
    return tmp_path


def hook(project, tool, tool_input, policy=None):
    event = {
        "session_id": "s", "transcript_path": "/x", "cwd": str(project),
        "permission_mode": "default", "hook_event_name": "PreToolUse",
        "tool_name": tool, "tool_input": tool_input, "tool_use_id": "toolu_1",
    }
    out = io.StringIO()
    code = run_claude_code_hook(io.StringIO(json.dumps(event)), out, policy)
    text = out.getvalue().strip()
    return code, (json.loads(text) if text else None)


def test_allowed_is_silent(project):
    assert hook(project, "Write", {"file_path": str(project / "src" / "a.py"),
                                   "content": "x"}) == (0, None)


def test_blocked_returns_deny_with_reason(project):
    code, out = hook(project, "Write", {"file_path": str(project / "config" / ".env"),
                                        "content": f"KEY={SECRET}"})
    assert code == 0
    spec = out["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse"
    assert spec["permissionDecision"] == "deny"
    assert spec["permissionDecisionReason"].startswith("surject blocked tool 'Write'")
    assert "not_glob[0]" in out["systemMessage"]
    events = (project / ".surject" / "events.jsonl").read_text(encoding="utf-8")
    assert SECRET not in events and SECRET not in json.dumps(out)


def test_monitor_mode_warns_without_deciding(project):
    write_policy(project, "monitor")
    code, out = hook(project, "Bash", {"command": "rm -rf build"})
    assert code == 0
    assert "hookSpecificOutput" not in out
    assert out["systemMessage"].startswith("surject flagged Bash")


def test_rate_limit_persists_across_hook_processes(project):
    decisions = []
    for _ in range(3):
        _, out = hook(project, "Bash", {"command": "ls"})
        decisions.append(out["hookSpecificOutput"]["permissionDecision"] if out else "allow")
    assert decisions == ["allow", "allow", "deny"]
    assert (project / ".surject" / "history.json").exists()


def test_no_history_file_without_rate_limits(project):
    (project / "surject.yaml").write_text("tools: {Write: allow}\n", encoding="utf-8")
    hook(project, "Write", {"file_path": "a"})
    assert not (project / ".surject" / "history.json").exists()


def test_no_policy_or_bad_input_stays_out_of_the_way(tmp_path_factory, project):
    empty = tmp_path_factory.mktemp("nopolicy")
    assert hook(empty, "Bash", {"command": "rm -rf /"}) == (0, None)
    out = io.StringIO()
    assert run_claude_code_hook(io.StringIO("not json"), out) == 0
    assert out.getvalue() == ""


def test_broken_policy_warns_user(project):
    (project / "surject.yaml").write_text("mode: sometimes\n", encoding="utf-8")
    code, out = hook(project, "Bash", {"command": "ls"})
    assert code == 0 and "not being checked" in out["systemMessage"]


def test_explicit_policy_path(project, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    code, out = hook(elsewhere, "Bash", {"command": "rm -rf x"},
                     policy=str(project / "surject.yaml"))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_cli_end_to_end(project):
    event = {"hook_event_name": "PreToolUse", "cwd": str(project), "tool_name": "Write",
             "tool_input": {"file_path": str(project / "data" / "raw" / "x.csv")}}
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    r = subprocess.run([sys.executable, "-m", "biject_surject", "hook", "claude-code"],
                       input=json.dumps(event), capture_output=True, text=True, env=env,
                       cwd=project)
    assert r.returncode == 0
    assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_print_config(capsys):
    from biject_surject.cli import main
    assert main(["hook", "claude-code", "--print-config"]) == 0
    out = capsys.readouterr().out
    snippet = json.loads(out[out.index("{"):])
    entry = snippet["hooks"]["PreToolUse"][0]
    assert entry["hooks"][0]["command"] == "surject hook claude-code"
