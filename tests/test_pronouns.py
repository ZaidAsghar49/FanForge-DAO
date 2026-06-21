import pytest
from file_claim_parser import isolate_claims

def test_pronoun_he_resolution():
    chunks = [
        "Virat Kohli is a legendary batsman. He averages 55.4 in ODI matches."
    ]
    claims = isolate_claims(chunks)
    assert len(claims) > 0
    assert "Virat Kohli averages 55.4 in ODI matches." in claims

def test_pronoun_his_resolution():
    chunks = [
        "Virat Kohli anchors the Indian team. His strike rate is 98.4 in ODI matches in India."
    ]
    claims = isolate_claims(chunks)
    assert len(claims) > 0
    assert "Virat Kohli's strike rate is 98.4 in ODI matches in India." in claims

def test_pronoun_plural_and_other_cases():
    chunks = [
        "Steve Smith plays for Australia. Their batting average is 55.4 in Test matches in Away conditions.",
        "Mithali Raj is a legendary player. She scored 6000 runs.",
        "Joe Root was in top form. They conceded 45 runs when bowling against him.",
        "Kane Williamson played exceptionally. He scored 250 runs himself to chase the target."
    ]
    claims = isolate_claims(chunks)
    print("ISOLATED CLAIMS:", claims)
    
    assert any("Steven Smith's batting average is 55.4" in c for c in claims)
    assert any("Mithali Raj scored 6000 runs" in c for c in claims)
    assert any("against Joe Root" in c for c in claims)
    assert any("Kane Williamson scored 250 runs Kane Williamson" in c for c in claims)
