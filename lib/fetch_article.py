"""Artigo: trafilatura local → Jina Reader → Firecrawl. PDF: pdftotext com limite de páginas."""
from __future__ import annotations
import json, re, subprocess, urllib.request
from pathlib import Path
from lib.fetch_types import FetchResult
from lib.net_guard import safe_open

MIN_CHARS = 400


def _firecrawl_post(url, key):
    """Firecrawl é POST para destino fixo (api.firecrawl.dev); não passa pelo safe_open (sem SSRF possível)."""
    req = urllib.request.Request("https://api.firecrawl.dev/v1/scrape", data=json.dumps({"url": url, "formats": ["markdown"]}).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def fetch_article(url: str, cfg, jina_key, firecrawl_key, opener=safe_open, run=subprocess.run) -> FetchResult:
    errors = []
    try:
        import trafilatura
        html, final, _ = opener(url, timeout=60)
        h = html.decode("utf-8", "ignore")
        text = trafilatura.extract(h, include_comments=False, output_format="markdown", url=final) or ""
        meta = trafilatura.extract_metadata(h, default_url=final)
        if len(text) >= MIN_CHARS:
            return FetchResult(platform="article", text=text, method="trafilatura",
                               title=getattr(meta, "title", None), author=getattr(meta, "author", None))
        errors.append(f"trafilatura: {len(text)} chars")
    except Exception as e:
        errors.append(f"trafilatura: {e}")
    try:
        hdr = {"Accept": "text/plain", **({"Authorization": f"Bearer {jina_key}"} if jina_key else {})}
        body, _, _ = opener("https://r.jina.ai/" + url, timeout=90, headers=hdr)
        text = body.decode("utf-8", "ignore")
        if len(text) >= MIN_CHARS:
            return FetchResult(platform="article", text=text, method="jina", title=_first_title(text))
        errors.append(f"jina: {len(text)} chars")
    except Exception as e:
        errors.append(f"jina: {e}")
    if firecrawl_key:
        try:
            raw = _firecrawl_post(url, firecrawl_key) if opener is safe_open else opener("https://api.firecrawl.dev/v1/scrape?url=" + url, timeout=120)[0]
            data = json.loads(raw.decode("utf-8", "ignore"))
            text = (data.get("data") or {}).get("markdown") or ""
            if len(text) >= MIN_CHARS:
                return FetchResult(platform="article", text=text, method="firecrawl",
                                   title=((data.get("data") or {}).get("metadata") or {}).get("title"))
            errors.append(f"firecrawl: {len(text)} chars")
        except Exception as e:
            errors.append(f"firecrawl: {e}")
    else:
        errors.append("firecrawl: sem chave")
    return FetchResult(platform="article", method="none", error=" | ".join(errors))


def _first_title(md: str) -> str | None:
    m = re.search(r"^Title:\s*(.+)$|^#\s+(.+)$", md, re.M)
    return (m.group(1) or m.group(2)).strip() if m else None


def fetch_pdf(url: str, work: Path, cfg, opener=safe_open, run=subprocess.run) -> FetchResult:
    work = Path(work); work.mkdir(parents=True, exist_ok=True)
    try:
        data, _, _ = opener(url, timeout=120)
    except Exception as e:
        return FetchResult(platform="pdf", method="none", error=f"download: {e}")
    pdf = work / "doc.pdf"; pdf.write_bytes(data)
    info = run(["pdfinfo", str(pdf)], capture_output=True, text=True, timeout=60)
    m = re.search(r"Pages:\s+(\d+)", getattr(info, "stdout", "") or "")
    pages = int(m.group(1)) if m else 0
    if pages == 0 or pages > cfg.pdf_max_pages:
        return FetchResult(platform="pdf", method="none", error=f"pdf com {pages} páginas (limite {cfg.pdf_max_pages})")
    out = run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, timeout=120)
    return FetchResult(platform="pdf", text=getattr(out, "stdout", "") or "", method="pdftotext", extra={"pages": pages})
