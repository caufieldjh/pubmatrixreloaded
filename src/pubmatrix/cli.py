"""Command line interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .client import (
    MAX_REQUESTS_PER_SECOND,
    Cache,
    PubTatorClient,
    PubTatorError,
    default_cache_path,
)
from .matrix import RELATION_TYPES, build_matrix, unique
from .output import write_all
from .terms import Term, read_term_file, resolve_term


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
    run.add_argument("-o", "--output", default="pubmatrix", help="output path prefix")
    run.add_argument("--workers", type=int, default=8, help="concurrent requests in flight")

    resolve = commands.add_parser("resolve", help="show how terms would be read")
    resolve.add_argument("terms", nargs="+")
    return parser


def _describe(term: Term) -> str:
    total = "?" if term.total is None else f"{term.total:,} docs"
    line = f"  {term.raw} -> {term.query or '(unresolved)'} [{term.kind}, {total}]"
    if term.problem:
        return f"{line}\n    PROBLEM: {term.problem}"
    if term.suggestion:
        return f"{line}\n    no exact entity match; closest is {term.suggestion}"
    return line


def _progress(done: int, total: int) -> None:
    end = "\n" if done == total else ""
    print(f"\r  {done}/{total} cells", end=end, file=sys.stderr, flush=True)


def _run(args: argparse.Namespace, client: PubTatorClient) -> int:
    rows = args.term + (read_term_file(args.terms_file) if args.terms_file else [])
    cols = args.modifier + (read_term_file(args.modifiers_file) if args.modifiers_file else [])
    if not unique(rows) or not unique(cols):
        print("error: give at least one term and one modifier", file=sys.stderr)
        return 2

    def report_terms(terms: list[Term]) -> None:
        print("Terms:", file=sys.stderr)
        for term in terms:
            print(_describe(term), file=sys.stderr)
        print("Querying pairs:", file=sys.stderr)

    matrix = build_matrix(
        client,
        rows,
        cols,
        relation=args.relation,
        workers=args.workers,
        on_terms=report_terms,
        progress=_progress,
    )
    for path in write_all(matrix, args.output):
        print(path)
    failed = matrix.failed
    if failed:
        print(f"{len(failed)} of {len(matrix.cells)} cells have no value:", file=sys.stderr)
        for reason in sorted({cell.error or "" for cell in failed}):
            print(f"  {reason}", file=sys.stderr)
        return 1
    return 0


def _resolve(args: argparse.Namespace, client: PubTatorClient) -> int:
    status = 0
    for raw in args.terms:
        try:
            term = resolve_term(raw, client)
            term.total = client.count(term.query)
            print(_describe(term))
            if term.kind == "text" and not raw.startswith('"'):
                for candidate in client.autocomplete(raw):
                    print(f"    candidate: {candidate['_id']} ({candidate.get('biotype')})")
        except PubTatorError as exc:
            print(f"  {raw}: {exc}", file=sys.stderr)
            status = 1
    return status


def main(argv: list[str] | None = None, client: PubTatorClient | None = None) -> int:
    args = _parser().parse_args(argv)
    if client is None:
        cache = Cache(args.cache or default_cache_path())
        client = PubTatorClient(cache, refresh=args.refresh, rate=MAX_REQUESTS_PER_SECOND)
    return _run(args, client) if args.command == "run" else _resolve(args, client)


if __name__ == "__main__":
    sys.exit(main())
