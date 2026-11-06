"""Write a matrix as an Excel workbook.

Sheets: Matrix (linked counts), Terms (how each input was read), Cells (one row
per pair with its exact query and retrieval time), and Run (how it was made).
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from .matrix import Matrix, heat_step
from .terms import Term

MAX_ROWS = 1_048_576
MAX_COLUMNS = 16_384
MAX_HYPERLINKS = 65_530
MAX_URL_LENGTH = 2_079

LEADING_COLUMNS = 3  # term, documents alone, modifiers with hits

HEAT = ("CDE2FB", "9EC5F4", "6DA7EC", "256ABF", "184F95", "0D366B")
FILLS = [PatternFill("solid", fgColor=color) for color in HEAT]
DARK_FROM = 3  # index of the first fill that needs light text
LIGHT_INK = Font(color="FFFFFF")
MUTED_INK = Font(color="5B6472")
BOLD = Font(bold=True)
UNKNOWN = PatternFill("lightUp", fgColor="9AA3AF")
HEADER = Alignment(wrap_text=True, vertical="bottom")


def fits_in_excel(matrix: Matrix) -> bool:
    return (
        len(matrix.cols) + LEADING_COLUMNS <= MAX_COLUMNS
        and len(matrix.cells) + 1 <= MAX_ROWS
        and len(matrix.rows) + 1 <= MAX_ROWS
    )


def _put_text(sheet: Worksheet, row: int, col: int, value: object):
    """Write as text, so a term like "=A1" is never taken for a formula."""
    cell = sheet.cell(row, col)
    if value is None:
        return cell
    cell.value = ILLEGAL_CHARACTERS_RE.sub("", str(value))
    cell.data_type = "s"
    return cell


def _put_number(sheet: Worksheet, row: int, col: int, value: int | None):
    cell = sheet.cell(row, col, value)
    cell.number_format = "#,##0"
    return cell


def _header_row(sheet: Worksheet, labels: list[str]) -> None:
    for col, label in enumerate(labels, start=1):
        cell = _put_text(sheet, 1, col, label)
        cell.font = BOLD
        cell.alignment = HEADER


def _widths(sheet: Worksheet, widths: list[int]) -> None:
    for col, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(col)].width = width


def _matrix_sheet(sheet: Worksheet, matrix: Matrix) -> bool:
    """Fill the grid. Returns whether counts were given hyperlinks."""
    sheet.title = "Matrix"
    _header_row(
        sheet,
        ["Term", "Documents alone", "Modifiers with hits"] + [c.label for c in matrix.cols],
    )
    low, high = matrix.count_range()
    linked = len(matrix.cells) <= MAX_HYPERLINKS
    for i, term in enumerate(matrix.rows):
        row = i + 2
        _put_text(sheet, row, 1, term.label).font = BOLD
        _put_number(sheet, row, 2, term.total)
        hits = 0
        for j in range(len(matrix.cols)):
            source = matrix.cells[i, j]
            cell = _put_number(sheet, row, j + 1 + LEADING_COLUMNS, source.count)
            if source.count is None:
                cell.fill = UNKNOWN
                continue
            if source.count == 0:
                cell.font = MUTED_INK
            else:
                hits += 1
                index = heat_step(source.count, low, high) - 1
                cell.fill = FILLS[index]
                if index >= DARK_FROM:
                    cell.font = LIGHT_INK
            if linked and len(source.link) <= MAX_URL_LENGTH:
                cell.hyperlink = source.link
        sheet.cell(row, 3, hits)
    last = get_column_letter(len(matrix.cols) + LEADING_COLUMNS)
    sheet.auto_filter.ref = f"A1:{last}{len(matrix.rows) + 1}"
    sheet.freeze_panes = sheet.cell(2, LEADING_COLUMNS + 1)
    _widths(sheet, [32, 12, 12] + [14] * len(matrix.cols))
    return linked


def _terms_sheet(sheet: Worksheet, matrix: Matrix) -> None:
    _header_row(
        sheet,
        ["Role", "Input", "Read as", "Entity type", "Query", "Documents alone", "Pairs with hits", "Note"],
    )

    def write(row: int, role: str, term: Term, hits: int) -> None:
        values = (role, term.raw, term.kind, term.biotype, term.query)
        for col, value in enumerate(values, start=1):
            _put_text(sheet, row, col, value)
        _put_number(sheet, row, 6, term.total)
        _put_number(sheet, row, 7, hits)
        _put_text(sheet, row, 8, term.note)

    row_hits = [0] * len(matrix.rows)
    col_hits = [0] * len(matrix.cols)
    for (i, j), cell in matrix.cells.items():
        if cell.count:
            row_hits[i] += 1
            col_hits[j] += 1
    row = 2
    for term, hits in zip(matrix.rows, row_hits):
        write(row, "term", term, hits)
        row += 1
    for term, hits in zip(matrix.cols, col_hits):
        write(row, "modifier", term, hits)
        row += 1
    sheet.auto_filter.ref = f"A1:H{row - 1}"
    sheet.freeze_panes = "A2"
    _widths(sheet, [10, 32, 10, 11, 40, 12, 10, 60])


def _cells_sheet(sheet: Worksheet, matrix: Matrix) -> None:
    _header_row(sheet, ["Term", "Modifier", "Count", "Query", "Link", "Retrieved (UTC)", "Note"])
    for row, ((i, j), cell) in enumerate(matrix.cells.items(), start=2):
        _put_text(sheet, row, 1, matrix.rows[i].label)
        _put_text(sheet, row, 2, matrix.cols[j].label)
        _put_number(sheet, row, 3, cell.count)
        _put_text(sheet, row, 4, cell.query)
        _put_text(sheet, row, 5, cell.link)
        _put_text(sheet, row, 6, cell.retrieved)
        _put_text(sheet, row, 7, cell.error or cell.note)
    sheet.auto_filter.ref = f"A1:G{len(matrix.cells) + 1}"
    sheet.freeze_panes = "A2"
    _widths(sheet, [28, 28, 11, 50, 50, 24, 60])


def _run_sheet(sheet: Worksheet, info: dict, linked: bool) -> None:
    rows = list(info.items())
    rows.append(("counts", "PubTator3 documents (PubMed abstracts and PMC full text), not PubMed hits"))
    rows.append(("blank cells", "No value is known; see the Note column on the Cells sheet. Blank is not zero."))
    if not linked:
        rows.append(("links", "Too many cells for Excel hyperlinks; see the Link column on the Cells sheet."))
    for row, (key, value) in enumerate(rows, start=1):
        _put_text(sheet, row, 1, key).font = BOLD
        _put_text(sheet, row, 2, value)
    _widths(sheet, [22, 100])


def write_excel(matrix: Matrix, path: Path, info: dict) -> None:
    workbook = Workbook()
    linked = _matrix_sheet(workbook.active, matrix)
    _terms_sheet(workbook.create_sheet("Terms"), matrix)
    _cells_sheet(workbook.create_sheet("Cells"), matrix)
    _run_sheet(workbook.create_sheet("Run"), info, linked)
    workbook.save(path)
