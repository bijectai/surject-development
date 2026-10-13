import json
import os
import subprocess
import sys
import textwrap

import pytest

FAKE_SERVER = """
import json, sys
for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        print(json.dumps({"echo": line.strip()}), flush=True)
        continue
    batch = msg if isinstance(msg, list) else [msg]
    out = []
    for m in batch:
        if "id" not in m:
            continue
        if m.get("method") == "tools/call":
            text = "ran " + m["params"]["name"]
            out.append({"jsonrpc": "2.0", "id": m["id"],
                        "result": {"content": [{"type": "text", "text": text}]}})
        else:
            out.append({"jsonrpc": "2.0", "id": m["id"], "result": {}})
    if out:
        print(json.dumps(out if isinstance(msg, list) else out[0]), flush=True)
"""

POLICY = """
mode: {mode}
tools:
  write_file:
    params:
      path: {{ not_glob: ["./data/raw/**"] }}
"""


def call(i, name, args):
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call",
            "params": {"name": name, "arguments": args}}


def run_proxy(tmp_path, messages, mode="enforce", extra_args=()):
    (tmp_path / "surject.yaml").write_text(POLICY.format(mode=mode), encoding="utf-8")
    (tmp_path / "server.py").write_text(textwrap.dedent(FAKE_SERVER), encoding="utf-8")
    stdin = "".join((m if isinstance(m, str) else json.dumps(m)) + "\n" for m in messages)
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    r = subprocess.run(
        [sys.executable, "-m", "biject_surject", "mcp", *extra_args, "--",
         sys.executable, "server.py"],
        input=stdin, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60,
    )
    replies = [json.loads(l) for l in r.stdout.splitlines() if l.strip()]
    return r, replies


def by_id(replies):
    out = {}
    for r in replies:
        for m in (r if isinstance(r, list) else [r]):
            if "id" in m:
                out[m["id"]] = m
    return out


def test_allowed_calls_and_other_traffic_pass_through(tmp_path):
    r, replies = run_proxy(tmp_path, [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        call(1, "write_file", {"path": "out/a.csv"}),
    ])
    assert r.returncode == 0, r.stderr
    got = by_id(replies)
    assert got[0]["result"] == {}
    assert got[1]["result"]["content"][0]["text"] == "ran write_file"
    assert "[surject]" not in r.stdout  # protocol channel stays clean
    assert "FLAGGED" not in r.stderr and "BLOCKED" not in r.stderr


def test_enforce_blocks_without_reaching_server(tmp_path):
    r, replies = run_proxy(tmp_path, [
        call(1, "write_file", {"path": "data/raw/a.csv", "token": "sk-proj-AbCdEfGhIjKlMnOpQrSt"}),
    ])
    got = by_id(replies)[1]
    assert got["result"]["isError"] is True
    text = got["result"]["content"][0]["text"]
    assert text.startswith("surject blocked tool 'write_file'")
    assert "ran write_file" not in r.stdout
    assert "BLOCKED write_file" in r.stderr
    assert "sk-proj-AbCdEf" not in r.stderr + r.stdout
    assert (tmp_path / ".surject" / "events.jsonl").exists()


def test_monitor_mode_flags_but_forwards(tmp_path):
    r, replies = run_proxy(tmp_path, [call(1, "write_file", {"path": "data/raw/a.csv"})],
                           mode="monitor")
    assert by_id(replies)[1]["result"]["content"][0]["text"] == "ran write_file"
    assert "FLAGGED write_file" in r.stderr


def test_mixed_batch_splits_blocked_from_allowed(tmp_path):
    r, replies = run_proxy(tmp_path, [[
        call(1, "write_file", {"path": "out/ok.csv"}),
        call(2, "write_file", {"path": "data/raw/no.csv"}),
    ]])
    got = by_id(replies)
    assert got[1]["result"]["content"][0]["text"] == "ran write_file"
    assert got[2]["result"]["isError"] is True


def test_unparseable_lines_are_forwarded(tmp_path):
    r, replies = run_proxy(tmp_path, ["not json at all"])
    assert replies == [{"echo": "not json at all"}]


def test_policy_flag_from_another_folder(tmp_path):
    r, replies = run_proxy(tmp_path, [call(1, "write_file", {"path": "data/raw/a.csv"})],
                           extra_args=("--policy", str(tmp_path / "surject.yaml")))
    assert by_id(replies)[1]["result"]["isError"] is True


def test_missing_policy_explains_policy_flag(tmp_path_factory):
    empty = tmp_path_factory.mktemp("no_policy_here")
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    r = subprocess.run([sys.executable, "-m", "biject_surject", "mcp", "--",
                        sys.executable, "-c", "pass"],
                       cwd=empty, env=env, capture_output=True, text=True)
    assert r.returncode == 1
    assert "--policy" in r.stderr
    assert r.stdout == ""  # never pollute the protocol channel
