"""Measure browser searches against Parquet and compare them with native DuckDB.

Usage: python proof/measure.py SITE_ROOT PORT CHROMIUM_PATH OUTPUT_JSON IDS_JSON SERVER_LOG

SITE_ROOT holds proof/index.html, node_modules/, and data/*.parquet, and range_server.py
must already be serving it on PORT. Needs duckdb and playwright.
"""

import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import duckdb
from playwright.sync_api import sync_playwright

SITE, PORT, CHROMIUM, OUTPUT = Path(sys.argv[1]), sys.argv[2], sys.argv[3], Path(sys.argv[4])
DATA = SITE / "data"
IDS = Path(sys.argv[5])  # JSON map from PubTator3 entity names to database IDs
LOG = Path(sys.argv[6])  # the output of range_server.py
LOG_OFFSET = 0
BASE = f"http://127.0.0.1:{PORT}"

# Set DATA_URL to read from a real host. The server log then shows only the page's own
# files, so the request and byte counts no longer cover the data.
DATA_URL = os.environ.get("DATA_URL", "/data/")
PAGE = (
    f"{BASE}/proof/index.html?duckdb=/node_modules/@duckdb/duckdb-wasm/dist/"
    f"&bundle=/bundle/duckdb.mjs&data={DATA_URL}"
)

GENES = ["351", "5663", "5664", "348", "54209", "4137", "6622", "120892", "11315", "65018",
         "5071", "3064", "6647", "203228", "23435", "2521"]  # fmt: skip
DISEASES = ["MESH:D000544", "MESH:D010300", "MESH:D006816", "MESH:D000690", "MESH:D057180"]
TOP_DISEASES = DISEASES[:3] + ["MESH:D001943", "MESH:D008545", "MESH:D015179", "MESH:D003920",
                               "MESH:D006973"]  # fmt: skip


def stats():
    """Return requests and bytes served since the last call, from the server's log."""
    global LOG_OFFSET
    with open(LOG, encoding="utf-8") as handle:
        handle.seek(LOG_OFFSET)
        lines = handle.readlines()
        LOG_OFFSET = handle.tell()
    gets = [line.split() for line in lines if " GET " in line]
    return {"requests": len(gets), "bytes": sum(int(parts[-1]) for parts in gets)}


def renderer_memory_mb():
    done = subprocess.run(["ps", "-eo", "rss,args"], capture_output=True, text=True, check=True)
    sizes = [int(line.split()[0]) for line in done.stdout.splitlines()
             if "--type=renderer" in line and "headless" in line]  # fmt: skip
    return max(sizes, default=0) // 1024


def native(con, row_file, rows, col_file, cols):
    con.execute("CREATE OR REPLACE TEMP TABLE want_r (id VARCHAR)")
    con.execute("CREATE OR REPLACE TEMP TABLE want_c (id VARCHAR)")
    con.executemany("INSERT INTO want_r VALUES (?)", [[r] for r in rows])
    con.executemany("INSERT INTO want_c VALUES (?)", [[c] for c in cols])
    found = con.execute(
        f"""SELECT r.concept, c.concept, count(*)
            FROM (SELECT * FROM '{DATA}/{row_file}.parquet' WHERE concept IN (SELECT id FROM want_r)) r
            JOIN (SELECT * FROM '{DATA}/{col_file}.parquet' WHERE concept IN (SELECT id FROM want_c)) c
            USING (pmid) GROUP BY 1, 2"""
    ).fetchall()
    return {(a, b): n for a, b, n in found}


def pick_chemicals(con, count, least, most):
    found = con.execute(
        f"""SELECT concept FROM '{DATA}/chemical.parquet' GROUP BY concept
            HAVING count(*) BETWEEN {least} AND {most} ORDER BY concept"""
    ).fetchall()
    return random.Random(7).sample([row[0] for row in found], count)


