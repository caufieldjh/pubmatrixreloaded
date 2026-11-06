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


def test_exact_match_can_be_the_wrong_type_without_a_concept(make_client):
    assert resolve_term("PFOA", make_client(FakeApi())).query == "@GENE_PFOA"


def test_concept_filter_keeps_lookup_to_one_type(make_client):
    term = resolve_term("PFOA", make_client(FakeApi()), "chemical")
    assert (term.kind, term.query) == ("text", '"PFOA"')
    assert term.suggestion == "@CHEMICAL_perfluorooctanoic_acid"


def test_synonyms_become_one_or_group(make_client):
    term = resolve_term("Ames test | BRCA1 | @GENE_X", make_client(FakeApi()))
    assert term.kind == "synonyms"
    assert term.query == '("Ames test" OR @GENE_BRCA1 OR @GENE_X)'
    assert term.label == "Ames test"


def test_stray_quotes_are_refused_not_flattened(make_client):
    term = resolve_term('"Ames test" OR "Ames assay"', make_client(FakeApi()))
    assert "stray quote" in term.problem


def test_greek_letters_are_spelled_out_in_phrases(make_client):
    term = resolve_term("17β-estradiol", make_client(FakeApi()))
    assert term.query == '"17beta-estradiol"'
    assert term.label == "17β-estradiol"


def test_text_with_no_documents_gets_a_note(make_client):
    term = resolve_term('"xyzzy"', make_client(FakeApi()))
    term.total = 0
    assert "check the spelling" in term.note
