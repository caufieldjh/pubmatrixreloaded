# /// script
# requires-python = ">=3.10"
# dependencies = ["duckdb>=1.1"]
# ///
"""Convert PubTator3 bulk files to Parquet that a browser can read in slices.

Usage: uv run scripts/build_parquet.py BULK_DIR OUT_DIR

BULK_DIR holds gene2pubtator3.gz, disease2pubtator3.gz, chemical2pubtator3.gz, and
relation2pubtator3.gz from https://ftp.ncbi.nlm.nih.gov/pub/lu/PubTator3/.

Rows are sorted by entity so that one entity's documents sit together, which lets a
reader fetch them with a few range requests. Sorting is left to the system's sort,
which works on disk, so memory stays low however large the input is.
"""

import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

import duckdb

ENTITY_FILES = ("gene", "disease", "chemical")

# Small enough that a rare entity costs little to read; large enough that the
# file's metadata, which every reader fetches first, stays small.
ROW_GROUP_SIZE = 20_000

SORT_MEMORY = "600M"

# DuckDB WASM walks a file's whole row group list for every query, which costs about
# a second in a file with thousands of row groups. Many small files keep that cheap.
SHARDS = 64

# Some rows carry several concepts, as in "1234;5678".
SPLIT_CONCEPTS = r"""BEGIN { FS = OFS = "\t" }
$3 != "" && $3 != "-" { n = split($3, ids, /[;,]/); for (i = 1; i <= n; i++) print ids[i], $1 }"""

# One line per mention of a concept: name, type, concept. Names are lowercased in ASCII
# only, and a reader must do the same. The bulk files' type column gives Gene, Disease,
# or Chemical.
SPLIT_NAMES = r"""BEGIN { FS = OFS = "\t" }
$3 != "" && $3 != "-" {
  k = split($3, ids, /[;,]/); n = split($4, names, "|")
  for (i = 1; i <= n; i++) if (names[i] != "") for (j = 1; j <= k; j++)
    print tolower(names[i]), $2, ids[j]
}"""

# Names seen once are mostly noise, and more than this many meanings help nobody.
MIN_NAME_COUNT = 2
MAX_MEANINGS = 5

# Relations are undirected in PubTator3 search, so put each pair in one order.
ORDER_PAIRS = r"""BEGIN { FS = OFS = "\t" }
{ if ($3 < $4) print $3, $4, $2; else print $4, $3, $2 }"""


def run_shell(command: str) -> None:
    subprocess.run(["bash", "-o", "pipefail", "-c", command], check=True)