def measure_relations(browser, con):
    """Read the cancer drug matrix from relations.parquet and compare with the live API."""
    example = json.loads(Path("docs/examples/cancer-drugs.json").read_text(encoding="utf-8"))
    ids = json.loads(IDS.read_text(encoding="utf-8"))

    def key(query):
        kind = query[1:].split("_")[0].capitalize()
        return f"{kind}|MESH:{ids[query]}"

    width = len(example["columns"])
    pairs, api = [], []
    for i, row in enumerate(example["rows"]):
        for j, col in enumerate(example["columns"]):
            pairs.append(sorted([key(row["query"]), key(col["query"])]))
            api.append(example["cells"][i * width + j]["count"])
    page = browser.new_context().new_page()
    page.goto(PAGE)
    page.wait_for_function("window.proof !== undefined", timeout=90_000)
    stats()
    start = time.monotonic()
    result = page.evaluate("job => window.proof.relations(job)", {"pairs": pairs, "type": "treat"})
    seconds = time.monotonic() - start
    used = stats()
    got = [n for _, _, n in result["cells"]]
    want = [
        con.execute(
            f"SELECT coalesce(sum(n), 0) FROM '{DATA}/relations.parquet' "
            "WHERE a = ? AND b = ? AND type = 'treat'",
            pair,
        ).fetchone()[0]
        for pair in pairs
    ]
    ratios = sorted(g / a for g, a in zip(got, api) if a >= 20)
    page.close()
    return {
        "cells": len(pairs),
        "search_seconds": round(seconds, 1),
        "search_requests": used["requests"],
        "search_mb": round(used["bytes"] / 1e6, 1),
        "matches_native": got == [int(w) for w in want],
        "median_ratio_to_api": round(ratios[len(ratios) // 2], 3),
    }


def main():
    con = duckdb.connect()
    con.execute("SET memory_limit = '1200MB'; SET threads = 2")
    typical = pick_chemicals(con, 1000, 100, 100_000)
    jobs = [
        ("16 genes by 5 diseases", "gene", GENES, "disease", DISEASES, "shard"),
        ("100 chemicals by 8 diseases", "chemical", typical[:100], "disease", TOP_DISEASES, "shard"),
        ("1000 chemicals by 8 diseases", "chemical", typical, "disease", TOP_DISEASES, "shard"),
    ]
    report = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROMIUM)
        for name, row_file, rows, col_file, cols, strategy in jobs:
            page = browser.new_context().new_page()
            page.on("pageerror", lambda error: print("PAGE ERROR", error, flush=True))
            page.on(
                "console",
                lambda m: print("CONSOLE", m.text[:300], flush=True) if m.type == "error" else None,
            )
            stats()
            start = time.monotonic()
            page.goto(PAGE)
            page.wait_for_function("window.proof !== undefined", timeout=90_000)
            first = stats()
            ready = time.monotonic() - start
            start = time.monotonic()
            try:
                result = page.evaluate(
                    "job => window.proof.matrix(job)",
                    {"rowFile": row_file, "rows": rows, "colFile": col_file, "cols": cols,
                     "strategy": strategy},
                )  # fmt: skip
            except Exception as error:  # noqa: B902 - a crash is a result worth recording
                report[name] = {"failed": str(error)[:300], "seconds": time.monotonic() - start}
                print(name, report[name], flush=True)
                page.close()
                continue
            seconds = time.monotonic() - start
            used = stats()
            memory = renderer_memory_mb()
            got = {(a, b): n for a, b, n in result["cells"]}
            want = native(con, row_file, rows, col_file, cols)
            report[name] = {
                "ready_seconds": round(ready, 1),
                "ready_mb": round(first["bytes"] / 1e6, 1),
                "search_seconds": round(seconds, 1),
                "search_requests": used["requests"],
                "search_mb": round(used["bytes"] / 1e6, 1),
                "rows_read": result["rowsRead"] + result["colsRead"],
                "stage_ms": {k: round(v) for k, v in result["ms"].items()},
                "renderer_mb": memory,
                "matches_native": got == want,
                "cells_nonzero": len(got),
            }
            print(name, report[name], flush=True)
            OUTPUT.write_text(json.dumps(report, indent=2))
            page.close()
        report["relations"] = measure_relations(browser, con)
        print("relations", report["relations"], flush=True)
        OUTPUT.write_text(json.dumps(report, indent=2))
        browser.close()


main()
