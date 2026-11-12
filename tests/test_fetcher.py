import functools

import pytest

from lib.fetcher import route, fetch
from lib.config import Config
from lib.fetch_types import FetchResult
from tests import iso_helpers


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch):
    """A guarda anti-SSRF antes do yt-dlp resolve DNS; aqui as URLs são de mentira."""
    monkeypatch.setattr("lib.fetcher.check_url", lambda url: ("h", "1.1.1.1"))


def test_route():
    assert route("https://youtu.be/x", "video") == "youtube"
    assert route("https://www.youtube.com/shorts/x", "link") == "youtube"
    assert route("https://vm.tiktok.com/ZM/", "link") == "tiktok"
    assert route("https://www.instagram.com/reel/a/", "link") == "instagram"
    assert route("https://www.linkedin.com/posts/x", "link") == "linkedin"
    assert route("https://x.com/a/status/1", "link") == "x"
    assert route("https://site.com/f.pdf", "link") == "pdf" and route("https://site.com/f", "document") == "pdf"
    assert route("https://blog.com/a", "article") == "article"


def test_fetch_dispatch_in_process(tmp_path):
    calls = []
    fns = {"article": lambda url, cfg, jk, fk, **k: (calls.append(url) or FetchResult(platform="article", text="t" * 500, method="trafilatura"))}
    item = {"id": 1, "link": "https://blog.com/a", "type": "article", "title": "", "tags": []}
    r = fetch(item, tmp_path, Config(), {"jina": None, "firecrawl": None}, journal=None, fns=fns, isolate=False)
    assert r.method == "trafilatura" and calls == ["https://blog.com/a"]


def test_fetch_timeout(tmp_path):
    fns = {"article": iso_helpers.sleepy_article}
    item = {"id": 1, "link": "https://blog.com/a", "type": "article", "title": "", "tags": []}
    r = fetch(item, tmp_path, Config(fetch_timeout_s=1), {}, journal=None, fns=fns, isolate=True)
    assert r.method == "none" and "timeout" in r.error


def test_large_result_not_misreported_as_timeout(tmp_path):
    """Fix round 1 (crítico): q.get() precisa vir ANTES do p.join(), senão um resultado
    grande (frames JPEG) enche o pipe e o filho fica travado no put() até o pai drenar —
    e o pai só chegava a drenar depois de esgotar o timeout inteiro, reportando timeout
    num fetch que na verdade tinha terminado."""
    import time
    fns = {"article": iso_helpers.big_article}
    item = {"id": 1, "link": "https://blog.com/a", "type": "article", "title": "", "tags": []}
    t0 = time.monotonic()
    r = fetch(item, tmp_path, Config(fetch_timeout_s=5), {}, journal=None, fns=fns, isolate=True)
    elapsed = time.monotonic() - t0
    assert r.method == "trafilatura"
    assert elapsed < 3, f"deveria voltar quase na hora, levou {elapsed:.1f}s (ficou preso até o timeout)"


def test_timeout_kills_process_group(tmp_path):
    """Fix round 1 (crítico): no timeout, o worker precisa ser líder de grupo (os.setsid())
    e o pai precisa matar o GRUPO (os.killpg) — senão netos como yt-dlp/ffmpeg ficam
    órfãos rodando depois do timeout."""
    import os as _os
    import subprocess
    import time
    pidfile = tmp_path / "sleeper.pid"

    fns = {"video": functools.partial(iso_helpers.sleeper_video, str(pidfile), 10)}
    item = {"id": 1, "link": "https://youtu.be/x", "type": "video", "title": "", "tags": []}
    r = fetch(item, tmp_path, Config(fetch_timeout_s=1), {}, journal=None, fns=fns, isolate=True)
    assert r.method == "none" and "timeout" in r.error

    time.sleep(0.5)  # dá tempo do SIGKILL propagar pro grupo inteiro
    sleeper_pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        _os.kill(sleeper_pid, 0)  # processo morto: sinal 0 levanta ProcessLookupError


def test_x_video_groq_is_journaled(tmp_path):
    """Fix round 1 (importante): quando o X embute vídeo, os segundos de Groq gastos na
    transcrição do vídeo precisam ir pro journal (res.method continua "fxtwitter", só o
    sub-fetch de vídeo é "groq")."""
    from lib.journal import Journal
    fns = {
        "x": lambda url, cfg, **k: FetchResult(platform="x", method="fxtwitter", text="post",
                                                extra={"video_url": "https://v"}),
        "video": lambda url, work, cfg, groq_key, platform, **k: FetchResult(
            platform="x", method="groq", text="fala", duration_s=42),
    }
    item = {"id": 1, "link": "https://x.com/a/status/1", "type": "link", "title": "", "tags": []}
    j = Journal(tmp_path / "j.db")
    cfg = Config()
    r = fetch(item, tmp_path, cfg, {"groq": "gk"}, journal=j, fns=fns, isolate=False)
    assert j.count_groq_seconds_today(cfg.tz) == 42
    j.close()
    assert r.method == "fxtwitter" and "fala" in r.text and r.extra.get("video_method") == "groq"


