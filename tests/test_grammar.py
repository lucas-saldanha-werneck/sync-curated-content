import pytest
from lib.grammar import validate_text, validate_triggers, validate_type, GrammarError

PAGES = {"energia-e-recarga", "pessoal"}

test_bad = ["a <b>", "x <!-- curated:r1 -->", "![[img.png]]", "![a](b)", "```py"]
test_bad.append("---\nfront")
test_bad.extend(["veja curated:r9", "[l](javascript:alert(1))", "[l](obsidian://open)", "[[pagina-inexistente]]", "zero\u200bwidth", "https://outro.com/x"])

@pytest.mark.parametrize("bad", test_bad)
def test_rejects(bad):
    with pytest.raises(GrammarError):
        validate_text(bad, 600, PAGES, source_url="https://fonte.com/a")

def test_accepts_plain_markdown_and_links():
    s = validate_text("Tarifa de **R$ 1,20/kWh** ([[energia-e-recarga]]). Fonte: https://fonte.com/a\r\n- item", 600, PAGES, "https://fonte.com/a")
    assert "\r" not in s and s.endswith("- item")

def test_length():
    with pytest.raises(GrammarError): validate_text("x" * 601, 600, PAGES)

def test_triggers_and_type():
    assert validate_triggers(["Solar", "solar", "banana", "inversor"], {"solar", "inversor"}) == ["solar", "inversor"]
    with pytest.raises(GrammarError): validate_triggers(["banana"], {"solar"})
    assert validate_type("skill", ["skill", "plugin"]) == "skill"
    with pytest.raises(GrammarError): validate_type("virus", ["skill"])

def test_reference_link_rejected():
    with pytest.raises(GrammarError):
        validate_text("[click][1]\n[1]: javascript:alert(1)", 600, PAGES)

def test_curated_case_insensitive():
    with pytest.raises(GrammarError):
        validate_text("Curated:r9", 600, PAGES)

def test_bare_bad_scheme_rejected():
    with pytest.raises(GrammarError):
        validate_text("Check this: javascript:alert(1)", 600, PAGES)
