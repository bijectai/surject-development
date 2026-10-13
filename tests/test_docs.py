"""Keep the docs honest: every YAML example must be a valid policy, and the
README's quoted output must match what Surject actually prints."""

import io
import re
from pathlib import Path

import pytest

import biject_surject as surject
from biject_surject.policy import parse_policy
from biject_surject.reporter import Reporter, render_snippet

ROOT = Path(__file__).resolve().parent.parent
DOCS = sorted([ROOT / "README.md", *(ROOT / "docs").rglob("*.md")])
YAML_BLOCK = re.compile(r"```yaml\n(.*?)```", re.S)


def yaml_examples():
    for doc in DOCS:
        for i, block in enumerate(YAML_BLOCK.findall(doc.read_text(encoding="utf-8"))):
            yield pytest.param(block, id=f"{doc.relative_to(ROOT).as_posix()}#{i}")


@pytest.mark.parametrize("block", list(yaml_examples()))
def test_yaml_examples_are_valid_policies(block):
    parse_policy(block)


def test_readme_alert_matches_real_output():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    out = io.StringIO()
    policy = parse_policy("paths: {read_only: ['./.git/**', './data/raw/**']}\n"
                          "unknown_tools: allow", root="/proj")
    surject.Monitor(policy, Reporter(stream=out, write_files=False)).check(
        "write_file", {"path": "data/raw/experiment_07.csv", "content": "overwritten"})
    for line in out.getvalue().splitlines():
        assert line in readme, line


def test_reports_doc_snippet_matches_real_format():
    doc = (ROOT / "docs" / "reports.md").read_text(encoding="utf-8")
    event = {
        "time": "2026-10-13T21:54:33.018Z", "tool": "write_file", "decision": "flag",
        "mode": "monitor", "params": {"path": "data/raw/experiment_07.csv"},
        "violations": [{"message": "'path' is inside ./data/raw/**, which is read-only, "
                                   "and 'write_file' looks like it changes files"}],
    }
    for line in render_snippet(event).splitlines():
        assert line in doc, line
