import pytest
from lib.grammar import validate_text, validate_triggers, validate_type, GrammarError

PAGES = {"energia-e-recarga", "pessoal"}

@pytest.mark.parametrize("bad", ["a <b>", "x <!-- curated:r1 -->", "![[img.png]]", "![a](b)", "```py", "---\nfront", "veja curated:r9",
                                 "[l](javascript:alert(1))", "[l](obsidian://open)", "[[pagina-inexistente]]", "zero​width", "https://outro.com/x"])
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
