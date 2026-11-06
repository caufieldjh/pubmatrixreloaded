"""Command line interface."""

from __future__ import annotations

import argparse
import shlex
import signal
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import __version__
from .client import (
    Cache,
    PubTatorClient,
    PubTatorError,
    default_cache_path,
)
from .matrix import INTERRUPTED, RELATION_TYPES, build_matrix, unique
from .output import write_all
from .schedule import QuietHours
from .terms import CONCEPTS, Term, read_term_file, resolve_term

# Above this many terms, individual readings go in the output files only.
LIST_TERMS_UP_TO = 50

# Above this many requests, suggest leaving the run for quiet hours.
LARGE_RUN_REQUESTS = 1000

# Measured against the live service. The client allows 3 per second, but slow
# responses keep a real run nearer to this.
TYPICAL_REQUESTS_PER_SECOND = 2.0

PROGRESS_LOG_SECONDS = 60.0

STOPPED_EXIT = 130


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pubmatrix",
        description="Pairwise literature matrices from PubTator3.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--cache", type=Path, help=f"cache file (default: {default_cache_path()})"
    )
    parser.add_argument(
        "--refresh", action="store_true", help="ignore cached answers and ask again"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="build a matrix")
    run.add_argument("-t", "--term", action="append", default=[], help="row term (repeatable)")
    run.add_argument("-m", "--modifier", action="append", default=[], help="column term (repeatable)")
    run.add_argument("-T", "--terms-file", help="row terms, one per line")
    run.add_argument("-M", "--modifiers-file", help="column terms, one per line")
    run.add_argument(
        "-r",
        "--relation",
        nargs="?",
        const="ANY",
        choices=RELATION_TYPES,
        help="count extracted relations instead of co-occurrence (default type: ANY)",
    )
    run.add_argument("--terms-type", choices=CONCEPTS, help="only look row terms up as this type")
    run.add_argument("--modifiers-type", choices=CONCEPTS, help="only look column terms up as this type")
    run.add_argument(
        "--quiet-hours",
        action="store_true",
        help="only send requests on weekends and 9 PM to 5 AM US Eastern, waiting otherwise",
    )
    run.add_argument("-o", "--output", default="pubmatrix", help="output path prefix")
    run.add_argument("--workers", type=int, default=8, help="concurrent requests in flight")

    resolve = commands.add_parser("resolve", help="show how terms would be read")
    resolve.add_argument("terms", nargs="+")
    resolve.add_argument("--type", choices=CONCEPTS, help="only look terms up as this type")
    return parser


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _describe(term: Term) -> str:
    total = "?" if term.total is None else f"{term.total:,} docs"
    line = f"  {term.raw} -> {term.query or '(unresolved)'} [{term.kind}, {total}]"
    return f"{line}\n    {term.note}" if term.note else line


def _summarize_terms(terms: list[Term]) -> None:
    if len(terms) <= LIST_TERMS_UP_TO:
        _log("Terms:")
        for term in terms:
            _log(_describe(term))
        return
    kinds = Counter(term.biotype or term.kind for term in terms)
    read_as = ", ".join(f"{count} {kind}" for kind, count in kinds.most_common())
    _log(f"{len(terms)} terms read as: {read_as}. See the Terms sheet for each one.")
    problems = [term for term in terms if term.problem]
    for term in problems[:10]:
        _log(_describe(term))
    if len(problems) > 10:
        _log(f"  and {len(problems) - 10} more with problems")


class _Progress:
    """Live counter in a terminal; a timestamped line now and then in a log file."""

    def __init__(self) -> None:
        self._live = sys.stderr.isatty()
        self._last = time.monotonic()

    def __call__(self, done: int, total: int) -> None:
        if self._live:
            print(f"\r  {done}/{total} cells", end="\n" if done == total else "", file=sys.stderr, flush=True)
        elif done == total or time.monotonic() - self._last >= PROGRESS_LOG_SECONDS:
            self._last = time.monotonic()
            _log(f"{datetime.now():%Y-%m-%d %H:%M} {done}/{total} cells")


