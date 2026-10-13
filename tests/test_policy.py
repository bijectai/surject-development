import pytest

from biject_surject.policy import (
    PolicyError,
    compile_glob,
    load_policy,
    normalize_path,
    parse_duration,
    parse_policy,
)

EXAMPLE = """
version: 1
mode: enforce
unknown_tools: block
tools:
  read_file:
    params:
      path: { glob: ["./data/**", "./src/**"] }
  write_file:
    params:
      path: { not_glob: ["./data/raw/**"] }
  http_get:
    params:
      url: { regex: "^https://api\\\\.example\\\\.com/" }
    rate_limit: { max: 30, per: 60s }
  delete_file: block
  list_dir: allow
rate_limit: { max: 120, per: 1m }
redact:
  names: [ssn]
  patterns: ["MRN-\\\\d+"]
"""


def test_parses_full_example():
    p = parse_policy(EXAMPLE)
    assert p.mode == "enforce"
    assert p.unknown_tools == "block"
    assert set(p.tools) == {"read_file", "write_file", "http_get", "delete_file", "list_dir"}
    assert p.tools["delete_file"].blocked
    assert not p.tools["list_dir"].blocked
    assert p.tools["http_get"].rate_limit.max == 30
    assert p.tools["http_get"].rate_limit.per == 60
    assert p.rate_limit.per == 60
    assert p.redact_names == ("ssn",)
    assert p.redact_patterns[0].search("MRN-123")


def test_empty_file_gives_defaults():
    p = parse_policy("")
    assert p.mode == "monitor"
    assert p.unknown_tools == "warn"
    assert p.tools == {}


@pytest.mark.parametrize("text, where", [
    ("mode: watch", "mode"),
    ("version: 2", "version"),
    ("unknown_tools: maybe", "unknown_tools"),
    ("tools: [a, b]", "tools"),
    ("tools: {t: {paramz: {}}}", "tools.t"),
    ("tools: {t: {params: {p: {regex: '('}}}}", "tools.t.params.p.regex"),
    ("tools: {t: {params: {p: {min: 5, max: 1}}}}", "tools.t.params.p"),
    ("tools: {t: {params: {p: {type: str}}}}", "tools.t.params.p.type"),
    ("tools: {t: {rate_limit: {max: 3}}}", "tools.t.rate_limit"),
    ("tools: {t: {rate_limit: {max: 0, per: 1s}}}", "tools.t.rate_limit.max"),
    ("rate_limit: {max: 3, per: soon}", "rate_limit.per"),
    ("bogus: 1", "policy"),
])
def test_errors_name_their_location(text, where):
    with pytest.raises(PolicyError) as exc:
        parse_policy(text)
    assert exc.value.where == where


def test_invalid_yaml():
    with pytest.raises(PolicyError, match="invalid YAML"):
        parse_policy("tools: {unclosed")


def test_missing_file_mentions_init(tmp_path):
    with pytest.raises(PolicyError, match="surject init"):
        load_policy(tmp_path / "nope.yaml")


def test_load_from_file(tmp_path):
    f = tmp_path / "surject.yaml"
    f.write_text("mode: enforce\n", encoding="utf-8")
    p = load_policy(f)
    assert p.mode == "enforce"
    assert p.source == str(f)


@pytest.mark.parametrize("value, seconds", [
    (30, 30), ("30", 30), ("30s", 30), ("5m", 300), ("1h", 3600),
    ("1d", 86400), ("500ms", 0.5), ("1.5m", 90),
])
def test_durations(value, seconds):
    assert parse_duration(value, "x") == seconds


@pytest.mark.parametrize("value", [0, -1, "1y", True, None])
def test_bad_durations(value):
    with pytest.raises(PolicyError):
        parse_duration(value, "x")


@pytest.mark.parametrize("raw, norm", [
    ("./data/raw/x.csv", "data/raw/x.csv"),
    ("data\\raw\\x.csv", "data/raw/x.csv"),
    ("data/../data/raw/x", "data/raw/x"),
    ("data//raw/./x", "data/raw/x"),
    (".", ""),
])
def test_normalize_path(raw, norm):
    assert normalize_path(raw) == norm


@pytest.mark.parametrize("pattern, path, hit", [
    ("./data/**", "data/a.csv", True),
    ("./data/**", "data/raw/deep/a.csv", True),
    ("./data/**", "database.csv", False),
    ("./data/*", "data/a.csv", True),
    ("./data/*", "data/raw/a.csv", False),
    ("**/*.env", ".env", True),  # dotfiles match: safer for deny rules
    ("**/*.env", "prod.env", True),
    ("**/*.env", "config/prod.env", True),
    ("data/?.csv", "data/a.csv", True),
    ("data/?.csv", "data/ab.csv", False),
    ("data/(x).csv", "data/(x).csv", True),
])
def test_glob(pattern, path, hit):
    assert bool(compile_glob(pattern).regex.match(normalize_path(path))) is hit
