from lib.journal import Journal

def test_item_roundtrip(tmp_path):
    j = Journal(tmp_path / "j.db")
    j.upsert_item(1, state="captured", link="https://x", title="t", retries=0)
    j.upsert_item(1, state="raw", retries=1)
    it = j.get_item(1)
    assert it["state"] == "raw" and it["link"] == "https://x" and it["retries"] == 1
    assert [i["id"] for i in j.items_in_state("raw")] == [1]
    j.close()

def test_run_and_ops(tmp_path):
    j = Journal(tmp_path / "j.db")
    rid = j.start_run("distill", [1, 2])
    op = j.add_op(rid, 1, "wiki/a.md", "update", "rev1", "sha")
    assert j.ops(rid)[0]["rev_after"] is None
    j.confirm_op(op, "rev2")
    assert j.ops(rid)[0]["rev_after"] == "rev2"
    j.finish_run(rid, "ok", {"items": 2})
    assert j.runs_today("distill", "America/Sao_Paulo") == 1
    j.close()

def test_lock_is_singleton(tmp_path):
    a = Journal(tmp_path / "j.db"); b = Journal(tmp_path / "j.db")
    assert a.acquire_lock() is True
    assert b.acquire_lock() is False
    a.release_lock()
    assert b.acquire_lock() is True
    a.close(); b.close()

def test_groq_and_requests(tmp_path):
    j = Journal(tmp_path / "j.db")
    j.add_groq_seconds(100); j.add_groq_seconds(50)
    assert j.count_groq_seconds_today("America/Sao_Paulo") == 150
    j.enqueue_request("now", None); j.enqueue_request("retry", "42")
    assert [r["cmd"] for r in j.pop_requests()] == ["now", "retry"]
    assert j.pop_requests() == []
    j.close()

def test_pop_requests_atomic_returning(tmp_path):
    j = Journal(tmp_path / "j.db")
    j.enqueue_request("cmd1", None); j.enqueue_request("cmd2", "arg")
    rows = j.pop_requests()
    assert len(rows) == 2
    assert [r["cmd"] for r in rows] == ["cmd1", "cmd2"]
    assert j.pop_requests() == []
    j.close()


def test_runs_today_counts_only_runs_that_reached_claude(tmp_path):
    # teto diário limita chamadas ao Claude: lote abortado antes de terminar (running/aborted) ou vazio não conta
    j = Journal(tmp_path / "j.db")
    j.finish_run(j.start_run("distill", [1]), "ok", {})
    j.finish_run(j.start_run("distill", [2]), "distill_invalid", {})
    j.finish_run(j.start_run("distill", [3]), "aborted", {})
    j.finish_run(j.start_run("distill", [4]), "empty", {})
    j.start_run("distill", [5])                                   # órfão "running"
    assert j.runs_today("distill", "America/Sao_Paulo") == 2
    j.close()

def test_consecutive_invalid_counts_only_the_tail(tmp_path):
    """Alimenta a trava do should_run: lotes que morreram sem produzir saída válida, em sequência."""
    j = Journal(tmp_path / "j2.db")
    assert j.consecutive_invalid("distill") == 0
    j.finish_run(j.start_run("distill", [1]), "ok", {})
    j.finish_run(j.start_run("distill", [2]), "distill_invalid", {})
    j.finish_run(j.start_run("distill", [3]), "distill_invalid", {})
    assert j.consecutive_invalid("distill") == 2
    j.finish_run(j.start_run("distill", [4]), "ok", {})          # lote bom zera a trava
    assert j.consecutive_invalid("distill") == 0
    j.start_run("distill", [5])                                   # lote em andamento não conta nem zera
    assert j.consecutive_invalid("distill") == 0
    j.close()

def test_consecutive_invalid_ignores_runs_before_since(tmp_path):
    """Reiniciar o container = intervenção do operador: a trava zera e o serviço tenta de novo."""
    j = Journal(tmp_path / "j3.db")
    j.finish_run(j.start_run("distill", [1]), "distill_invalid", {})
    j.finish_run(j.start_run("distill", [2]), "distill_invalid", {})
    assert j.consecutive_invalid("distill") == 2
    assert j.consecutive_invalid("distill", since="2020-01-01T00:00:00-03:00") == 2   # boot antigo: conta tudo
    assert j.consecutive_invalid("distill", since="2099-01-01T00:00:00-03:00") == 0   # boot depois: trava zerada
    j.close()
