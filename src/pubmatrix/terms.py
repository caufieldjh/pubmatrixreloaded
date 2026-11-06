"""Turn the strings a user typed into PubTator3 query fragments.

A wrong entity ID does not fail at PubTator3: it returns zero documents with
HTTP 200. Autocomplete also always offers something, even for concepts it has no
entity for ("apoptosis" returns a cortical malformation). So a term becomes an
entity only on an exact name or synonym match, and otherwise stays a phrase.
An exact match can still be the wrong kind of thing ("PFOA" is also the bacterial
gene pfoA), which is what the concept filter is for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .client import PubTatorClient

_MATCHED = re.compile(r"^Matched on \w+ <m>(.*)</m>$")
_ID_PREFIX = re.compile(r"^@[A-Z]+_")

SYNONYM_SEPARATOR = " | "

# Term types accepted by autocomplete's concept filter. Unknown values return
# no candidates rather than an error.
CONCEPTS = ("chemical", "disease", "gene", "variant")

# The search index spells these out: "17β-estradiol" finds 22 documents where
# "17beta-estradiol" finds over 28,000.
_GREEK = str.maketrans(
    {
        "α": "alpha",
        "β": "beta",
        "γ": "gamma",
        "δ": "delta",
        "ε": "epsilon",
        "κ": "kappa",
        "λ": "lambda",
        "μ": "mu",
        "µ": "mu",
        "ω": "omega",
    }
)


@dataclass
class Term:
    raw: str
    kind: str  # "entity", "text", or "synonyms"
    query: str  # the fragment used in PubTator3 search queries
    name: str | None = None
    biotype: str | None = None
    suggestion: str | None = None  # closest entity, when a term stayed text
    total: int | None = None  # documents matching this term alone
    problem: str | None = None  # why this term cannot produce counts

    @property
    def label(self) -> str:
        """Display name: what the user typed, minus query syntax."""
        first = self.raw.split(SYNONYM_SEPARATOR)[0]
        return _ID_PREFIX.sub("", first).replace("_", " ").strip('"')

    @property
    def note(self) -> str:
        if self.problem:
            return self.problem
        if self.kind == "text" and self.total == 0:
            return "No documents for this phrase; check the spelling"
        if self.suggestion:
            return f"No exact entity match; closest is {self.suggestion}"
        return ""


def phrase(text: str) -> str:
    """Quote free text: unquoted, words match independently, not as a phrase."""
    return '"' + text.replace('"', "").translate(_GREEK) + '"'


def is_exact_match(candidate: dict, term: str) -> bool:
    wanted = term.strip().casefold()
    if str(candidate.get("name", "")).casefold() == wanted:
        return True
    matched = _MATCHED.match(str(candidate.get("match", "")))
    return bool(matched) and matched.group(1).casefold() == wanted


def resolve_term(raw: str, client: PubTatorClient, concept: str | None = None) -> Term:
    """Interpret one term.

    `@GENE_BRCA1` is used as written, `"quoted text"` is always a phrase,
    `a | b` means either, and anything else is looked up and becomes an entity
    only on an exact match. `concept` limits lookup to one entity type.
    """
    raw = raw.strip()
    if SYNONYM_SEPARATOR in raw:
        parts = [p for p in raw.split(SYNONYM_SEPARATOR) if p.strip()]
        terms = [resolve_term(part, client, concept) for part in parts]
        problem = next((t.problem for t in terms if t.problem), None)
        query = "(" + " OR ".join(t.query for t in terms) + ")"
        return Term(raw, "synonyms", query, problem=problem)
    if raw.startswith("@"):
        return Term(raw, "entity", raw)
    quoted = len(raw) > 1 and raw.startswith('"') and raw.endswith('"')
    if '"' in (raw[1:-1] if quoted else raw):
        return Term(raw, "text", "", problem='stray quote mark; write alternatives as a | b')
    if quoted:
        return Term(raw, "text", phrase(raw[1:-1]))
    candidates = client.autocomplete(raw, concept=concept.upper() if concept else None)
    for candidate in candidates:
        if is_exact_match(candidate, raw):
            return Term(
                raw,
                "entity",
                candidate["_id"],
                name=candidate.get("name"),
                biotype=candidate.get("biotype"),
            )
    suggestion = candidates[0]["_id"] if candidates else None
    return Term(raw, "text", phrase(raw), suggestion=suggestion)


def read_term_file(path: str) -> list[str]:
    """Read one term per line, skipping blank lines and # comments."""
    with open(path, encoding="utf-8") as handle:
        lines = (line.strip() for line in handle)
        return [line for line in lines if line and not line.startswith("#")]
