# PubMatrix Reloaded

A rebuild of [PubMatrix](https://pmc.ncbi.nlm.nih.gov/articles/PMC317283/)
(Becker et al., 2003) on top of the
[PubTator3 API](https://www.ncbi.nlm.nih.gov/research/pubtator3/api).

Give it two lists of terms. It counts the literature for every pair and returns
a matrix in which each count links to the documents behind it.

This is a first prototype: a command line tool that writes files.

## Install and run

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run pubmatrix run -T examples/genes.txt -M examples/diseases.txt -o output/demo
```

This writes three files:

| File | Contents |
|---|---|
| `output/demo.html` | Sortable heatmap table; each count links to PubTator3 |
| `output/demo.csv` | Plain grid for spreadsheets and clustering tools |
| `output/demo.json` | Everything: term readings, queries, links, and errors |

Terms can also go on the command line:

```sh
uv run pubmatrix run -t BRCA1 -t TP53 -m @DISEASE_Breast_Neoplasms -m "DNA repair" -o output/small
```

Exit status is 0 when every cell has a value, 1 when the files were written but
some cells have none, and 2 for bad input.

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

Lookup is deliberately strict. PubTator3's autocomplete always offers something:
for `apoptosis` it offers a cortical malformation. So `breast cancer` stays a
phrase, even though `@DISEASE_Breast_Neoplasms` is what you likely want. The tool
prints how it read each term and the closest entity. To see the candidates:

```sh
uv run pubmatrix resolve "breast cancer" p53
```

Relation searches need entities on both sides, so give an `@` ID for any term
that stayed a phrase.

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
- Parenthesized OR groups such as `(@GENE_A OR @GENE_B) AND @DISEASE_C` get HTTP
  400 from PubTator3, and so do IDs like `@DISEASE_MESH:D001943`. These cells show
  the error.
- The relation types `convert` and `bind` appear in PubTator3's site code, but
  every query tried for them returned zero, so they are not offered.

## Speed and caching

PubTator3 asks for no more than three requests per second, and the client holds to
that across all threads. A matrix takes about one request per cell plus two per
term in the worst case, so 100 by 10 terms should take about seven minutes.

Answers are cached for 30 days in `~/.cache/pubmatrix/cache.sqlite`, so a rerun
is near instant and an interrupted run picks up where it stopped. Use `--refresh`
to ask again, or `--cache PATH` for another location. Both go before `run`.
Failures are never cached.

## Compared with the original

| | PubMatrix (2003) | This prototype |
|---|---|---|
| Source | PubMed hit counts | PubTator3 concepts over abstracts and full text |
| Term handling | No curation; any PubMed string | Exact entity lookup, phrase fallback |
| Cell | `A AND B` count | Same, or a typed relation count |
| Limit | 100 terms by 10 modifiers | None set; paced at 3 requests per second |
| Output | Linked HTML table | Linked HTML, CSV, JSON |
| Saved searches | Shared server archive | Local cache and output files only |
| Interface | Web form | Command line |

Not rebuilt yet: the web form and the shared archive of searches and term lists.

## Development

```sh
uv run pytest
```

The tests use a fake API and do not touch the network.
