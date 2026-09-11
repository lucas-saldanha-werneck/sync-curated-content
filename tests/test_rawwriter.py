from lib.rawwriter import slugify, raw_relpath, render_raw, frames_reldir, sanitize_control_chars
from lib.fetch_types import FetchResult

ITEM = {"id": 77, "link": "https://youtu.be/x", "title": "Como Escolher Inversor: Guia 2026!", "excerpt": "resumo", "note": "ver p/ LuxVolt",
        "tags": ["solar"], "type": "video", "created": "2026-09-10T23:30:00.000Z", "cover": "", "domain": "youtu.be", "raw": {"_id": 77}}

def test_slug_and_path():
    assert slugify("Como Escolher Inversor: Guia 2026!", "x") == "como-escolher-inversor-guia-2026"
    assert raw_relpath(ITEM, "youtube", "America/Sao_Paulo") == "raw/curated/2026-09-10_youtube-como-escolher-inversor-guia-2026-r77.md"
    assert frames_reldir("raw/curated/a-r1.md") == "raw/curated/a-r1/frames"

def test_sanitize():
    assert sanitize_control_chars("a​b c\x07d e") == "ab cd e"

def test_render_raw_frontmatter_and_sections():
    fr = FetchResult(platform="youtube", text="fala​ do vídeo", method="captions", title="T", author="A", duration_s=120)
    md = render_raw(ITEM, fr, "youtube", "raw/curated/x-r77.md", ["01_0000.jpg"])
    assert md.startswith("---\n") and "raindrop_id: 77" in md and 'transcript_method: "captions"' in md and "ingested: true" in md
    assert "## Nota do Lucas\nver p/ LuxVolt" in md and "fala do vídeo" in md and "​" not in md
    assert '"_id": 77' in md and "frames:\n  - 01_0000.jpg" in md

def test_frontmatter_strings_are_quoted():
    fr = FetchResult(platform="youtube", text="test", method="captions", title="T", author="A", duration_s=120)
    md = render_raw(ITEM, fr, "youtube", "raw/curated/x-r77.md", ["01_0000.jpg"])
    assert 'created: "2026-09-10T23:30:00.000Z"' in md
    assert 'platform: "youtube"' in md
    assert 'transcript_method: "captions"' in md
    assert 'origem: "raindrop"' in md

def test_empty_frames_list():
    fr = FetchResult(platform="youtube", text="test", method="captions", title="T", author="A", duration_s=120)
    md = render_raw(ITEM, fr, "youtube", "raw/curated/x-r77.md", [])
    assert "frames: []" in md
