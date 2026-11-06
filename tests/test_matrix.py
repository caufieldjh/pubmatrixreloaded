import pytest

from conftest import FakeApi
from pubmatrix.matrix import build_matrix


def test_cooccurrence_counts_and_links(make_client):
    api = FakeApi({"@GENE_BRCA1": 10, '"tumor"': 20, '@GENE_BRCA1 AND "tumor"': 5})
    matrix = build_matrix(make_client(api), ["BRCA1"], ["tumor"])
    cell = matrix.cells[0, 0]
    assert cell.count == 5
    assert cell.link.endswith("text=%40GENE_BRCA1%20AND%20%22tumor%22")
    assert matrix.rows[0].total == 10
    assert matrix.failed == []


def test_true_zero_stays_zero(make_client):
    api = FakeApi({"@GENE_A": 10, "@GENE_B": 20})
    matrix = build_matrix(make_client(api), ["@GENE_A"], ["@GENE_B"])
    assert matrix.cells[0, 0].count == 0
    assert matrix.cells[0, 0].error is None


def test_unknown_entity_is_unknown_not_zero(make_client):
    api = FakeApi({"@GENE_A": 10})
    matrix = build_matrix(make_client(api), ["@GENE_A"], ["@DISEASE_Typo"])
    cell = matrix.cells[0, 0]
    assert cell.count is None
    assert "@DISEASE_Typo" in cell.error
    assert "@GENE_A AND @DISEASE_Typo" not in api.searches()


def test_text_with_no_hits_gives_true_zero_without_querying_pairs(make_client):
    api = FakeApi({"@GENE_A": 10})
    matrix = build_matrix(make_client(api), ["@GENE_A"], ['"nothing here"'])
    assert matrix.cells[0, 0].count == 0
    assert len(api.searches()) == 2


def test_relation_query_form(make_client):
    query = "relations:treat|@CHEMICAL_A|@DISEASE_B"
    api = FakeApi({"@CHEMICAL_A": 9, "@DISEASE_B": 9, query: 4})
    matrix = build_matrix(make_client(api), ["@CHEMICAL_A"], ["@DISEASE_B"], relation="treat")
    assert matrix.cells[0, 0].count == 4


def test_relation_with_text_term_is_unknown_not_zero(make_client):
    api = FakeApi({"@GENE_BRCA1": 10, '"apoptosis"': 9})
    matrix = build_matrix(make_client(api), ["BRCA1"], ["apoptosis"], relation="ANY")
    cell = matrix.cells[0, 0]
    assert cell.count is None
    assert "@DISEASE_Malformations" in cell.error
    assert not [q for q in api.searches() if q.startswith("relations:")]


def test_unknown_relation_type_is_refused(make_client):
    with pytest.raises(ValueError):
        build_matrix(make_client(FakeApi()), ["a"], ["b"], relation="BOGUS")


def test_one_failed_cell_does_not_stop_the_rest(make_client):
    api = FakeApi(
        {"@A": 5, "@B": 5, "@C": 5, "@A AND @C": 2},
        {"@B AND @C": [400]},
    )
    matrix = build_matrix(make_client(api), ["@A", "@B"], ["@C"])
    assert matrix.cells[0, 0].count == 2
    assert matrix.cells[1, 0].count is None
    assert "HTTP 400" in matrix.cells[1, 0].error


def test_lookup_failure_blocks_only_that_term(make_client):
    api = FakeApi({"@A": 5, "@B": 5, "@A AND @B": 1}, {"@C": [400]})
    matrix = build_matrix(make_client(api), ["@A", "@C"], ["@B"])
    assert matrix.cells[0, 0].count == 1
    assert matrix.cells[1, 0].count is None


def test_duplicate_terms_are_merged(make_client):
    api = FakeApi({"@A": 5, "@B": 5})
    matrix = build_matrix(make_client(api), ["@A", "@A "], ["@B"])
    assert len(matrix.rows) == 1
