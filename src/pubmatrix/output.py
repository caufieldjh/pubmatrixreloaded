"""Write a matrix as CSV, JSON, and a self-contained sortable HTML page."""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from . import __version__
from .client import API_BASE
from .excel import fits_in_excel, write_excel
from .matrix import HEAT_STEPS, Matrix, heat_step
from .terms import Term



def write_csv(matrix: Matrix, path: Path) -> None:
    """Plain numeric grid for spreadsheets. Unknown cells are blank, not zero."""
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["term"] + [col.label for col in matrix.cols])
        for i, row in enumerate(matrix.rows):
            counts = [matrix.cells[i, j].count for j in range(len(matrix.cols))]
            writer.writerow([row.label] + ["" if c is None else c for c in counts])


def _term_dict(term: Term) -> dict:
    return {
        "input": term.raw,
        "kind": term.kind,
        "query": term.query,
        "name": term.name,
        "biotype": term.biotype,
        "suggestion": term.suggestion,
        "total": term.total,
        "note": term.note,
    }


def run_info(matrix: Matrix, command: str | None = None) -> dict:
    """What it takes to trace a result back to how it was made."""
    return {
        "tool": f"pubmatrix-reloaded {__version__}",
        "command": command,
        "source": f"PubTator3 ({API_BASE})",
        "mode": "relation" if matrix.relation else "co-occurrence",
        "relation": matrix.relation,
        "started": matrix.started,
        "finished": matrix.finished,
        "status": f"partial: {matrix.stopped}" if matrix.stopped else "complete",
        "cells": len(matrix.cells),
        "cells_without_value": len(matrix.failed),
    }


def to_dict(matrix: Matrix, command: str | None = None) -> dict:
    return {
        "run": run_info(matrix, command),
        "rows": [_term_dict(t) for t in matrix.rows],
        "columns": [_term_dict(t) for t in matrix.cols],
        "cells": [
            {
                "row": matrix.rows[i].label,
                "column": matrix.cols[j].label,
                "count": cell.count,
                "query": cell.query,
                "link": cell.link,
                "retrieved": cell.retrieved,
                "error": cell.error,
                "note": cell.note,
            }
            for (i, j), cell in matrix.cells.items()
        ],
    }


