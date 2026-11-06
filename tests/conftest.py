"""Shared test fixtures, including a stand-in for the PubTator3 API."""

import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from pubmatrix.client import PubTatorClient

ENTITIES = {
    "BRCA1": [
        {
            "_id": "@GENE_BRCA1",
            "biotype": "gene",
            "name": "BRCA1",
            "match": "Matched on name <m>BRCA1</m>",
        },
    ],
    "p53": [
        {
            "_id": "@GENE_TP53",
            "biotype": "gene",
            "name": "TP53",
            "match": "Matched on synonyms <m>P53</m>",
        },
    ],
    "PFOA": [
        {
            "_id": "@CHEMICAL_perfluorooctanoic_acid",
            "biotype": "chemical",
            "name": "perfluorooctanoic acid",
            "match": "Matched on synonyms <m>PFOA cpd</m>",
        },
        {
            "_id": "@GENE_PFOA",
            "biotype": "gene",
            "name": "pfoA",
            "match": "Matched on name <m>pfoA</m>",
        },
    ],
    "apoptosis": [
        {
            "_id": "@DISEASE_Malformations",
            "biotype": "disease",
            "name": "Malformations of Cortical Development",
            "match": "Multiple matches",
        },
    ],
}


class FakeApi:
    """Stands in for PubTator3, with its habit of answering nonsense with zero."""

    def __init__(self, counts=None, failures=None):
        self.counts = counts or {}
        self.failures = failures or {}  # query -> list of status codes, used in order
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = urlparse(str(request.url))
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path.endswith("/entity/autocomplete/"):
            self.requests.append(("autocomplete", params["query"]))
            found = ENTITIES.get(params["query"], [])
            if "concept" in params:
                found = [e for e in found if e["biotype"] == params["concept"].lower()]
            return httpx.Response(200, json=found)
        text = params["text"]
        self.requests.append(("search", text))
        if self.failures.get(text):
            return httpx.Response(self.failures[text].pop(0), text="nope")
        return httpx.Response(200, text=json.dumps({"count": self.counts.get(text, 0)}))

    def searches(self):
        return [text for kind, text in self.requests if kind == "search"]


@pytest.fixture
def make_client():
    def build(api, **kwargs):
        kwargs.setdefault("rate", 100000)
        return PubTatorClient(transport=httpx.MockTransport(api), sleep=lambda _: None, **kwargs)

    return build