def test_child_crash_fails_fast(tmp_path):
    """Fix round 2: um filho que morre ANTES de dar q.put() (os._exit, segfault, OOM) não pode
    prender o pai até o fim do timeout inteiro — precisa falhar rápido."""
    import os as _os
    import time

    fns = {"article": iso_helpers.crash_article}
    item = {"id": 1, "link": "https://blog.com/a", "type": "article", "title": "", "tags": []}
    t0 = time.monotonic()
    r = fetch(item, tmp_path, Config(fetch_timeout_s=5), {}, journal=None, fns=fns, isolate=True)
    elapsed = time.monotonic() - t0
    assert r.method == "none" and "morreu" in r.error
    assert elapsed < 2, f"deveria falhar rápido, levou {elapsed:.1f}s"


def test_isolate_false_never_raises(tmp_path):
    """Fix round 1 (importante): com isolate=False uma exceção do fetcher não pode
    propagar — vira FetchResult(method="none", error=...)."""
    def boom(url, cfg, jk, fk, **k):
        raise ValueError("boom")
    fns = {"article": boom}
    item = {"id": 1, "link": "https://blog.com/a", "type": "article", "title": "", "tags": []}
    r = fetch(item, tmp_path, Config(), {}, journal=None, fns=fns, isolate=False)
    assert r.method == "none" and "boom" in r.error


def test_fetch_entrega_o_work_dir_ao_app_fetch_pelo_grupo(tmp_path, monkeypatch):
    """O yt-dlp baixa legenda/vídeo PARA DISCO rodando como app-fetch, mas quem cria a pasta
    é o orquestrador (app). Sem entregar a pasta, o download morre em 'Permission denied'
    e o item vira `parcial` sem transcrição (bug achado 2026-09-14)."""
    import os, stat
    from lib import fetcher
    gid = os.getgid()
    monkeypatch.setattr(fetcher, "_app_fetch_pw", lambda: type("PW", (), {"pw_uid": 99999, "pw_gid": gid})())
    work = tmp_path / "r1"
    fetch({"id": 1, "link": "https://blog.com/x", "type": "article"}, work, Config(), {}, None,
          fns={"article": lambda *a, **k: FetchResult(platform="article", text="t", method="trafilatura")}, isolate=False)
    st = work.stat()
    assert st.st_gid == gid
    assert st.st_mode & 0o777 == 0o770 and st.st_mode & stat.S_ISGID


def test_fetch_work_dir_sem_app_fetch_fica_privado(tmp_path, monkeypatch):
    """Mac/teste sem o usuário app-fetch: sem sandbox, a pasta fica só do dono."""
    from lib import fetcher
    monkeypatch.setattr(fetcher, "_app_fetch_pw", lambda: None)
    work = tmp_path / "r2"
    fetch({"id": 2, "link": "https://blog.com/y", "type": "article"}, work, Config(), {}, None,
          fns={"article": lambda *a, **k: FetchResult(platform="article", text="t", method="trafilatura")}, isolate=False)
    assert work.stat().st_mode & 0o777 == 0o700


def test_child_kills_itself_when_parent_cannot(tmp_path, monkeypatch):
    """Produção: o pai roda como `app` sem CAP_KILL e o filho já caiu para `app-fetch`, então o
    killpg/p.kill() do pai levam EPERM. O próprio filho precisa se matar (com os netos) no prazo."""
    import multiprocessing.process as mpp
    import os as _os
    import subprocess
    import time
    from lib import fetcher
    pidfile = tmp_path / "sleeper.pid"
    parent = _os.getpid()
    real_killpg = _os.killpg

    def killpg_eperm_in_parent(pgid, sig):
        if _os.getpid() == parent:
            raise PermissionError(1, "Operation not permitted")
        return real_killpg(pgid, sig)

    monkeypatch.setattr(fetcher.os, "killpg", killpg_eperm_in_parent)
    monkeypatch.setattr(mpp.BaseProcess, "kill", lambda self: None)     # p.kill() também "falha"

    fake_video_fetch = functools.partial(iso_helpers.sleeper_video, str(pidfile), 0)   # yt-dlp/ffmpeg travado

    item = {"id": 1, "link": "https://youtu.be/x", "type": "video", "title": "", "tags": []}
    r = fetch(item, tmp_path, Config(fetch_timeout_s=1), {}, journal=None, fns={"video": fake_video_fetch}, isolate=True)
    assert r.method == "none" and "timeout" in r.error
    sleeper_pid = int(pidfile.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            _os.kill(sleeper_pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.2)
    _os.kill(sleeper_pid, 9)
    pytest.fail("neto travado continuou vivo: o filho não se limitou sozinho")
