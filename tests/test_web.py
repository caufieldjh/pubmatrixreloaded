"""Check that the web page stays in step with the Python tool."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pubmatrix.cli import LARGE_RUN_REQUESTS, TYPICAL_REQUESTS_PER_SECOND, _parser
from pubmatrix.matrix import HEAT_STEPS, RELATION_TYPES, WEB_SEARCH
from pubmatrix.terms import CONCEPTS, SYNONYM_SEPARATOR

ROOT = Path(__file__).parent.parent
CORE = (ROOT / "docs" / "core.js").read_text(encoding="utf-8")


def js_list(name):
    block = re.search(rf"export const {name} = \[(.*?)\];", CORE, re.S).group(1)
    return tuple(re.findall(r"'([^']+)'", block))


def js_value(name):
    return re.search(rf"export const {name} = '?([^';]+)'?;", CORE).group(1)


def test_lists_match():
    assert js_list("RELATION_TYPES") == RELATION_TYPES
    assert js_list("CONCEPTS") == CONCEPTS


def test_constants_match():
    assert float(js_value("TYPICAL_REQUESTS_PER_SECOND")) == TYPICAL_REQUESTS_PER_SECOND
    assert int(js_value("LARGE_RUN_REQUESTS")) == LARGE_RUN_REQUESTS
    assert int(js_value("HEAT_STEPS")) == HEAT_STEPS
    assert js_value("SYNONYM_SEPARATOR") == SYNONYM_SEPARATOR
    assert js_value("WEB_SEARCH") == WEB_SEARCH


EXAMPLE_IDS = re.findall(r"id: '([a-z-]+)'", CORE)


def test_every_example_file_is_listed():
    files = sorted(p.stem for p in (ROOT / "docs" / "examples").glob("*.json"))
    assert files == sorted(EXAMPLE_IDS) and files


@pytest.mark.parametrize("example", EXAMPLE_IDS)
def test_example_is_a_complete_result(example):
    text = (ROOT / "docs" / "examples" / f"{example}.json").read_text(encoding="utf-8")
    data = json.loads(text)
    assert len(data["cells"]) == len(data["rows"]) * len(data["columns"])
    assert data["run"]["status"] == "complete"
    assert data["run"]["cells_without_value"] == 0
    assert not re.search(r"/home/|/tmp/", text)


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_generated_command_is_accepted_by_the_tool():
    script = """
    import { buildCommand } from './docs/core.js';
    console.log(buildCommand({ output: 'my run', relation: 'treat', termsType: 'chemical',
                               modifiersType: 'disease', quietHours: true }));
    """
    done = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    words = re.findall(r"'[^']*'|\S+", done.stdout)
    args = [w.strip("'") for w in words[words.index("run") :]]
    parsed = _parser().parse_args(args)
    assert parsed.terms_type == "chemical"
    assert parsed.modifiers_type == "disease"
    assert parsed.relation == "treat"
    assert parsed.quiet_hours is True
    assert parsed.output == "my run"


def test_page_policy_allows_what_the_search_needs():
    """The Parquet reader is fetched at run time, and blocking it crashes every query."""
    page = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    policy = re.search(r"connect-src ([^;]+);", page).group(1)
    for host in ("cdn.jsdelivr.net", "extensions.duckdb.org", "huggingface.co", "*.hf.co"):
        assert f"https://{host}" in policy