def write_json(matrix: Matrix, path: Path, command: str | None = None) -> None:
    text = json.dumps(to_dict(matrix, command), indent=2, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")


def _header(term: Term) -> str:
    total = "?" if term.total is None else f"{term.total:,}"
    return f'{escape(term.label)}<small>{total} docs</small>'


def _cell_html(matrix: Matrix, i: int, j: int, low: int, high: int) -> str:
    cell = matrix.cells[i, j]
    pair = f"{matrix.rows[i].label} × {matrix.cols[j].label}"
    if cell.count is None:
        title = escape(f"{pair}: no value. {cell.error}", quote=True)
        return f'<td class="none" title="{title}">—</td>'
    title = escape(f"{pair}: {cell.count:,} documents", quote=True)
    link = escape(cell.link, quote=True)
    step = heat_step(cell.count, low, high) if cell.count else 0
    return (
        f'<td class="h{step}" data-v="{cell.count}" title="{title}">'
        f'<a href="{link}" target="_blank" rel="noopener">{cell.count:,}</a></td>'
    )


def _terms_table(matrix: Matrix) -> str:
    seen, rows = set(), []
    for term in matrix.rows + matrix.cols:
        if term.raw in seen:
            continue
        seen.add(term.raw)
        total = "" if term.total is None else f"{term.total:,}"
        rows.append(
            f"<tr><td>{escape(term.raw)}</td><td>{escape(term.kind)}</td>"
            f"<td><code>{escape(term.query)}</code></td>"
            f'<td class="num">{total}</td><td>{escape(term.note)}</td></tr>'
        )
    return "\n".join(rows)


_STYLE = """
:root {
  --surface: #ffffff; --ink: #14181f; --muted: #5b6472; --line: #dfe3e9;
  --h1: #cde2fb; --h2: #9ec5f4; --h3: #6da7ec;
  --h4: #256abf; --h5: #184f95; --h6: #0d366b;
  --ink-low: #14181f; --ink-high: #ffffff;
}
@media (prefers-color-scheme: dark) {
  :root {
    --surface: #16181d; --ink: #e8eaee; --muted: #a0a8b4; --line: #333842;
    --h1: #0d366b; --h2: #184f95; --h3: #256abf;
    --h4: #6da7ec; --h5: #9ec5f4; --h6: #cde2fb;
    --ink-low: #ffffff; --ink-high: #14181f;
  }
}
body { margin: 0; padding: 24px 16px; background: var(--surface); color: var(--ink);
  font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1200px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 32px 0 8px; }
p { margin: 4px 0; color: var(--muted); }
.scroll { overflow-x: auto; margin-top: 16px; }
table { border-collapse: separate; border-spacing: 2px; }
th { font-weight: 600; text-align: left; vertical-align: bottom; padding: 4px 8px; }
th small { display: block; font-weight: 400; color: var(--muted); }
th button { all: unset; cursor: pointer; display: block; }
th button:focus-visible { outline: 2px solid var(--h3); }
th[aria-sort="descending"] small::after { content: " ↓"; }
th[aria-sort="ascending"] small::after { content: " ↑"; }
tbody th { position: sticky; left: 0; background: var(--surface); }
#matrix td { min-width: 72px; padding: 0; text-align: right;
  font-variant-numeric: tabular-nums; border-radius: 4px; }
#matrix td a { display: block; padding: 8px 10px; color: inherit; text-decoration: none; }
#matrix td a:hover { text-decoration: underline; }
#matrix td a:focus-visible { outline: 2px solid var(--ink); outline-offset: -2px; }
td.h0 { color: var(--muted); }
td.h1 { background: var(--h1); color: var(--ink-low); }
td.h2 { background: var(--h2); color: var(--ink-low); }
td.h3 { background: var(--h3); color: var(--ink-low); }
td.h4 { background: var(--h4); color: var(--ink-high); }
td.h5 { background: var(--h5); color: var(--ink-high); }
td.h6 { background: var(--h6); color: var(--ink-high); }
#matrix td.none { padding: 8px 10px; color: var(--muted); cursor: help;
  outline: 1px dashed var(--line); outline-offset: -1px; }
.legend { display: flex; align-items: center; gap: 2px; margin-top: 12px;
  font-size: 13px; color: var(--muted); }
.legend i { width: 28px; height: 12px; border-radius: 2px; }
.legend span { margin: 0 6px; }
#terms { border-spacing: 0; width: 100%; font-size: 14px; }
#terms th, #terms td { padding: 6px 12px 6px 0; border-bottom: 1px solid var(--line);
  text-align: left; vertical-align: top; }
#terms .num { text-align: right; font-variant-numeric: tabular-nums; }
code { font-size: 13px; }
.partial { color: var(--ink); font-weight: 600; }
"""

_SCRIPT = """
const table = document.getElementById('matrix');
const value = (row, col) => {
  const cell = row.cells[col];
  return cell.dataset.v === undefined ? null : Number(cell.dataset.v);
};
table.querySelectorAll('thead button').forEach((button) => {
  button.addEventListener('click', () => {
    const th = button.closest('th');
    const col = th.cellIndex;
    const sign = th.getAttribute('aria-sort') === 'descending' ? 1 : -1;
    table.querySelectorAll('thead th').forEach((h) => h.removeAttribute('aria-sort'));
    th.setAttribute('aria-sort', sign < 0 ? 'descending' : 'ascending');
    const body = table.tBodies[0];
    const rows = [...body.rows].sort((a, b) => {
      if (col === 0) return -sign * a.cells[0].textContent.localeCompare(b.cells[0].textContent);
      const x = value(a, col), y = value(b, col);
      if (x === null || y === null) return (x === null) - (y === null);
      return sign * (x - y);
    });
    rows.forEach((row) => body.appendChild(row));
  });
});
"""


def render_html(matrix: Matrix) -> str:
    low, high = matrix.count_range()
    head = "".join(
        f'<th scope="col"><button type="button">{_header(col)}</button></th>'
        for col in matrix.cols
    )
    body = "\n".join(
        f'<tr><th scope="row">{_header(row)}</th>'
        + "".join(
            _cell_html(matrix, i, j, low, high) for j in range(len(matrix.cols))
        )
        + "</tr>"
        for i, row in enumerate(matrix.rows)
    )
    mode = (
        f"Documents with a <b>{escape(matrix.relation)}</b> relation between the pair"
        if matrix.relation
        else "Documents mentioning both terms"
    )
    swatches = "".join(
        f'<i style="background: var(--h{n})"></i>' for n in range(1, HEAT_STEPS + 1)
    )
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    partial = (
        f'<p class="partial">Partial result: {len(matrix.failed):,} of '
        f"{len(matrix.cells):,} cells have no value. Run the same command again to continue.</p>"
        if matrix.stopped
        else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PubMatrix Reloaded</title>
<style>{_STYLE}</style>
</head>
<body>
<main>
<h1>PubMatrix Reloaded</h1>
<p>{mode}, from PubTator3 (PubMed abstracts and PMC full text). Generated {generated}.</p>
{partial}
<p>Select a count to open those documents in PubTator3. Select a column heading to sort.
— means no value is known; hover for the reason.</p>
<div class="scroll">
<table id="matrix">
<thead><tr><th scope="col"><button type="button">Term<small>docs alone</small></button></th>{head}</tr></thead>
<tbody>
{body}
</tbody>
</table>
</div>
<div class="legend"><span>{low:,}</span>{swatches}<span>{high:,} (log scale; 0 is unshaded)</span></div>
<h2>How each term was read</h2>
<div class="scroll">
<table id="terms">
<thead><tr><th>Input</th><th>Read as</th><th>Query</th><th>Documents</th><th>Note</th></tr></thead>
<tbody>
{_terms_table(matrix)}
</tbody>
</table>
</div>
</main>
<script>{_SCRIPT}</script>
</body>
</html>
"""


def write_html(matrix: Matrix, path: Path) -> None:
    path.write_text(render_html(matrix), encoding="utf-8")


def write_all(matrix: Matrix, prefix: str, command: str | None = None) -> list[Path]:
    base = Path(prefix)
    base.parent.mkdir(parents=True, exist_ok=True)
    csv_path, json_path, html_path, xlsx_path = (
        base.with_name(base.name + ext) for ext in (".csv", ".json", ".html", ".xlsx")
    )
    write_csv(matrix, csv_path)
    write_json(matrix, json_path, command)
    write_html(matrix, html_path)
    paths = [csv_path, json_path, html_path]
    if fits_in_excel(matrix):
        write_excel(matrix, xlsx_path, run_info(matrix, command))
        paths.append(xlsx_path)
    else:
        print("This matrix is too large for Excel; skipping the .xlsx file.", file=sys.stderr)
    return paths
