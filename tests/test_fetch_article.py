from pathlib import Path
from lib.config import Config
from lib.fetch_article import fetch_article, fetch_pdf

HTML = (Path(__file__).parent / "fixtures" / "article.html").read_bytes()


def test_trafilatura_first():
    r = fetch_article("https://blog.example.com/p", Config(), None, None, opener=lambda u, **k: (HTML, u, {}))
    assert r.method == "trafilatura" and "inversor" in r.text.lower() and len(r.text) > 400


def test_falls_back_to_jina_then_firecrawl():
    calls = []
    def opener(u, **k):
        calls.append(u)
        if u.startswith("https://r.jina.ai/"): return (b"x" * 10, u, {})          # curto demais
        return (b"<html><body><p>oi</p></body></html>", u, {})
    def fake_firecrawl_post(u, k):
        return b'{"success":true,"data":{"markdown":"' + b"conteudo " * 100 + b'"}}'
    r = fetch_article("https://x.com/p", Config(), "jk", "fk", opener=opener, firecrawl_post=fake_firecrawl_post)
    assert r.method == "firecrawl" and len(r.text) > 400 and any("r.jina.ai" in c for c in calls)


def test_all_fail():
    r = fetch_article("https://x.com/p", Config(), None, None, opener=lambda u, **k: (b"", u, {}))
    assert r.method == "none" and "trafilatura" in r.error and "jina" in r.error


def test_pdf_page_limit(tmp_path):
    cfg = Config(pdf_max_pages=2)
    def run(cmd, **k):
        class P: returncode = 0; stdout = "Pages: 5\n" if cmd[0] == "pdfinfo" else "texto"
        return P()
    r = fetch_pdf("https://x.com/a.pdf", tmp_path, cfg, opener=lambda u, **k: (b"%PDF-1.4", u, {}), run=run)
    assert r.method == "none" and "páginas" in r.error


def test_pdf_missing_poppler_returns_none(tmp_path):
    cfg = Config(pdf_max_pages=10)
    def run(cmd, **k):
        raise FileNotFoundError("pdfinfo not found")
    r = fetch_pdf("https://x.com/a.pdf", tmp_path, cfg, opener=lambda u, **k: (b"%PDF-1.4", u, {}), run=run)
    assert r.method == "none" and "pdf" in r.error


def test_jina_success():
    call_count = [0]
    def opener(u, **k):
        call_count[0] += 1
        if u.startswith("https://r.jina.ai/"):
            return (b"Title: Meu artigo\nConteudo " * 50, u, {})
        return (b"<html><body><p>oi</p></body></html>", u, {})
    r = fetch_article("https://x.com/p", Config(), "jk", None, opener=opener)
    assert r.method == "jina" and r.title == "Meu artigo" and len(r.text) > 400


def test_firecrawl_post_is_called_with_key():
    calls = []
    def fake_firecrawl_post(u, k):
        calls.append((u, k))
        return b'{"success":true,"data":{"markdown":"' + b"conteudo " * 100 + b'"}}'
    def opener(u, **k):
        return (b"<html><body><p>oi</p></body></html>", u, {})
    r = fetch_article("https://blog.example.com/p", Config(), None, "fk", opener=opener, firecrawl_post=fake_firecrawl_post)
    assert r.method == "firecrawl" and len(r.text) > 400 and len(calls) == 1 and calls[0] == ("https://blog.example.com/p", "fk")
