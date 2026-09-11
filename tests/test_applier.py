import json
from pathlib import Path

import pytest

from lib.applier import plan_ops, apply, insert_after_anchor, marker, render_catalog, LimitError
from lib.snapshot import Snap
from lib.config import Config
from lib.journal import Journal
from tests.test_dropbox_store import make as make_store

FIX = Path(__file__).parent / "fixtures"
OUT = json.loads((FIX / "claude_output.json").read_text())
ITEMS = {OUT["items"][0]["raindrop_id"]: {"link": "https://fonte.com/a", "title": "Fonte A", "raw": "raw/curated/2026-09-10_youtube-fonte-a-r1.md"}}
PAGE = b"# Energia\n\n## Tarifa\n\ntexto antigo\n\n## Outra\n"


def snap():
    return Snap({"wiki/energia-e-recarga.md": (PAGE, "rev1"), "CLAUDE.md": (b"", "r0")})


def test_insert_after_anchor():
    assert insert_after_anchor("a\n## X\nb\n", "## X", "BLOCO") == "a\n## X\nBLOCO\nb\n"
    assert insert_after_anchor("a\n", "## nope", "B") is None
    assert insert_after_anchor("a\n", "", "B") == "a\nB\n"


def test_plan_ops_builds_marked_blocks_catalog_and_log():
    m = plan_ops(OUT, snap(), ITEMS, Config(), "run1", "2026-09-11", {"inversor", "solar"})
    paths = {o.path for o in m.ops}
    assert "wiki/energia-e-recarga.md" in paths and "wiki/ferramentas-e-ideias.md" in paths and "wiki/log/2026-09_curated.md" in paths
    page = [o for o in m.ops if o.path == "wiki/energia-e-recarga.md"][0]
    assert page.kind == "update" and page.rev_before == "rev1" and marker(1, "2026-09-11").encode() in page.after
    assert b"raw/curated/2026-09-10_youtube-fonte-a-r1.md" in page.after      # claim cita o raw
    assert m.per_item[1]["decision"] == "wiki" and set(m.per_item[1]["required"]) == {"claims", "catalog", "log"}


def test_plan_ops_both_claims_present_not_self_skipped_by_idempotency():
    # regressão: inserir o 2º claim do mesmo item não pode ser tratado como "já aplicado"
    # só porque o 1º claim do mesmo item já deixou o marcador no texto.
    m = plan_ops(OUT, snap(), ITEMS, Config(), "run1", "2026-09-11", {"inversor", "solar"})
    page = [o for o in m.ops if o.path == "wiki/energia-e-recarga.md"][0]
    assert page.after.count(marker(1, "2026-09-11").encode()) == 2
    assert b"22kW" in page.after and b"tarifa branca" in page.after


def test_plan_ops_skips_already_applied_marker():
    already = PAGE.replace(
        b"## Tarifa\n\n",
        b"## Tarifa\n\n" + marker(1, "2026-09-10").encode() + b"\n- velho ([raw](../old.md))\n",
    )
    s = Snap({"wiki/energia-e-recarga.md": (already, "rev1"), "CLAUDE.md": (b"", "r0")})
    m = plan_ops(OUT, s, ITEMS, Config(), "run1", "2026-09-11", {"inversor", "solar"})
    page_ops = [o for o in m.ops if o.path == "wiki/energia-e-recarga.md"]
    assert page_ops == []   # nada novo a inserir: marcador de r1 já presente


def test_plan_ops_anchor_missing_sends_item_to_retry():
    out = json.loads(json.dumps(OUT)); out["items"][0]["claims"][0]["anchor"] = "## Não existe"
    m = plan_ops(out, snap(), ITEMS, Config(), "run1", "2026-09-11", {"inversor", "solar"})
    assert m.per_item[1]["decision"] == "retry" and not [o for o in m.ops if o.path == "wiki/energia-e-recarga.md"]


def test_plan_ops_retry_does_not_leak_partial_edits_into_shared_page():
    # o 1º claim tem âncora válida (aplicaria com sucesso isoladamente), o 2º não —
    # o item inteiro deve virar "retry" sem deixar nenhum resquício da 1ª edição na página.
    out = json.loads(json.dumps(OUT))
    out["items"][0]["claims"][1]["anchor"] = "## Não existe"
    m = plan_ops(out, snap(), ITEMS, Config(), "run1", "2026-09-11", {"inversor", "solar"})
    assert m.per_item[1]["decision"] == "retry"
    page_ops = [o for o in m.ops if o.path == "wiki/energia-e-recarga.md"]
    assert page_ops == []
    # catálogo e log também não devem ter sido gerados para um item que falhou
    assert not [o for o in m.ops if o.path == "wiki/ferramentas-e-ideias.md"]
    assert not [o for o in m.ops if o.path == "wiki/log/2026-09_curated.md"]


