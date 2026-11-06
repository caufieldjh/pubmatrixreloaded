"""Build the term-by-modifier matrix."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Iterable
from urllib.parse import quote

from .client import PubTatorClient, PubTatorError
from .terms import Term, resolve_term

WEB_SEARCH = "https://www.ncbi.nlm.nih.gov/research/pubtator3/docsum?text="

# An unknown relation type returns zero documents rather than an error, so only
# types seen returning documents are offered. PubTator3's site also names convert
# and bind, but every query tried for them returned zero.
RELATION_TYPES = (
    "ANY",
    "associate",
    "cause",
    "compare",
    "cotreat",
    "drug_interact",
    "inhibit",
    "interact",
    "negative_correlate",
    "positive_correlate",
    "prevent",
    "stimulate",
    "treat",
)

Progress = Callable[[int, int], None]


@dataclass
class Cell:
    query: str
    count: int | None = None  # None means unknown, never zero
    error: str | None = None

    @property
    def link(self) -> str:
        return WEB_SEARCH + quote(self.query, safe="")


@dataclass
class Matrix:
    rows: list[Term]
    cols: list[Term]
    relation: str | None = None
    cells: dict[tuple[int, int], Cell] = field(default_factory=dict)

    @property
    def failed(self) -> list[Cell]:
        return [cell for cell in self.cells.values() if cell.count is None]

    def count_range(self) -> tuple[int, int]:
        """Smallest and largest positive counts, or (0, 0) if there are none."""
        counts = [c.count for c in self.cells.values() if c.count]
        return (min(counts), max(counts)) if counts else (0, 0)


def cell_query(row: Term, col: Term, relation: str | None) -> str:
    if relation:
        return f"relations:{relation}|{row.query}|{col.query}"
    return f"{row.query} AND {col.query}"


def unique(terms: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(t.strip() for t in terms if t.strip()))


def _prepare_term(raw: str, client: PubTatorClient) -> Term:
    """Resolve a term and count it alone, which also exposes bad entity IDs."""
    try:
        term = resolve_term(raw, client)
    except PubTatorError as exc:
        return Term(raw, "text", "", problem=str(exc))
    try:
        term.total = client.count(term.query)
    except PubTatorError as exc:
        term.problem = str(exc)
        return term
    if term.kind == "entity" and term.total == 0:
        term.problem = f"no documents for {term.query}; the entity ID is probably wrong"
    return term


def _blocked(row: Term, col: Term, relation: str | None) -> str | None:
    for term in (row, col):
        if term.problem:
            return f"{term.raw}: {term.problem}"
        if relation and term.kind != "entity":
            hint = f" (closest: {term.suggestion})" if term.suggestion else ""
            return f"{term.raw}: relation search needs an entity ID{hint}"
    return None


def build_matrix(
    client: PubTatorClient,
    rows: Iterable[str],
    cols: Iterable[str],
    relation: str | None = None,
    workers: int = 8,
    on_terms: Callable[[list[Term]], None] | None = None,
    progress: Progress | None = None,
) -> Matrix:
    """Query every row/column pair. Failures are recorded per cell, not raised.

    Workers overlap slow responses; the client's limiter still caps the request
    rate. Each query takes seconds, so a single thread would run far below it.
    """
    if relation is not None and relation not in RELATION_TYPES:
        raise ValueError(f"unknown relation type: {relation}")
    row_names, col_names = unique(rows), unique(cols)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        names = unique(row_names + col_names)
        terms = dict(zip(names, pool.map(lambda n: _prepare_term(n, client), names)))
        matrix = Matrix(
            [terms[n] for n in row_names], [terms[n] for n in col_names], relation
        )
        if on_terms:
            on_terms(list(terms.values()))

        pending = {}
        for i, row in enumerate(matrix.rows):
            for j, col in enumerate(matrix.cols):
                cell = Cell(cell_query(row, col, relation))
                matrix.cells[i, j] = cell
                cell.error = _blocked(row, col, relation)
                if cell.error:
                    continue
                if row.total == 0 or col.total == 0:
                    cell.count = 0  # a pair cannot exceed either term alone
                    continue
                pending[pool.submit(client.count, cell.query)] = cell

        for done, future in enumerate(as_completed(pending), start=1):
            cell = pending[future]
            try:
                cell.count = future.result()
            except PubTatorError as exc:
                cell.error = str(exc)
            if progress:
                progress(done, len(pending))
    return matrix