def connect(work: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '1200MB'; SET threads = 2; SET temp_directory = '{work}'")
    return con


def sort_command(work: Path, keys: str) -> str:
    return f"LC_ALL=C sort -S {SORT_MEMORY} -T {shlex.quote(str(work))} --parallel=2 {keys}"


def build_entities(name: str, bulk: Path, out: Path, work: Path) -> None:
    source = shlex.quote(str(bulk / f"{name}2pubtator3.gz"))
    sorted_rows = work / f"{name}.tsv"
    keys = "-t $'\\t' -u -k1,1 -k2,2n"
    run_shell(
        f"gzip -dc {source} | awk {shlex.quote(SPLIT_CONCEPTS)} | {sort_command(work, keys)}"
        f" > {shlex.quote(str(sorted_rows))}"
    )
    connect(work).execute(
        f"""COPY (SELECT * FROM read_csv('{sorted_rows}', delim = '\t', header = false,
                  columns = {{'concept': 'VARCHAR', 'pmid': 'INTEGER'}}, quote = '', escape = '',
                  parallel = false))
            TO '{out / f"{name}.parquet"}'
            (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE {ROW_GROUP_SIZE})"""
    )
    sorted_rows.unlink()


def quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_names(bulk: Path, out: Path, work: Path) -> None:
    """Build a table from each name to the concepts it most often means.

    The browser has no autocomplete, so this is how it reads what people type.
    """
    sources = " ".join(shlex.quote(str(bulk / f"{n}2pubtator3.gz")) for n in ENTITY_FILES)
    rows = work / "names.tsv"
    count_rows = (
        "awk -v OFS='\\t' '{ n = $1; sub(/^ *[0-9]+ /, \"\"); "
        f"if (n >= {MIN_NAME_COUNT}) print $0 \"\\t\" n }}'"
    )
    keep_top = (
        "awk -F'\\t' -v OFS='\\t' '{ key = $1 FS $2; "
        f"seen = (key == last) ? seen + 1 : 1; last = key; if (seen <= {MAX_MEANINGS}) print }}'"
    )
    order = "-t $'\\t' -k1,1 -k2,2 -k4,4nr"
    run_shell(
        f"gzip -dc {sources} | awk {shlex.quote(SPLIT_NAMES)} | {sort_command(work, '')} | uniq -c"
        f" | {count_rows} | {sort_command(work, order)} | {keep_top} > {shlex.quote(str(rows))}"
    )
    connect(work).execute(
        f"""COPY (SELECT * FROM read_csv('{rows}', delim = '\t', header = false,
                  columns = {{'name': 'VARCHAR', 'type': 'VARCHAR', 'concept': 'VARCHAR',
                              'n': 'INTEGER'}},
                  quote = '', escape = '', parallel = false))
            TO '{out / "names.parquet"}'
            (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE {ROW_GROUP_SIZE})"""
    )
    rows.unlink()


def shard_file(name: str, out: Path, work: Path, column: str = "concept") -> list[str]:
    """Split one sorted file into shards, returning the first value of each."""
    con = connect(work)
    source = out / f"{name}.parquet"
    starts = [
        row[0]
        for row in con.execute(
            f"""SELECT DISTINCT stats_min_value FROM parquet_metadata('{source}')
                WHERE path_in_schema = '{column}'
                  AND row_group_id % (SELECT ceil(num_row_groups / {SHARDS})::INT
                                      FROM parquet_file_metadata('{source}')) = 0
                ORDER BY 1"""
        ).fetchall()
    ]
    folder = out / name
    folder.mkdir(exist_ok=True)
    for index, first in enumerate(starts):
        test = f"{column} >= {quoted(first)}"
        if index + 1 < len(starts):
            test += f" AND {column} < {quoted(starts[index + 1])}"
        con.execute(
            f"""COPY (SELECT * FROM '{source}' WHERE {test})
                TO '{folder / f"{index:03d}.parquet"}'
                (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE {ROW_GROUP_SIZE})"""
        )
    return starts


def build_relations(bulk: Path, out: Path, work: Path) -> None:
    source = shlex.quote(str(bulk / "relation2pubtator3.gz"))
    pairs = work / "relations.tsv"
    run_shell(
        f"gzip -dc {source} | awk {shlex.quote(ORDER_PAIRS)} | {sort_command(work, '')}"
        f" | uniq -c | awk '{{ n = $1; sub(/^ *[0-9]+ /, \"\"); print $0 \"\\t\" n }}'"
        f" > {shlex.quote(str(pairs))}"
    )
    connect(work).execute(
        f"""COPY (SELECT * FROM read_csv('{pairs}', delim = '\t', header = false,
                  columns = {{'a': 'VARCHAR', 'b': 'VARCHAR', 'type': 'VARCHAR', 'n': 'INTEGER'}},
                  quote = '', escape = '', parallel = false))
            TO '{out / "relations.parquet"}'
            (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE {ROW_GROUP_SIZE})"""
    )
    pairs.unlink()


def main() -> None:
    bulk, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    work = out / "work"
    work.mkdir(exist_ok=True)
    manifest = {}
    for name in ENTITY_FILES:
        start = time.monotonic()
        if not (out / f"{name}.parquet").exists():
            build_entities(name, bulk, out, work)
        manifest[name] = shard_file(name, out, work)
        size = sum(path.stat().st_size for path in (out / name).glob("*.parquet"))
        count = len(manifest[name])
        print(f"{name}: {count} shards, {size / 1e6:.0f} MB in {time.monotonic() - start:.0f}s")
    start = time.monotonic()
    if not (out / "names.parquet").exists():
        build_names(bulk, out, work)
    manifest["names"] = shard_file("names", out, work, column="name")
    size = sum(path.stat().st_size for path in (out / "names").glob("*.parquet"))
    print(f"names: {size / 1e6:.0f} MB in {time.monotonic() - start:.0f}s", flush=True)
    (out / "shards.json").write_text(json.dumps(manifest))
    if not (out / "relations.parquet").exists():
        start = time.monotonic()
        build_relations(bulk, out, work)
        size = (out / "relations.parquet").stat().st_size
        print(f"relations: {size / 1e6:.0f} MB in {time.monotonic() - start:.0f}s", flush=True)
    work.rmdir()


if __name__ == "__main__":
    main()
