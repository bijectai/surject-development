import pytest

from biject_surject.checker import ToolCall, check
from biject_surject.paths import changes_files, is_path_param, path_args, words
from biject_surject.policy import PolicyError, parse_policy

POLICY = """
unknown_tools: allow
paths:
  protect: ["**/.env", "~/.ssh/**"]
  read_only: ["./data/raw/**"]
  params: [notebook]
"""


def run(tool, params, text=POLICY, root="/proj"):
    return check(parse_policy(text, root=root), ToolCall(tool, params))


def rules(v):
    return [x.rule for x in v.violations]


@pytest.mark.parametrize("name, expected", [
    ("NotebookEdit", ("notebook", "edit")),
    ("mcp__fs__read_file", ("mcp", "fs", "read", "file")),
    ("str_replace_based_edit_tool", ("str", "replace", "based", "edit", "tool")),
    ("LS", ("ls",)),
])
def test_words(name, expected):
    assert words(name) == expected


@pytest.mark.parametrize("tool, writes", [
    ("Write", True), ("Edit", True), ("MultiEdit", True), ("delete_file", True),
    ("mcp__github__create_or_update_file", True), ("apply_patch", True),
    ("Read", False), ("read_file", False), ("Glob", False), ("list_directory", False),
    ("mcp__fs__search_files", False),
    ("frobnicate", True),          # unclear: assume it changes files
    ("read_and_write", True),      # any write word wins
])
def test_changes_files(tool, writes):
    assert changes_files(tool) is writes


@pytest.mark.parametrize("name, hit", [
    ("path", True), ("file_path", True), ("filePath", True), ("FilePath", True),
    ("output_dir", True), ("notebook_path", True), ("destination", True),
    ("content", False), ("url", False), ("command", False), ("pathological", False),
])
def test_is_path_param(name, hit):
    assert is_path_param(name) is hit


def test_path_args_walks_nested_values_and_skips_urls():
    params = {
        "path": "a.txt",
        "edits": [{"path": "b.txt", "content": "c.txt"}, {"file_path": "file:///d.txt"}],
        "paths": ["e.txt", "f.txt"],
        "source": "https://example.com/x",
        "options": {"output_dir": "out"},
        "count": 3,
    }
    got = {(label, top, p) for label, top, p in path_args(params)}
    assert got == {
        ("path", "path", "a.txt"),
        ("edits[0].path", "edits", "b.txt"),
        ("edits[1].file_path", "edits", "/d.txt"),
        ("paths[0]", "paths", "e.txt"),
        ("paths[1]", "paths", "f.txt"),
        ("options.output_dir", "options", "out"),
    }


def test_protect_applies_to_any_tool_and_to_reads():
    for tool in ("write_file", "Read", "anything"):
        v = run(tool, {"path": "config/.env"})
        assert rules(v) == ["paths.protect[0]"], tool


def test_protect_names_the_argument_not_the_value():
    v = run("Write", {"file_path": "/proj/app/.env", "content": "SECRET=1"})
    msg = v.violations[0].message
    assert "'file_path'" in msg and "**/.env" in msg
    assert "/proj/app" not in msg and v.violations[0].param == "file_path"


def test_read_only_allows_reads_blocks_writes():
    assert run("read_file", {"path": "data/raw/x.csv"}).ok
    v = run("write_file", {"path": "/proj/data/raw/x.csv"})
    assert rules(v) == ["paths.read_only[0]"]
    assert "looks like it changes files" in v.violations[0].message


def test_home_folder_patterns(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    v = run("Read", {"file_path": str(tmp_path / ".ssh" / "id_rsa")})
    assert rules(v) == ["paths.protect[1]"]
    assert rules(run("Read", {"file_path": "~/.ssh/config"})) == ["paths.protect[1]"]


def test_windows_drive_paths_match_case_insensitively():
    text = 'paths: {protect: ["C:/Secrets/**"]}\nunknown_tools: allow'
    assert not run("Read", {"path": r"c:\secrets\key.txt"}, text=text).ok


def test_extra_param_names():
    v = run("save", {"notebook": "data/raw/n.ipynb"})
    assert rules(v) == ["paths.read_only[0]"]


def test_each_hit_reported_once_per_argument():
    v = run("write_file", {"path": "x/.env", "backup_path": "y/.env"})
    assert rules(v) == ["paths.protect[0]", "paths.protect[0]"]
    assert [x.message.split("'")[1] for x in v.violations] == ["path", "backup_path"]


def test_blocked_tool_reports_only_the_block():
    text = POLICY + "tools: {rm: block}\n"
    assert rules(run("rm", {"path": ".env"}, text=text)) == ["tools.rm"]


def test_path_rules_combine_with_tool_rules():
    text = POLICY + "tools: {write_file: {params: {path: {glob: ['./out/**']}}}}\n"
    v = run("write_file", {"path": "config/.env"}, text=text)
    assert sorted(rules(v)) == ["paths.protect[0]", "tools.write_file.params.path.glob"]


def test_no_path_rules_means_no_path_checks():
    assert run("write_file", {"path": ".env"}, text="unknown_tools: allow").ok


@pytest.mark.parametrize("text, where", [
    ("paths: [a]", "paths"),
    ("paths: {deny: [a]}", "paths"),
    ("paths: {protect: 3}", "paths.protect"),
])
def test_paths_section_errors(text, where):
    with pytest.raises(PolicyError) as exc:
        parse_policy(text)
    assert exc.value.where == where
