"""`checker: lean`: talking to an external checker over the docs/KERNEL.md
protocol, and what happens when it fails."""

import io
import subprocess
import sys
from pathlib import Path

import pytest

import biject_surject as surject
from biject_surject.checker import ALLOW, BLOCK, FLAG
from biject_surject.kernel import ExternalChecker, KernelError, kernel_command
from biject_surject.policy import PolicyError, parse_policy
from biject_surject.reporter import Reporter

FAKE = [sys.executable, str(Path(__file__).with_name("fake_kernel.py"))]

POLICY = """
checker: lean
mode: {mode}
unknown_tools: allow
paths: {{protect: ["**/.env"]}}
tools:
  t: {{rate_limit: {{max: 1, per: 1h}}}}
"""


def monitor(tmp_path, monkeypatch, behaviour="ok", mode="monitor", timeout=None):
    monkeypatch.setenv("FAKE_KERNEL", behaviour)
    policy = parse_policy(POLICY.format(mode=mode), root=str(tmp_path))
    stream = io.StringIO()
    m = surject.Monitor(policy, Reporter(root=tmp_path, stream=stream, write_files=False))
    m._external = ExternalChecker(policy, command=FAKE, **({"timeout": timeout} if timeout else {}))
    return m, stream


@pytest.fixture(autouse=True)
def _close_kernels():
    made = []
    orig = ExternalChecker.__init__

    def track(self, *a, **kw):
        orig(self, *a, **kw)
        made.append(self)
    ExternalChecker.__init__ = track
    yield
    ExternalChecker.__init__ = orig
    for k in made:
        k.close()


def test_checker_option_is_validated():
    assert parse_policy("checker: lean").checker == "lean"
    assert parse_policy("").checker == "python"
    with pytest.raises(PolicyError) as exc:
        parse_policy("checker: z3")
    assert exc.value.where == "checker"


def test_kernel_verdicts_are_used(tmp_path, monkeypatch):
    m, _ = monitor(tmp_path, monkeypatch)
    v = m.check("Read", {"path": "a/.env"})
    assert v.decision == FLAG
    assert v.checker == "fake-kernel@1"
    assert [x.rule for x in v.violations] == ["paths.protect[0]"]
    assert m.check("Read", {"path": "ok.txt"}).decision == ALLOW
    assert m._external.name == "fake-kernel@1"


def test_kernel_process_is_reused(tmp_path, monkeypatch):
    m, _ = monitor(tmp_path, monkeypatch)
    m.check("Read", {"path": "x"})
    pid = m._external._proc.pid
    m.check("Read", {"path": "y"})
    assert m._external._proc.pid == pid


def test_rate_limits_reach_the_kernel(tmp_path, monkeypatch):
    m, _ = monitor(tmp_path, monkeypatch)
    assert [m.check("t").decision for _ in range(2)] == [ALLOW, FLAG]


def test_missing_messages_borrow_reference_wording(tmp_path, monkeypatch):
    m, _ = monitor(tmp_path, monkeypatch, "no_messages")
    v = m.check("Read", {"path": ".env"})
    assert v.violations[0].message == "'path' must not touch **/.env (protected path)"


def test_kernel_decides_even_when_it_disagrees(tmp_path, monkeypatch):
    m, _ = monitor(tmp_path, monkeypatch, "allow_all")
    assert m.check("Read", {"path": ".env"}).decision == ALLOW


@pytest.mark.parametrize("behaviour", ["garbage", "error", "crash", "wrong_contract"])
def test_monitor_mode_falls_back_to_python_with_one_warning(tmp_path, monkeypatch, capsys, behaviour):
    m, _ = monitor(tmp_path, monkeypatch, behaviour)
    v1 = m.check("Read", {"path": ".env"})
    v2 = m.check("Read", {"path": ".env"})
    assert v1.decision == v2.decision == FLAG
    assert v1.checker.startswith("python-reference@")
    err = capsys.readouterr().err
    assert err.count("kernel unavailable") == 1
    assert "using the Python reference checker" in err


@pytest.mark.parametrize("behaviour", ["garbage", "error", "crash", "wrong_contract"])
def test_enforce_mode_blocks_when_the_kernel_fails(tmp_path, monkeypatch, behaviour):
    m, _ = monitor(tmp_path, monkeypatch, behaviour, mode="enforce")
    with pytest.raises(surject.PolicyViolation, match="kernel could not check"):
        m.enforce("Read", {"path": "harmless.txt"})


def test_timeout(tmp_path, monkeypatch):
    m, _ = monitor(tmp_path, monkeypatch, "slow", mode="enforce", timeout=0.5)
    v = m.check("Read", {"path": "x"})
    assert v.decision == BLOCK and "no reply within 0.5s" in v.violations[0].message


def test_missing_kernel_program(tmp_path, monkeypatch, capsys):
    policy = parse_policy(POLICY.format(mode="monitor"), root=str(tmp_path))
    m = surject.Monitor(policy, Reporter(root=tmp_path, stream=io.StringIO(), write_files=False))
    m._external = ExternalChecker(policy, command=["surject-kernel-that-does-not-exist"])
    assert m.check("Read", {"path": ".env"}).decision == FLAG
    assert "can't start" in capsys.readouterr().err


def test_kernel_command_from_environment(monkeypatch):
    monkeypatch.delenv("SURJECT_KERNEL", raising=False)
    assert kernel_command() == ["surject-kernel"]
    monkeypatch.setenv("SURJECT_KERNEL", "lean-kernel --fast")
    assert kernel_command() == ["lean-kernel", "--fast"]


def test_monitor_builds_external_checker_from_policy(tmp_path):
    m = surject.Monitor(parse_policy("checker: lean"), Reporter(root=tmp_path, stream=io.StringIO()))
    assert isinstance(m._external, ExternalChecker)
    assert surject.Monitor(parse_policy(""))._external is None


def test_fake_kernel_passes_the_conformance_vectors():
    """The protocol round trip loses nothing: every vector through the fake
    kernel gives the expected verdict."""
    import json
    from biject_surject import ir
    root = Path(__file__).resolve().parent
    proc = subprocess.Popen(FAKE, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                            encoding="utf-8")

    def ask(msg):
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())

    try:
        assert ask({"type": "hello", "contract": 1})["contract"] == 1
        n = 0
        for path in sorted((root / "conformance").glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                v = json.loads(line)
                ask({"type": "load", "policy_id": v["name"], "policy": v["policy"]})
                reply = ask({"type": "check", "policy_id": v["name"], "request": v["request"]})
                assert ir.comparable(reply["verdict"]) == v["expected"], v["name"]
                n += 1
        assert n > 200
    finally:
        proc.stdin.close()
        proc.wait(timeout=10)
