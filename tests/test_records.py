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