def test_plan_ops_limit_delta():
    out = json.loads(json.dumps(OUT)); out["items"][0]["claims"] = [{"page": "wiki/energia-e-recarga.md", "anchor": "## Tarifa", "text": "x" * 600}] * 7
    with pytest.raises(LimitError):
        plan_ops(out, snap(), ITEMS, Config(max_delta_bytes=1000), "run1", "2026-09-11", {"inversor", "solar"})


def test_apply_cas_and_journal(tmp_path):
    store = make_store(); store.add("/Projects/vault/wiki/energia-e-recarga.md", PAGE)
    ok, rev, _ = store.exists("/Projects/vault/wiki/energia-e-recarga.md")
    s = Snap({"wiki/energia-e-recarga.md": (PAGE, rev), "CLAUDE.md": (b"", "r0")})
    m = plan_ops(OUT, s, ITEMS, Config(), "run1", "2026-09-11", {"inversor", "solar"})
    j = Journal(tmp_path / "j.db"); j.start_run("distill", [1])
    m2 = apply(m, store, j, Config())
    assert all(o.rev_after for o in m2.ops)
    data, _ = store.download("/Projects/vault/wiki/energia-e-recarga.md"); assert b"curated:r1" in data
    assert store.exists("/Projects/vault/wiki/log/2026-09_curated.md")[0]
    assert set(m2.per_item[1]["done"]) == {"claims", "catalog", "log"}


def test_render_catalog_escapes_pipes_and_newlines():
    entries = [{
        "id": 1, "date": "2026-09-11", "raw": "raw/curated/x.md",
        "catalog": {"name": "Ferramenta | Injetada", "type": "ferramenta", "what": "l1\nl2",
                    "when": "quando usar", "triggers": ["a|b"], "implement": False},
    }]
    out = render_catalog(entries).decode("utf-8")
    sep = "|---|---|---|---|---|---|---|---|"
    idx = out.index(sep) + len(sep) + 1                # pula o "\n" logo após o separador
    rows_section = out[idx:]
    assert rows_section.count("\n") == len(entries) == 1
    assert "Ferramenta \\| Injetada" in rows_section
    assert "l1 l2" in rows_section                      # newline virou espaço, sem quebrar a linha
    assert "a\\|b" in rows_section


def test_plan_ops_bad_id_does_not_abort_batch():
    out = json.loads(json.dumps(OUT))
    bad_item = dict(out["items"][0]); bad_item["raindrop_id"] = "x"
    out["items"] = [bad_item] + out["items"]            # item malformado antes do item válido
    m = plan_ops(out, snap(), ITEMS, Config(), "run1", "2026-09-11", {"inversor", "solar"})
    assert -1 not in m.per_item
    assert any("raindrop_id" in w for w in m.warnings)
    assert [o for o in m.ops if o.path == "wiki/energia-e-recarga.md"]
    assert m.per_item[1]["decision"] == "wiki"


def test_apply_conflict_propagates(tmp_path):
    from lib.dropbox_store import ConflictError

    store = make_store(); store.add("/Projects/vault/wiki/energia-e-recarga.md", PAGE)
    s = Snap({"wiki/energia-e-recarga.md": (PAGE, "rev-velho"), "CLAUDE.md": (b"", "r0")})
    m = plan_ops(OUT, s, ITEMS, Config(), "run1", "2026-09-11", {"inversor", "solar"})
    with pytest.raises(ConflictError):
        apply(m, store, Journal(tmp_path / "j.db"), Config())


def test_render_catalog_ignores_entries_without_catalog():
    # registro de decisão "skip" tem "catalog": null — não pode derrubar a regeração do catálogo (bug 2026-09-11, r1707710772)
    entries = [
        {"id": 1, "date": "2026-09-11", "raw": "raw/curated/x.md", "decision": "skip", "catalog": None},
        {"id": 2, "date": "2026-09-11", "raw": "raw/curated/y.md", "decision": "wiki",
         "catalog": {"name": "Y", "type": "ferramenta", "what": "w", "when": "q", "triggers": [], "implement": False}},
    ]
    out = render_catalog(entries).decode("utf-8")
    assert "<!-- r2 -->" in out and "<!-- r1 -->" not in out
