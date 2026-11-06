from conftest import FakeApi
from pubmatrix.terms import phrase, read_term_file, resolve_term


def test_exact_name_becomes_entity(make_client):
    term = resolve_term("BRCA1", make_client(FakeApi()))
    assert (term.kind, term.query, term.biotype) == ("entity", "@GENE_BRCA1", "gene")


def test_exact_synonym_becomes_entity_case_insensitively(make_client):
    term = resolve_term("p53", make_client(FakeApi()))
    assert term.query == "@GENE_TP53"
    assert term.name == "TP53"


def test_label_is_what_the_user_typed_without_syntax(make_client):
    client = make_client(FakeApi())
    assert resolve_term("p53", client).label == "p53"
    assert resolve_term('"DNA repair"', client).label == "DNA repair"
    assert resolve_term("@DISEASE_Breast_Neoplasms", client).label == "Breast Neoplasms"


def test_fuzzy_candidate_is_only_a_suggestion(make_client):
    term = resolve_term("apoptosis", make_client(FakeApi()))
    assert (term.kind, term.query) == ("text", '"apoptosis"')
    assert term.suggestion == "@DISEASE_Malformations"


def test_explicit_id_skips_lookup(make_client):
    api = FakeApi()
    assert resolve_term("@GENE_X", make_client(api)).query == "@GENE_X"
    assert api.requests == []


def test_quoted_term_skips_lookup(make_client):
    api = FakeApi()
    term = resolve_term('"BRCA1"', make_client(api))
    assert (term.kind, term.query) == ("text", '"BRCA1"')
    assert api.requests == []


def test_phrase_strips_inner_quotes():
    assert phrase('a "b" c') == '"a b c"'


def test_read_term_file_skips_comments_and_blanks(tmp_path):
    path = tmp_path / "t.txt"
    path.write_text("# note\n\nBRCA1\n  TP53  \n")
    assert read_term_file(str(path)) == ["BRCA1", "TP53"]
