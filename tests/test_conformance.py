"""Run the checker conformance vectors (docs/KERNEL.md) against the Python
reference checker, and make sure the committed vectors are up to date."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from biject_surject import ir

ROOT = Path(__file__).resolve().parent.parent
VECTOR_DIR = ROOT / "tests" / "conformance"


def vectors():
    for path in sorted(VECTOR_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            v = json.loads(line)
            yield pytest.param(v, id=f"{path.stem}:{v['name']}")


ALL = list(vectors())


def test_there_are_vectors():
    assert len(ALL) > 200


@pytest.mark.parametrize("vector", ALL)
def test_reference_checker_conforms(vector):
    verdict = ir.check_request(vector["policy"], vector["request"])
    assert ir.comparable(verdict) == vector["expected"]


def test_vector_names_are_unique():
    names = [p.values[0]["name"] for p in ALL]
    assert len(names) == len(set(names))


def test_vectors_are_up_to_date():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_conformance.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
