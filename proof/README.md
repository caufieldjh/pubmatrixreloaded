# Browser search proof

Measures whether DuckDB WASM can run PubMatrix searches in a browser from Parquet files, as proposed in issue 3. This is a test bench, not a feature. Results are recorded in that issue.

## Running it

```sh
# Download the four bulk files, then build (about 30 minutes, under 2.5 GB of memory).
uv run scripts/build_parquet.py BULK_DIR PARQUET_DIR

# Set up a site folder holding proof/, node_modules/ (with @duckdb/duckdb-wasm),
# bundle/duckdb.mjs (esbuild of duckdb-browser.mjs), and data/ (the Parquet folder).
python proof/range_server.py SITE 8791 40 50 > server.log &
uv run --with duckdb --with playwright python proof/measure.py \
  SITE 8791 CHROMIUM results.json ids.json server.log
```

`range_server.py` adds 40 ms per request and limits throughput to 50 Mbit/s, so that results stand in for a host reached over the internet.

## What it taught us

- DuckDB WASM downloaded whole files until `filesystem` options were set in `db.open`.
- `WHERE concept IN (...)` read 527 MB where one query per term read 19 MB.
- Each query walks the whole row group list, about a second in a large file, so the data is split into shards listed in `shards.json`.
- The test server needs keep-alive and `ETag` headers, or Chromium's range requests crawl.
