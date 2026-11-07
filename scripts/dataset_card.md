---
license: other
license_name: public-domain-us-government
license_link: https://ftp.ncbi.nlm.nih.gov/pub/lu/PubTator3/README.txt
pretty_name: PubTator3 entity index for PubMatrix Reloaded
tags:
  - biomedical
  - literature
  - text-mining
  - pubtator
size_categories:
  - 100M<n<1B
configs: []
---

# PubTator3 entity index for PubMatrix Reloaded

A reformatting of [PubTator3](https://www.ncbi.nlm.nih.gov/research/pubtator3/)'s bulk annotations, arranged so that a web browser can read small slices of it with [DuckDB WASM](https://duckdb.org/docs/api/wasm/overview). It serves [PubMatrix Reloaded](https://github.com/caufieldjh/pubmatrixreloaded), which counts the literature for pairs of biomedical terms.

**This is not an NCBI product.** The annotations are NCBI's and are unchanged in meaning; only the layout is new. NCBI does not endorse this dataset. For anything beyond this layout, use [the original files](https://ftp.ncbi.nlm.nih.gov/pub/lu/PubTator3/).

- **Source release:** 2026-10-06
- **Built with:** [`scripts/build_parquet.py`](https://github.com/caufieldjh/pubmatrixreloaded/blob/main/scripts/build_parquet.py)

## Files

| Path | Contents |
|---|---|
| `gene/NNN.parquet` | `concept` (NCBI Gene ID), `pmid` |
| `disease/NNN.parquet` | `concept` (for example `MESH:D001943`), `pmid` |
| `chemical/NNN.parquet` | `concept` (for example `MESH:D013629`), `pmid` |
| `names/NNN.parquet` | `name`, `type`, `concept`, `n`: how often a lowercased name was used for a concept |
| `relations.parquet` | `a`, `b`, `type`, `n`: documents with that relation between entities `a` and `b` |
| `shards.json` | For each table, the first value in each shard |

Entity rows are sorted by `concept`, then `pmid`, and each (concept, pmid) pair appears once. The names table is built from the bulk files' mentions: names are lowercased in ASCII only, names seen once are dropped, and each name keeps its five most common concepts per type. Concepts sort as bytes. To find a concept, take the last shard whose first concept is not greater than it. In `relations.parquet`, each pair is stored with `a` before `b`, because PubTator3 treats relations as undirected in search.

The data is sharded because DuckDB WASM walks a file's whole row group list for each query, which takes about a second in a large file.

## Reading it in a browser

Pass `filesystem: { reliableHeadRequests: true, allowFullHTTPReads: false, forceFullHTTPReads: false }` to `db.open`, or DuckDB WASM may download whole files. Query one concept at a time: `WHERE concept IN (...)` can read far more data. Do not read one file from several workers at once: concurrent range requests for the same URL returned bad bytes in Chromium, seen as `ZSTD Decompression failure`.

## Limits

- Counts differ from the PubTator3 API by a few percent, for reasons not established. Compared on 132 relation cells, the median was 3 to 4% higher here, with rank agreement of at least 0.995.
- Genes are per-species IDs. The API's gene symbols group species together.
- Annotations are machine generated and contain errors. A co-occurrence is a shared document, not evidence of a relationship.
- Only genes, diseases, chemicals, and relations are included: no species, cell lines, or variants, and no free text.

## License and citation

NCBI states that this data is a United States Government Work, not subject to copyright, and that NLM and the U.S. Government place no restriction on its use. They disclaim all warranties. See [the original notice](https://ftp.ncbi.nlm.nih.gov/pub/lu/PubTator3/README.txt).

If you use this data, please cite PubTator3:

> Wei C-H, Allot A, Lai P-T, Leaman R, Tian S, Luo L, Jin Q, Wang Z, Chen Q, Lu Z. PubTator 3.0: an AI-powered literature resource for unlocking biomedical knowledge. *Nucleic Acids Research*. 2024;52(W1):W540-W546. doi:10.1093/nar/gkae235