def _duration(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} seconds"
    if seconds < 5400:
        return f"{seconds / 60:.0f} minutes"
    return f"{seconds / 3600:.1f} hours"


def _announce_size(rows: list[str], cols: list[str], quiet: bool) -> None:
    n_rows, n_cols = len(unique(rows)), len(unique(cols))
    requests = n_rows * n_cols + 2 * (n_rows + n_cols)
    seconds = requests / TYPICAL_REQUESTS_PER_SECOND
    _log(
        f"{n_rows} x {n_cols} = {n_rows * n_cols} cells, up to {requests} requests, "
        f"about {_duration(seconds)} of sending. Cached answers cost nothing."
    )
    if requests > LARGE_RUN_REQUESTS and not quiet:
        _log("This is a large run. Consider --quiet-hours to keep it off NCBI's busy hours.")


def _announce_pause(opens: datetime) -> None:
    _log(f"Outside quiet hours; waiting until {opens:%a %Y-%m-%d %H:%M %Z}.")


def _raise_interrupt(*_: object) -> None:
    raise KeyboardInterrupt


def _run(args: argparse.Namespace, client: PubTatorClient) -> int:
    rows = args.term + (read_term_file(args.terms_file) if args.terms_file else [])
    cols = args.modifier + (read_term_file(args.modifiers_file) if args.modifiers_file else [])
    if not unique(rows) or not unique(cols):
        _log("error: give at least one term and one modifier")
        return 2
    _announce_size(rows, cols, args.quiet_hours)

    def report_terms(terms: list[Term]) -> None:
        _summarize_terms(terms)
        _log("Querying pairs:")

    try:
        matrix = build_matrix(
            client,
            rows,
            cols,
            relation=args.relation,
            row_type=args.terms_type,
            col_type=args.modifiers_type,
            workers=args.workers,
            on_terms=report_terms,
            progress=_Progress(),
        )
    except KeyboardInterrupt:
        _log("Stopped while reading terms. Run the same command again to continue.")
        return STOPPED_EXIT

    for path in write_all(matrix, args.output, shlex.join(sys.argv)):
        print(path)
    failed = matrix.failed
    if matrix.stopped:
        _log(f"Stopped early ({matrix.stopped}). The files are partial.")
        _log("Run the same command again to continue; answers so far are saved.")
        return STOPPED_EXIT if matrix.stopped == INTERRUPTED else 1
    if failed:
        _log(f"{len(failed)} of {len(matrix.cells)} cells have no value:")
        reasons = sorted({cell.error or "" for cell in failed})
        for reason in reasons[:20]:
            _log(f"  {reason}")
        if len(reasons) > 20:
            _log(f"  and {len(reasons) - 20} more reasons; see the output files")
        return 1
    return 0


def _resolve(args: argparse.Namespace, client: PubTatorClient) -> int:
    status = 0
    concept = args.type.upper() if args.type else None
    for raw in args.terms:
        try:
            term = resolve_term(raw, client, args.type)
            if not term.problem:
                term.total = client.count(term.query)
            print(_describe(term))
            if term.kind == "text" and not raw.startswith('"'):
                for candidate in client.autocomplete(raw, concept=concept):
                    print(f"    candidate: {candidate['_id']} ({candidate.get('biotype')})")
        except PubTatorError as exc:
            print(f"  {raw}: {exc}", file=sys.stderr)
            status = 1
    return status


def main(argv: list[str] | None = None, client: PubTatorClient | None = None) -> int:
    args = _parser().parse_args(argv)
    if client is None:
        quiet = getattr(args, "quiet_hours", False)
        client = PubTatorClient(
            Cache(args.cache or default_cache_path()),
            refresh=args.refresh,
            window=QuietHours() if quiet else None,
            # An unattended night should outlast an outage; a person at a terminal
            # should hear about it sooner.
            patience=3600.0 if quiet else 300.0,
            on_pause=_announce_pause,
        )
    signal.signal(signal.SIGTERM, _raise_interrupt)
    return _run(args, client) if args.command == "run" else _resolve(args, client)


if __name__ == "__main__":
    sys.exit(main())
