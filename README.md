# PubMatrix Reloaded

A rebuild of [PubMatrix](https://pmc.ncbi.nlm.nih.gov/articles/PMC317283/)
(Becker et al., 2003) on top of the
[PubTator3 API](https://www.ncbi.nlm.nih.gov/research/pubtator3/api).

Give it two lists of terms. It counts the literature for every pair and returns
a matrix in which each count links to the documents behind it.

It is built for the job the original was loved for: "here are 1000 chemicals we
care about; how many were tested in these 8 assays?" Start it at the end of the
day and read the spreadsheet in the morning.

This is an early prototype: a command line tool that writes files.

## Web page

[caufieldjh.github.io/pubmatrixreloaded](https://caufieldjh.github.io/pubmatrixreloaded/)
plans a search and shows its results. It helps you write the term lists, estimates
the run, and gives you the command to paste. Afterward, it displays the `.json` file
as a sortable heatmap, without uploading it anywhere.

The page cannot run searches itself. PubTator3 sends no CORS headers, so browsers
refuse its answers on any site, and GitHub Pages has no server to ask on the page's
behalf. The searching stays on your computer.

The page lives in `docs/` and has no build step. To serve it from GitHub, set
Settings > Pages to deploy from the `main` branch, `/docs` folder.

## Install and run

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run pubmatrix run -T examples/genes.txt -M examples/diseases.txt -o output/demo
```

This writes four files:

| File | Contents |
|---|---|
| `output/demo.xlsx` | Excel workbook; see [the workbook](#the-workbook) |
| `output/demo.html` | Sortable heatmap table; each count links to PubTator3 |
| `output/demo.csv` | Plain grid for spreadsheets and clustering tools |
| `output/demo.json` | Everything: run details, term readings, queries, links, retrieval times, and errors |

Terms can also go on the command line:

```sh
uv run pubmatrix run -t BRCA1 -t TP53 -m @DISEASE_Breast_Neoplasms -m "DNA repair" -o output/small
```

Exit status is 0 when every cell has a value, 1 when the files were written but
some cells have none, 2 for bad input, and 130 when the run was stopped by hand.

## A large overnight run

```sh
nohup uv run pubmatrix run \
  -T examples/chemicals.txt -M examples/assays.txt \
  --terms-type chemical --quiet-hours -o output/screen > screen.log 2>&1 &
```

`--quiet-hours` sends requests only on weekends and from 9 PM to 5 AM US Eastern
on weekdays, and waits otherwise. Started at 5 PM, it sits idle until 9 PM. Use
`tail screen.log` to check on it; it logs a progress line each minute. Files are
written when the run ends.

There is no fixed limit on list sizes. Live runs averaged about 2 requests per
second, so expect these sending times, not counting any wait for quiet hours:

| Matrix | Requests, at most | Time |
|---|---|---|
| 1000 chemicals by 8 assays | 10,016 | about 1.4 hours |
| 200 by 200 | 40,800 | about 5.7 hours |

Unlike the original, this runs on your own computer, so the computer has to stay
on and awake until it finishes.

**Stopping and continuing.** Ctrl-C or `kill` stops the run right away and writes
partial files, with unfinished cells blank and labeled. Run the same command
again to continue: every answer is saved as it arrives. If morning comes and the
run is unfinished, stop it to get what it has, then start it again.

**Outages.** A request retries for up to five minutes, or an hour with
`--quiet-hours`. If requests keep failing past that, the run stops and writes
partial files instead of marking every remaining cell as failed.

## The workbook

| Sheet | Contents |
|---|---|
| Matrix | Counts with heat shading and a link on each. "Modifiers with hits" gives, for each term, how many modifiers it appears with. Filters and frozen headers are on. |
| Terms | How each input was read, its entity type, its query, its documents alone, and any warning |
| Cells | One row per pair: count, exact query, link, the time PubTator3 gave the answer, and why a cell is blank |
| Run | Tool version, the full command, start and end times, and whether the run finished |

Together these are the trail from a number back to its source. Counts change as
PubTator3 updates, and a resumed run can span days, so each cell keeps its own
retrieval time. Above 65,530 cells, Excel's hyperlink limit applies, so the links
stay on the Cells sheet as text.

## Co-occurrence and relations

By default a cell counts documents mentioning both terms (`A AND B`), as
PubMatrix did. With `--relation`, a cell instead counts documents where PubTator3
extracted a relation between the two entities, which is new here:

```sh
uv run pubmatrix run -t tamoxifen -m @DISEASE_Breast_Neoplasms --relation treat -o output/rel
```

`--relation` alone means any relation. The types are `associate`, `cause`,
`compare`, `cotreat`, `drug_interact`, `inhibit`, `interact`,
`negative_correlate`, `positive_correlate`, `prevent`, `stimulate`, and `treat`.
Relation direction does not change the count.

## How terms are read

| You write | It is read as |
|---|---|
| `@GENE_BRCA1` | That entity ID, as written |
| `"DNA repair"` | That exact phrase, with no entity lookup |
| `p53` | The entity with that exact name or synonym (`@GENE_TP53`), otherwise a phrase |
| `Ames test \| Ames assay` | Documents with either; each part is read by the rules above |

Lookup is deliberately strict. PubTator3's autocomplete always offers something:
for `apoptosis` it offers a cortical malformation. So `breast cancer` stays a
phrase, even though `@DISEASE_Breast_Neoplasms` is what you likely want. The tool
prints how it read each term and the closest entity. To see the candidates:

```sh
uv run pubmatrix resolve "breast cancer" p53
```

Relation searches need a single entity on both sides, so give an `@` ID for any
term that stayed a phrase.

**Tell it what kind of list you have.** An exact match can be the wrong kind of
thing: `PFOA` on its own resolves to the bacterial gene *pfoA*. `--terms-type` and
`--modifiers-type` (`chemical`, `disease`, `gene`, or `variant`) limit lookup to one
type, and `resolve --type` does the same. Always use this for chemical lists. The
output shows the entity type of every term, so a stray gene is easy to find.

**Phrases are literal.** Use `|` to list the wordings of an assay or the synonyms
of a chemical, since a phrase finds only its own wording. Greek letters are spelled
out for you, because `"17β-estradiol"` finds 22 documents and `"17beta-estradiol"`
finds 28,538. A CAS number works only as literal text in documents, so prefer
names. A phrase with no documents at all gets a spelling warning in the Terms sheet.

## Reading the numbers

**These are not PubMed counts.** PubTator3 searches PubMed abstracts and PMC
full text, and it matches concepts rather than strings. PubMed gives 16,708 for
`BRCA1 AND breast cancer`; PubTator3 gives 45,434 for `@GENE_BRCA1 AND "breast cancer"`.
A full-text co-mention is weaker evidence than a shared abstract, and relation
mode is the stricter view.

**A blank is not a zero.** PubTator3 answers a misspelled entity ID, an unknown
relation type, and a phrase inside a relation search with zero documents and
HTTP 200. This tool counts each term alone first and refuses relation queries it
knows cannot work, so those cells stay blank with a stated reason (`—` in the
HTML, empty in the CSV, `null` plus `error` in the JSON). A `0` means PubTator3
was asked a valid question and found nothing.

Other things to know:

- Gene entities cover all species (`@GENE_BRCA1` is described as "All Species").
- As in the original, there is no significance test. Large counts partly reflect
  how common each term is, so compare against the "docs alone" figures.
- PubTator3 answers HTTP 400 when a group made only of `@` IDs meets a lone `@` ID,
  as in `(@GENE_A OR @GENE_B) AND @DISEASE_C`. Adding a phrase to either side
  avoids it. IDs like `@DISEASE_MESH:D001943` get a 400 in any pair. These cells
  show the error.
- The relation types `convert` and `bind` appear in PubTator3's site code, but
  every query tried for them returned zero, so they are not offered.

## Speed and caching

PubTator3 asks for no more than three requests per second, and the client holds to
that across all threads, retries included. The quiet hours are NCBI's published
advice for E-utilities; PubTator3 states only the rate, so applying them here is a
courtesy rather than a stated requirement.

Answers are cached for 30 days in `~/.cache/pubmatrix/cache.sqlite`, so a rerun
is near instant and does not wait for quiet hours. Use `--refresh` to ask again, or
`--cache PATH` for another location. Both go before `run`. A stopped `--refresh`
run starts over if resumed with `--refresh`; resume it without. Failures are never
cached.

## Compared with the original

| | PubMatrix (2003) | This prototype |
|---|---|---|
| Source | PubMed hit counts | PubTator3 concepts over abstracts and full text |
| Term handling | No curation; any PubMed string | Exact entity lookup, phrase fallback |
| Cell | `A AND B` count | Same, or a typed relation count |
| Limit | 100 terms by 10 modifiers in the 2003 paper | None set; paced at 3 requests per second |
| Off-peak running | On the server | `--quiet-hours`, on your own computer |
| Output | Linked HTML table; Excel in later use | Excel, linked HTML, CSV, JSON |
| Traceability | Stored searches | Exact query and retrieval time for every cell |
| Saved searches | Shared server archive | Local cache and output files only |
| Interface | Web form | Command line, with a web page to plan and view |

Not rebuilt yet: a web form that runs searches, the server that runs jobs for you,
and the shared archive of searches and term lists.

## Development

```sh
uv run pytest
node --test tests/web/*.test.mjs
```

The tests use a fake API and do not touch the network. `tests/test_web.py` checks
that the values copied into `docs/core.js` still match the Python tool.

To refresh the page's example, rerun it and copy `output/example.json` to `docs/`:

```sh
uv run pubmatrix run -T examples/chemicals.txt -M examples/assays.txt --terms-type chemical -o output/example
```
