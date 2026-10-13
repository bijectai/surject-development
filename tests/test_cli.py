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
    assert "write_file" in policy.tools
    assert "created surject.yaml" in capsys.readouterr().out


def test_init_default_policy_catches_env_writes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    main(["init"])
    m = surject.Monitor(load_policy(tmp_path / "surject.yaml"),
                        Reporter(root=tmp_path, stream=io.StringIO()))
    assert not m.check("write_file", {"path": "config/.env"}).ok
    assert not m.check("write_file", {"path": str(tmp_path / ".git" / "HEAD")}).ok
    assert m.check("write_file", {"path": "out/results.csv"}).ok


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
