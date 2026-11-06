import csv
import json

from openpyxl import load_workbook

import pubmatrix.excel
from conftest import FakeApi
from pubmatrix.cli import main
from pubmatrix.matrix import build_matrix
from pubmatrix.output import HEAT_STEPS, heat_step, render_html, write_all


def sample(make_client):
    api = FakeApi({"@A": 5, "@B": 5, "@A AND @B": 3})
    return build_matrix(make_client(api), ["@A"], ["@B", "@Typo"])


def test_csv_leaves_unknown_blank(make_client, tmp_path):
    csv_path, *_ = write_all(sample(make_client), str(tmp_path / "sub" / "m"))
    rows = list(csv.reader(csv_path.open()))
    assert rows == [["term", "@B", "@Typo"], ["@A", "3", ""]]


def test_json_records_null_and_reason(make_client, tmp_path):
    _, json_path, *_ = write_all(sample(make_client), str(tmp_path / "m"))
    data = json.loads(json_path.read_text())
    bad = [c for c in data["cells"] if c["column"] == "@Typo"][0]
    assert bad["count"] is None and "probably wrong" in bad["error"]
    assert data["run"]["mode"] == "co-occurrence"
    assert data["run"]["status"] == "complete"


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


def workbook_for(make_client, tmp_path, api, rows, cols, **kwargs):
    matrix = build_matrix(make_client(api), rows, cols, **kwargs)
    paths = write_all(matrix, str(tmp_path / "m"), "pubmatrix run -t x")
    return load_workbook(paths[-1])


def test_excel_matrix_has_counts_links_and_blanks(make_client, tmp_path):
    api = FakeApi({"@A": 5, "@B": 5, "@A AND @B": 3})
    book = workbook_for(make_client, tmp_path, api, ["@A"], ["@B", "@Typo"])
    assert book.sheetnames == ["Matrix", "Terms", "Cells", "Run"]
    sheet = book["Matrix"]
    assert [c.value for c in sheet[1]] == ["Term", "Documents alone", "Modifiers with hits", "@B", "@Typo"]
    assert [c.value for c in sheet[2]] == ["@A", 5, 1, 3, None]
    assert "pubtator3/docsum" in sheet["D2"].hyperlink.target
    assert sheet.freeze_panes == "D2"


def test_excel_traces_every_cell(make_client, tmp_path):
    api = FakeApi({"@A": 5, "@B": 5, "@A AND @B": 3})
    book = workbook_for(make_client, tmp_path, api, ["@A"], ["@B", "@Typo"])
    good, bad = list(book["Cells"].values)[1:]
    assert good[:4] == ("@A", "@B", 3, "@A AND @B")
    assert good[5]
    assert bad[2] is None and "probably wrong" in bad[6]
    run = dict(book["Run"].values)
    assert run["command"] == "pubmatrix run -t x"
    assert run["status"] == "complete"


def test_excel_counts_hits_for_each_modifier(make_client, tmp_path):
    api = FakeApi({"@A": 5, "@B": 5, "@C": 5, "@A AND @C": 2, "@B AND @C": 1})
    book = workbook_for(make_client, tmp_path, api, ["@A", "@B"], ["@C"])
    rows = list(book["Terms"].values)
    assert rows[3][:2] == ("modifier", "@C")
    assert rows[3][6] == 2


def test_excel_keeps_formula_like_terms_as_text(make_client, tmp_path):
    api = FakeApi({"@A": 5, '"=1+1"': 5})
    book = workbook_for(make_client, tmp_path, api, ['"=1+1"'], ["@A"])
    cell = book["Matrix"]["A2"]
    assert cell.value == "=1+1"
    assert cell.data_type == "s"


def test_excel_drops_links_past_its_limit(make_client, tmp_path, monkeypatch):
    monkeypatch.setattr(pubmatrix.excel, "MAX_HYPERLINKS", 0)
    api = FakeApi({"@A": 5, "@B": 5, "@A AND @B": 3})
    book = workbook_for(make_client, tmp_path, api, ["@A"], ["@B"])
    assert book["Matrix"]["D2"].hyperlink is None
    assert "links" in dict(book["Run"].values)


def test_too_large_for_excel_still_writes_the_rest(make_client, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pubmatrix.excel, "MAX_ROWS", 1)
    matrix = sample(make_client)
    paths = write_all(matrix, str(tmp_path / "m"))
    assert [p.suffix for p in paths] == [".csv", ".json", ".html"]
    assert "too large for Excel" in capsys.readouterr().err


def test_partial_run_is_labeled(make_client, tmp_path):
    matrix = sample(make_client)
    matrix.stopped = "interrupted"
    assert "Partial result" in render_html(matrix)
    _, json_path, *_ = write_all(matrix, str(tmp_path / "m"))
    assert json.loads(json_path.read_text())["run"]["status"] == "partial: interrupted"


def test_cli_warns_about_large_runs(make_client, tmp_path, capsys):
    terms = tmp_path / "t.txt"
    terms.write_text("\n".join(f"@T{i}" for i in range(200)))
    mods = tmp_path / "m.txt"
    mods.write_text("\n".join(f"@M{i}" for i in range(10)))
    prefix = str(tmp_path / "o")
    main(["run", "-T", str(terms), "-M", str(mods), "-o", prefix], client=make_client(FakeApi()))
    err = capsys.readouterr().err
    assert "2000 cells" in err and "--quiet-hours" in err
    assert "210 terms read as" in err


def test_recorded_command_does_not_include_the_local_install_path(
    make_client, tmp_path, monkeypatch, capsys
):
    prefix = str(tmp_path / "m")
    args = ["run", "-t", "@A", "-m", "@B", "-o", prefix]
    monkeypatch.setattr("sys.argv", ["/home/someone/.venv/bin/pubmatrix", *args])
    api = FakeApi({"@A": 5, "@B": 5, "@A AND @B": 3})
    main(args, client=make_client(api))
    data = json.loads((tmp_path / "m.json").read_text())
    assert data["run"]["command"].startswith("pubmatrix run -t @A")
