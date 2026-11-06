import csv
import json

from conftest import FakeApi
from pubmatrix.cli import main
from pubmatrix.matrix import build_matrix
from pubmatrix.output import HEAT_STEPS, heat_step, render_html, write_all


def sample(make_client):
    api = FakeApi({"@A": 5, "@B": 5, "@A AND @B": 3})
    return build_matrix(make_client(api), ["@A"], ["@B", "@Typo"])


def test_csv_leaves_unknown_blank(make_client, tmp_path):
    csv_path, _, _ = write_all(sample(make_client), str(tmp_path / "sub" / "m"))
    rows = list(csv.reader(csv_path.open()))
    assert rows == [["term", "@B", "@Typo"], ["@A", "3", ""]]


def test_json_records_null_and_reason(make_client, tmp_path):
    _, json_path, _ = write_all(sample(make_client), str(tmp_path / "m"))
    data = json.loads(json_path.read_text())
    bad = [c for c in data["cells"] if c["column"] == "@Typo"][0]
    assert bad["count"] is None and "probably wrong" in bad["error"]
    assert data["mode"] == "co-occurrence"


def test_html_escapes_term_text(make_client):
    api = FakeApi({"@A": 5, '"<script>x"': 5})
    html = render_html(build_matrix(make_client(api), ["@A"], ['"<script>x"']))
    assert "<script>x" not in html
    assert "&lt;script&gt;x" in html


def test_heat_step_stays_in_range():
    assert heat_step(1878, 1878, 92807) == 1
    assert heat_step(92807, 1878, 92807) == HEAT_STEPS
    assert 1 < heat_step(13216, 1878, 92807) < HEAT_STEPS


def test_heat_step_handles_a_single_value():
    assert 1 <= heat_step(5, 5, 5) <= HEAT_STEPS


def test_cli_success_writes_outputs(make_client, tmp_path, capsys):
    api = FakeApi({"@GENE_BRCA1": 5, "@B": 5, "@GENE_BRCA1 AND @B": 3})
    prefix = str(tmp_path / "m")
    code = main(["run", "-t", "BRCA1", "-m", "@B", "-o", prefix], client=make_client(api))
    assert code == 0
    assert (tmp_path / "m.html").exists()
    assert "@GENE_BRCA1" in capsys.readouterr().err


def test_cli_exit_code_reports_missing_cells(make_client, tmp_path, capsys):
    prefix = str(tmp_path / "m")
    code = main(["run", "-t", "@A", "-m", "@Typo", "-o", prefix], client=make_client(FakeApi()))
    assert code == 1
    assert (tmp_path / "m.csv").exists()
    assert "1 of 1 cells have no value" in capsys.readouterr().err


def test_cli_needs_both_lists(make_client, capsys):
    assert main(["run", "-t", "@A"], client=make_client(FakeApi())) == 2


def test_cli_resolve_shows_candidates(make_client, capsys):
    assert main(["resolve", "apoptosis"], client=make_client(FakeApi())) == 0
    assert "@DISEASE_Malformations" in capsys.readouterr().out
