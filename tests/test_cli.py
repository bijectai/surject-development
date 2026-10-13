import io
import json

import pytest

import biject_surject as surject
from biject_surject import __version__
from biject_surject.cli import main
from biject_surject.policy import load_policy
from biject_surject.reporter import Reporter


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"surject {__version__}"


def test_no_args_prints_help(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "usage: surject" in out
    for cmd in ("init", "validate", "report"):
        assert cmd in out


def test_init_writes_valid_policy(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    policy = load_policy(tmp_path / "surject.yaml")
    assert policy.mode == "monitor"
    assert policy.paths.protect and policy.paths.read_only
    assert "created surject.yaml" in capsys.readouterr().out


@pytest.mark.parametrize("tool, params", [
    ("write_file", {"path": "config/.env"}),                        # LangChain-style
    ("Write", {"file_path": "{root}/.env", "content": "x"}),        # Claude Code
    ("Read", {"file_path": "{root}/certs/server.pem"}),             # reads count too
    ("mcp__fs__edit_file", {"edits": [{"path": ".env.local"}]}),    # nested MCP args
    ("save", {"destination": "~/.ssh/authorized_keys"}),            # home folder
    ("Edit", {"file_path": "{root}/.git/config"}),                  # read-only dir
    ("Bash", {"command": "cat .env"}),                              # inside a shell command
    ("Bash", {"command": "rm -rf .git"}),                           # whole read-only folder
])
def test_default_policy_catches_risky_paths_whatever_the_tool_name(tmp_path, monkeypatch, tool, params):
    monkeypatch.chdir(tmp_path)
    main(["init"])
    m = surject.Monitor(load_policy(tmp_path / "surject.yaml"),
                        Reporter(root=tmp_path, stream=io.StringIO()))
    params = json.loads(json.dumps(params).replace("{root}", tmp_path.as_posix()))
    assert not m.check(tool, params).ok


@pytest.mark.parametrize("tool, params", [
    ("write_file", {"path": "out/results.csv"}),
    ("Read", {"file_path": "{root}/.git/HEAD"}),     # reading .git is fine
    ("Bash", {"command": "ls -la && git status && cat README.md"}),
    ("Bash", {"command": "grep -r TODO .git"}),      # reading .git is fine
    ("http_get", {"url": "https://example.com/.env"}),
])
def test_default_policy_stays_quiet_on_normal_calls(tmp_path, monkeypatch, tool, params):
    monkeypatch.chdir(tmp_path)
    main(["init"])
    m = surject.Monitor(load_policy(tmp_path / "surject.yaml"),
                        Reporter(root=tmp_path, stream=io.StringIO()))
    params = json.loads(json.dumps(params).replace("{root}", tmp_path.as_posix()))
    assert m.check(tool, params).ok


def test_init_refuses_to_overwrite(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "surject.yaml").write_text("mode: enforce\n", encoding="utf-8")
    assert main(["init"]) == 1
    assert "already exists" in capsys.readouterr().err
    assert main(["init", "--force"]) == 0
    assert load_policy(tmp_path / "surject.yaml").mode == "monitor"


def test_init_adds_state_dir_to_existing_gitignore_once(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".gitignore").write_text("node_modules/", encoding="utf-8")
    main(["init"])
    main(["init", "--force"])
    text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert text.count(".surject/") == 1
    assert text.startswith("node_modules/\n")


def test_init_does_not_create_gitignore(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    main(["init"])
    assert not (tmp_path / ".gitignore").exists()


def test_validate_ok_and_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    main(["init"])
    capsys.readouterr()
    assert main(["validate"]) == 0
    assert ": ok" in capsys.readouterr().out

    bad = tmp_path / "bad.yaml"
    bad.write_text("mode: sometimes\n", encoding="utf-8")
    assert main(["validate", str(bad)]) == 1
    assert "mode: expected one of monitor, enforce" in capsys.readouterr().err


def test_validate_without_policy(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["validate"]) == 1
    assert "surject init" in capsys.readouterr().err


def test_report_lists_and_prints(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "surject.yaml").write_text("tools: {rm: block}\n", encoding="utf-8")
    assert main(["report"]) == 0
    assert "nothing caught yet" in capsys.readouterr().out

    m = surject.Monitor(load_policy(tmp_path / "surject.yaml"),
                        Reporter(root=tmp_path, stream=io.StringIO()))
    m.check("rm", {"path": "x"})
    event_id = json.loads((tmp_path / ".surject" / "events.jsonl").read_text().splitlines()[0])["id"]

    assert main(["report"]) == 0
    listing = capsys.readouterr().out
    assert event_id in listing and "tools.rm" in listing

    assert main(["report", event_id[:12]]) == 0
    assert "# Surject caught `rm`" in capsys.readouterr().out

    assert main(["report", "nope"]) == 1


@pytest.fixture
def caught(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "surject.yaml").write_text(
        "paths: {read_only: ['./data/raw/**']}\nunknown_tools: allow\n", encoding="utf-8")
    m = surject.Monitor(load_policy(tmp_path / "surject.yaml"),
                        Reporter(root=tmp_path, stream=io.StringIO()))
    m.check("write_file", {"path": "data/raw/exp_07.csv", "api_key": "sk-proj-AbCdEfGhIjKlMnOpQrSt"})
    return tmp_path


def test_report_last_short_snippet(caught, capsys):
    assert main(["report", "last", "--short"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[0] == "My agent tried to call `write_file` on data/raw/exp_07.csv, and Surject caught it."
    assert out[1].startswith("Why: 'path' is inside ./data/raw/**, which is read-only")
    assert out[2].startswith("Verdict: flagged (monitor mode, so the call still ran)")
    assert out[3] == "Caught by Surject: pip install biject-surject"
    assert "sk-proj" not in "\n".join(out)


def test_report_copy_uses_clipboard(caught, capsys, monkeypatch):
    copied = []
    monkeypatch.setattr("biject_surject.clipboard.copy", lambda text: copied.append(text) or "clip")
    assert main(["report", "last", "--copy", "--short"]) == 0
    assert copied and copied[0].startswith("My agent tried")
    assert "copied to clipboard (short report)" in capsys.readouterr().err


def test_report_copy_falls_back_to_printing(caught, capsys, monkeypatch):
    monkeypatch.setattr("biject_surject.clipboard.copy", lambda text: None)
    assert main(["report", "last", "--copy"]) == 0
    captured = capsys.readouterr()
    assert "no clipboard tool found" in captured.err
    assert captured.out.startswith("# Surject caught `write_file`")


def test_report_last_with_nothing_caught(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["report", "last"]) == 1
    assert "nothing caught yet" in capsys.readouterr().err


def test_report_copy_needs_an_id(caught, capsys):
    assert main(["report", "--copy"]) == 2


def test_template_has_no_invalid_escapes():
    import warnings
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "src" / "biject_surject" / "templates.py").read_text(encoding="utf-8")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        compile(src, "templates.py", "exec")
