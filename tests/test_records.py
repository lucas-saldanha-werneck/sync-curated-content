from lib.records import Records, terminal_state, record_path
from lib.config import Config
from tests.test_dropbox_store import make as make_store

def test_terminal_state_order():
    assert terminal_state({"meta", "captured"}) is None
    assert terminal_state({"meta", "parcial", "wiki"}) == "wiki"   # wiki > parcial
    assert terminal_state({"erro", "nowiki"}) == "nowiki"

def test_record_path():
    cfg = Config()
    assert record_path(cfg, 42, "meta") == "/Projects/vault/raw/curated/_catalog/r42.meta.json"

def test_write_refresh_state():
    store = make_store(); cfg = Config(); r = Records(store, cfg)
    r.write(7, "meta", {"link": "https://a"}); r.write(7, "captured", {"raw": "raw/curated/x.md"})
    m = r.refresh()
    assert m[7] == {"meta", "captured"} and r.state(7) is None
    r.write(7, "wiki", {"pages": ["wiki/a.md"]}); r.refresh()
    assert r.state(7) == "wiki"
    assert r.read(7, "meta")["link"] == "https://a"
    assert r.write(7, "meta", {"link": "https://a"})   # idêntico → ok, não levanta


def test_redo_generation_reopens_item():
    # undo + retry (Task 26): registros são imutáveis, então "re-destilar" = nova geração (redoN → distillN/wikiN)
    from lib.records import generation, gen_kind, has_distill
    store = make_store(); cfg = Config(); r = Records(store, cfg)
    r.write(7, "meta", {}); r.write(7, "captured", {"raw": "raw/curated/x.md"})
    r.write(7, "distill", {"decision": "wiki", "catalog": {"name": "v0"}, "date": "2026-09-11", "raw": "raw/curated/x.md"}); r.write(7, "wiki", {}); r.refresh()
    assert r.state(7) == "wiki" and has_distill(r.kinds(7)) and generation(r.kinds(7)) == 0
    assert r.request_redo(7) == "redo1"; r.refresh()
    k = r.kinds(7)
    assert generation(k) == 1 and gen_kind("distill", k) == "distill1" and not has_distill(k) and terminal_state(k) is None
    assert r.request_redo(7) is None                 # já reaberto: não empilha redo2 sem nova destilação
    r.write(7, gen_kind("distill", k), {"decision": "wiki", "catalog": {"name": "v1"}, "date": "2026-09-12", "raw": "raw/curated/x.md"}); r.write(7, gen_kind("wiki", k), {}); r.refresh()
    assert r.state(7) == "wiki" and has_distill(r.kinds(7))
    assert [e["catalog"]["name"] for e in r.catalog_entries()] == ["v1"]   # catálogo usa a geração atual
    assert r.latest_distill(7)["catalog"]["name"] == "v1"
    assert r.request_redo(7) == "redo2"
    assert terminal_state({"captured", "distill", "wiki", "redo1", "nowiki"}) == "nowiki"   # opt-out não tem geração


def test_request_redo_reopens_erro_item_without_distill():
    # M3 (review Opus): item em sr:erro nunca chegou a ter registro distill; retry precisa reabri-lo mesmo assim
    from lib.queue import needs_distill, needs_capture
    store = make_store(); cfg = Config(); r = Records(store, cfg)
    r.write(8, "meta", {}); r.write(8, "captured", {"raw": "raw/curated/y.md"}); r.write(8, "erro", {"error": "incompleto 3x"}); r.refresh()
    assert r.state(8) == "erro" and not needs_distill(r.kinds(8))
    assert r.request_redo(8) == "redo1"; r.refresh()
    assert r.state(8) is None and needs_distill(r.kinds(8))
    r.write(9, "meta", {}); r.write(9, "erro", {"error": "raw existe com outro id"}); r.refresh()   # erro na captura
    assert r.request_redo(9) == "redo1"; r.refresh(); assert needs_capture({"id": 9}, r.kinds(9))
    r.write(10, "meta", {}); r.write(10, "retry1", {}); r.refresh()
    assert r.request_redo(10) is None                      # item só com retry de captura: retry comum (journal) basta


# --- recaptura: gerações também valem para a CAPTURA (2026-09-12/14) --------------------
# Motivo: 11 vídeos do YouTube foram capturados SEM legenda (bot-wall por IP de datacenter).
# Depois o túnel residencial passou a funcionar, mas `retry` só re-destilava o raw velho e
# vazio. Agora `recapture` abre uma geração que refaz a captura e grava um raw próprio.

def test_generation_counts_recap_markers():
    from lib.records import generation
    assert generation({"captured", "parcial"}) == 0
    assert generation({"captured", "parcial", "recap1"}) == 1
    assert generation({"captured", "parcial", "redo1", "recap2"}) == 2


def test_gen_kind_applies_to_captured():
    from lib.records import gen_kind
    assert gen_kind("captured", {"captured", "parcial"}) == "captured"
    assert gen_kind("captured", {"captured", "parcial", "recap1"}) == "captured1"


def test_needs_recapture_only_when_the_generation_asked_for_it():
    from lib.records import needs_recapture
    assert not needs_recapture({"captured", "parcial"})
    assert not needs_recapture({"captured", "parcial", "redo1"})      # redo = só re-destilar
    assert needs_recapture({"captured", "parcial", "recap1"})
    assert not needs_recapture({"captured", "parcial", "recap1", "captured1", "distill1"})


def test_has_capture_respects_the_generation():
    from lib.records import has_capture
    assert not has_capture({"meta"})
    assert has_capture({"meta", "captured"})
    assert has_capture({"meta", "captured", "redo1"})                  # redo reaproveita o raw antigo
    assert not has_capture({"meta", "captured", "recap1"})             # recap exige captura nova
    assert has_capture({"meta", "captured", "recap1", "captured1"})


def test_request_recapture_opens_a_generation():
    from lib.records import gen_kind, needs_recapture, has_capture
    store = make_store(); cfg = Config(); r = Records(store, cfg)
    r.write(8, "meta", {}); r.write(8, "captured", {"raw": "raw/curated/x.md", "method": "none"})
    r.write(8, "distill", {"decision": "parcial", "catalog": {"name": "v0"}, "date": "2026-09-12", "raw": "raw/curated/x.md"})
    r.write(8, "parcial", {}); r.refresh()
    assert r.request_recapture(8) == "recap1"; r.refresh()
    k = r.kinds(8)
    assert terminal_state(k) is None and needs_recapture(k) and not has_capture(k)
    assert gen_kind("captured", k) == "captured1"
    r.write(8, "captured1", {"raw": "raw/curated/x.g1.md", "method": "apify-captions"}); r.refresh()
    k = r.kinds(8)
    assert has_capture(k) and not needs_recapture(k)
    assert r.request_recapture(8) is None            # já reaberto: não empilha recap2 sem nova destilação


def test_capture_kind_picks_the_raw_that_vale_para_a_geracao():
    """redo reaproveita o raw da geração anterior; recaptura usa o próprio."""
    from lib.records import capture_kind
    assert capture_kind({"meta"}) is None
    assert capture_kind({"meta", "captured"}) == "captured"
    assert capture_kind({"meta", "captured", "redo1"}) == "captured"          # redo: raw velho serve
    assert capture_kind({"meta", "captured", "recap1"}) == "captured"         # ainda não recapturou
    assert capture_kind({"meta", "captured", "recap1", "captured1"}) == "captured1"
    assert capture_kind({"meta", "captured", "recap1", "captured1", "redo2"}) == "captured1"
