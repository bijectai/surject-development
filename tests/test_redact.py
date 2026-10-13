import re

import pytest

from biject_surject.policy import parse_policy
from biject_surject.redact import Redactor

R = Redactor()

# Fake but correctly shaped secrets. None of these are real credentials.
SECRETS = {
    "openai": "sk-proj-AbCdEfGhIjKlMnOpQrStUvWx1234",
    "anthropic": "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUv",
    "github_classic": "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
    "github_fine": "github_pat_11ABCDEFG0123456789_abcdefghijklmnop",
    "aws": "AKIAIOSFODNN7EXAMPLE",
    "slack": "xoxb-123456789012-abcdefghijkl",
    "google": "AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q",
    "stripe": "sk_live_" + "a1B2c3D4e5F6g7H8i9J0",
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk",
    "hf": "hf_" + "AbCdEfGhIjKlMnOpQrStUvWxYz012345",
    "random": "Zx9Qw3Er7Ty1Ui5Op2As8Df4Gh6Jk0LmNb",
}


@pytest.mark.parametrize("kind", sorted(SECRETS))
def test_known_secret_shapes_are_hidden_in_text(kind):
    secret = SECRETS[kind]
    out = R.text(f"calling api with {secret} now")
    assert secret not in out
    assert "[REDACTED:" in out
    assert out.startswith("calling api with ")


@pytest.mark.parametrize("kind", sorted(SECRETS))
def test_known_secret_shapes_are_hidden_deep_in_structures(kind):
    secret = SECRETS[kind]
    params = {"request": {"headers": [{"note": f"x {secret} y"}]}, "args": (secret,)}
    out = R.value(params)
    assert secret not in repr(out)


@pytest.mark.parametrize("name", [
    "password", "api_key", "apiKey", "X-Api-Key", "Authorization", "auth_token",
    "client_secret", "SESSION_ID", "cookie", "db_password", "privateKey", "pin",
])
def test_sensitive_names_hide_any_value(name):
    out = R.value({name: "plain-looking-value", "path": "data/x.csv"})
    assert out[name] == "[REDACTED:name]"
    assert out["path"] == "data/x.csv"


@pytest.mark.parametrize("name", ["path", "keyword", "url", "query", "limit", "monkey"])
def test_ordinary_names_are_not_redacted(name):
    assert R.value({name: "hello"})[name] == "hello"


def test_non_string_values_under_sensitive_names():
    out = R.value({"pin": 1234, "token": None, "secret": ""})
    assert out == {"pin": "[REDACTED:name]", "token": None, "secret": ""}


def test_assignments_in_free_text_keep_label():
    out = R.text('connect password=hunter2 user=bob "api_key": "abc123"')
    assert "hunter2" not in out and "abc123" not in out
    assert "password=[REDACTED:assignment]" in out
    assert "user=bob" in out


def test_url_credentials_and_bearer():
    out = R.text("GET https://bob:hunter2@db.internal/x with Bearer abcdefghij123456")
    assert "hunter2" not in out and "abcdefghij123456" not in out
    assert "https://[REDACTED:url-credentials]@db.internal/x" in out


def test_private_key_block():
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nIBAAK\n-----END RSA PRIVATE KEY-----"
    out = R.text(f"key:\n{pem}\nend")
    assert "MIIEow" not in out and "[REDACTED:private-key]" in out


def test_emails_hidden():
    assert "alice@example.com" not in R.text("send to alice@example.com")


def test_git_shas_and_paths_stay_visible():
    sha = "3ea949dd73bb970b55383a095edd482e5dc70dcb"
    text = f"commit {sha} wrote ./data/raw/experiment_2026_10_13.csv"
    assert R.text(text) == text


def test_bytes_and_objects():
    class Client:
        def __repr__(self):
            return f"Client(key={SECRETS['openai']})"

    out = R.value({"blob": b"\x00secret", "client": Client()})
    assert out["blob"] == "[7 bytes]"
    assert SECRETS["openai"] not in out["client"]


def test_deep_nesting_is_truncated_not_crashing():
    v = cur = {}
    for _ in range(50):
        cur["n"] = {}
        cur = cur["n"]
    assert "[TRUNCATED]" in repr(R.value(v))


def test_policy_can_add_names_and_patterns():
    policy = parse_policy('redact: {names: [patient_id], patterns: ["MRN-\\\\d+"]}')
    r = Redactor.for_policy(policy)
    out = r.value({"patient_id": "P-77", "note": "see MRN-12345"})
    assert out["patient_id"] == "[REDACTED:name]"
    assert out["note"] == "see [REDACTED:policy-pattern]"


def test_policy_cannot_disable_builtins():
    with pytest.raises(Exception):
        parse_policy("redact: {enabled: false}")
    r = Redactor.for_policy(parse_policy("redact: {names: [], patterns: []}"))
    assert SECRETS["aws"] not in r.text(SECRETS["aws"])


def test_redacted_output_is_stable():
    once = R.text(f"token={SECRETS['openai']}")
    assert R.text(once) == once
    assert re.fullmatch(r"token=\[REDACTED:[a-z-]+\]", once)
