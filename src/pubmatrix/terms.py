"""Turn the strings a user typed into PubTator3 query fragments.

A wrong entity ID does not fail at PubTator3: it returns zero documents with
HTTP 200. Autocomplete also always offers something, even for concepts it has no
entity for ("apoptosis" returns a cortical malformation). So a term becomes an
entity only on an exact name or synonym match, and otherwise stays a phrase.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .client import PubTatorClient

_MATCHED = re.compile(r"^Matched on \w+ <m>(.*)</m>$")
_ID_PREFIX = re.compile(r"^@[A-Z]+_")


@dataclass
class Term:
    raw: str
    kind: str  # "entity" or "text"
    query: str  # the fragment used in PubTator3 search queries
    name: str | None = None
    biotype: str | None = None
    suggestion: str | None = None  # closest entity, when a term stayed text
    total: int | None = None  # documents matching this term alone
    problem: str | None = None  # why this term cannot produce counts

    @property
    def label(self) -> str:
        """Display name: what the user typed, minus query syntax."""
        return _ID_PREFIX.sub("", self.raw).replace("_", " ").strip('"')


def phrase(text: str) -> str:
    """Quote free text: unquoted, words match independently, not as a phrase."""
    return '"' + text.replace('"', "") + '"'


def is_exact_match(candidate: dict, term: str) -> bool:
    wanted = term.strip().casefold()
    if str(candidate.get("name", "")).casefold() == wanted:
        return True
    matched = _MATCHED.match(str(candidate.get("match", "")))
    return bool(matched) and matched.group(1).casefold() == wanted


def resolve_term(raw: str, client: PubTatorClient) -> Term:
    """Interpret one term.

    `@GENE_BRCA1` is used as written, `"quoted text"` is always a phrase, and
    anything else is looked up and becomes an entity only on an exact match.
    """
    raw = raw.strip()
    if raw.startswith("@"):
        return Term(raw, "entity", raw)
    if len(raw) > 1 and raw.startswith('"') and raw.endswith('"'):
        return Term(raw, "text", phrase(raw[1:-1]))
    candidates = client.autocomplete(raw)
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
