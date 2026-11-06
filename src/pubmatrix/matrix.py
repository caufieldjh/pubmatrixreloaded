"""Build the term-by-modifier matrix."""

from __future__ import annotations

import math
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable
from urllib.parse import quote

from .client import PubTatorClient, PubTatorError, QueryError, Stopped
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

HEAT_STEPS = 6

NOT_FINISHED = "the run stopped before this cell was finished"
INTERRUPTED = "interrupted"


@dataclass
class Cell:
    query: str
    count: int | None = None  # None means unknown, never zero
    error: str | None = None
    retrieved: str | None = None  # when PubTator3 gave this count (UTC)
    note: str | None = None

    @property
    def link(self) -> str:
        return WEB_SEARCH + quote(self.query, safe="")


@dataclass
class Matrix:
    rows: list[Term]
    cols: list[Term]
    relation: str | None = None
    cells: dict[tuple[int, int], Cell] = field(default_factory=dict)
    started: str = ""
    finished: str = ""
    stopped: str | None = None  # why the run ended early, if it did

    @property
    def failed(self) -> list[Cell]:
        return [cell for cell in self.cells.values() if cell.count is None]

    def count_range(self) -> tuple[int, int]:
        """Smallest and largest positive counts, or (0, 0) if there are none."""
        counts = [c.count for c in self.cells.values() if c.count]
        return (min(counts), max(counts)) if counts else (0, 0)


def heat_step(count: int, low: int, high: int) -> int:
    """Map a positive count to steps 1..HEAT_STEPS, log-scaled from low to high.

    Counts span orders of magnitude, so a linear scale would leave all but the
    largest cell looking empty. Anchoring at the smallest count keeps the steps
    useful when every count is large.
    """
    if high <= low:
        return HEAT_STEPS // 2
    fraction = math.log(count / low) / math.log(high / low)
    return 1 + min(HEAT_STEPS - 1, int(HEAT_STEPS * fraction))


def cell_query(row: Term, col: Term, relation: str | None) -> str:
    if relation:
        return f"relations:{relation}|{row.query}|{col.query}"
    return f"{row.query} AND {col.query}"


def unique(terms: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(t.strip() for t in terms if t.strip()))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _prepare_term(raw: str, concept: str | None, client: PubTatorClient) -> Term:
    """Resolve a term and count it alone, which also exposes bad entity IDs."""
    try:
        term = resolve_term(raw, client, concept)
    except PubTatorError as exc:
        return Term(raw, "text", "", problem=str(exc))
    if term.problem:
        return term
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
            return f"{term.raw}: relation search needs a single entity ID{hint}"
    return None


def _record(cell: Cell, future: Future) -> bool:
    """Copy a finished request into its cell. True if the service seems down."""
    try:
        cell.count, cell.retrieved = future.result()
    except Stopped:
        cell.error = NOT_FINISHED
    except QueryError as exc:
        cell.error = str(exc)
    except PubTatorError as exc:
        cell.error = str(exc)
        return True
    return False


def _collect(
    pending: dict[Future, Cell], give_up_after: int, progress: Progress | None
) -> str | None:
    """Fill cells as answers arrive. Returns why it stopped early, if it did."""
    failures_in_a_row = 0
    try:
        for done, future in enumerate(as_completed(pending), start=1):
            if _record(pending[future], future):
                failures_in_a_row += 1
            else:
                failures_in_a_row = 0
            if progress:
                progress(done, len(pending))
            if failures_in_a_row >= give_up_after:
                return f"{failures_in_a_row} requests in a row failed"
    except KeyboardInterrupt:
        return INTERRUPTED
    return None


def build_matrix(
    client: PubTatorClient,
    rows: Iterable[str],
    cols: Iterable[str],
    relation: str | None = None,
    row_type: str | None = None,
    col_type: str | None = None,
    workers: int = 8,
    on_terms: Callable[[list[Term]], None] | None = None,
    progress: Progress | None = None,
) -> Matrix:
    """Query every row/column pair. Failures are recorded per cell, not raised.

    Workers overlap slow responses; the client's limiter still caps the request
    rate. Each query takes seconds, so a single thread would run far below it.
    If the run is interrupted or the service stays down, the matrix comes back
    early with `stopped` set and the unasked cells marked. Answers already cached
    are not lost, so the same call picks up where this one ended.
    """
    if relation is not None and relation not in RELATION_TYPES:
        raise ValueError(f"unknown relation type: {relation}")
    row_keys = [(name, row_type) for name in unique(rows)]
    col_keys = [(name, col_type) for name in unique(cols)]
    keys = list(dict.fromkeys(row_keys + col_keys))
    started = _utc_now()

    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        prepared = pool.map(lambda key: _prepare_term(key[0], key[1], client), keys)
        terms = dict(zip(keys, prepared))
        matrix = Matrix(
            [terms[k] for k in row_keys], [terms[k] for k in col_keys], relation, started=started
        )
        if on_terms:
            on_terms(list(terms.values()))

        pending: dict[Future, Cell] = {}
        for i, row in enumerate(matrix.rows):
            for j, col in enumerate(matrix.cols):
                cell = Cell(cell_query(row, col, relation))
                matrix.cells[i, j] = cell
                cell.error = _blocked(row, col, relation)
                if cell.error:
                    continue
                if row.total == 0 or col.total == 0:
                    empty = row if row.total == 0 else col
                    cell.count = 0  # a pair cannot exceed either term alone
                    cell.note = f"not asked: {empty.raw} has no documents on its own"
                    continue
                pending[pool.submit(client.fetch_count, cell.query)] = cell

        matrix.stopped = _collect(pending, 2 * workers, progress)
        if matrix.stopped:
            client.stop()
    except BaseException:
        client.stop()
        raise
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    for future, cell in pending.items():
        if cell.count is None and cell.error is None:
            if future.done() and not future.cancelled():
                _record(cell, future)
            else:
                cell.error = NOT_FINISHED
    matrix.finished = _utc_now()
    return matrix
