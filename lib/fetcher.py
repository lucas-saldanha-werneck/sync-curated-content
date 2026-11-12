"""Roteia a URL para o fetcher certo e roda isolado (processo filho, uid app-fetch, timeout)."""
from __future__ import annotations
import dataclasses, multiprocessing as mp, os, pwd, queue, re, signal, sys, time, urllib.parse
from pathlib import Path
from lib.fetch_types import FetchResult
from lib.net_guard import check_url

# o worker isolado recebe uma cópia do cfg SEM estes campos (cada fetcher recebe só a chave de que precisa, por argumento)
SECRET_FIELDS = ("raindrop_token", "dropbox_refresh_token", "dropbox_app_key", "dropbox_app_secret", "groq_api_key",
                 "claude_oauth_token", "apify_token", "jina_api_key", "firecrawl_api_key", "uptimerobot_url",
                 "claude_credit_api_key")


def _host_is(host: str, *domains: str) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def route(url: str, rtype: str) -> str:
    """Roteia pelo HOSTNAME (não pelo texto da URL inteira): `https://evil/?q=youtube.com` não vai ao yt-dlp."""
    u = url.strip()
    try:
        host = (urllib.parse.urlsplit(u).hostname or "").lower().rstrip(".")
    except ValueError:
        host = ""
    if _host_is(host, "youtube.com", "youtu.be", "youtube-nocookie.com"): return "youtube"
    if _host_is(host, "tiktok.com"): return "tiktok"
    if _host_is(host, "instagram.com"): return "instagram"
    if _host_is(host, "linkedin.com"): return "linkedin"
    if host in ("x.com", "www.x.com", "mobile.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"): return "x"
    if u.lower().split("?")[0].endswith(".pdf") or rtype == "document": return "pdf"
    return "article"


def public_cfg(cfg):
    """Cópia do cfg sem segredos, para o processo isolado (que faz parse de conteúdo hostil)."""
    if not dataclasses.is_dataclass(cfg):
        return cfg
    blank = {f.name: (None if f.default is None else "") for f in dataclasses.fields(cfg) if f.name in SECRET_FIELDS}
    return dataclasses.replace(cfg, **blank)

def _app_fetch_pw():
    try:
        return pwd.getpwnam("app-fetch")
    except KeyError:
        return None


def _hand_work_dir_to_app_fetch(work: Path) -> None:
    """O fetcher (yt-dlp, ffmpeg) grava NA PASTA e roda como app-fetch, mas quem cria a pasta é o
    orquestrador (app), que não tem CAP_CHOWN. Entrega pelo GRUPO (chgrp não precisa de capability):
    2770 + setgid, para o que o filho criar continuar do grupo e o app conseguir limpar depois.
    Sem isso o download morre em 'Permission denied' e o item vira parcial (bug 2026-09-14)."""
    pw = _app_fetch_pw()
    if pw is None:
        os.chmod(work, 0o700)           # Mac/testes: sem sandbox de uid, pasta só do dono
        return
    try:
        os.chown(work, -1, pw.pw_gid); os.chmod(work, 0o2770)
    except PermissionError as e:
        raise RuntimeError(f"work dir não pôde ser entregue ao grupo app-fetch (app é membro do grupo?): {e}") from e


def _drop_privileges():
    # no Mac/testes app-fetch não existe → no-op; em produção qualquer falha levanta (review Opus M2)
    from lib.privs import drop_to
    drop_to("app-fetch")
    os.umask(0o007)                     # o que o fetcher criar fica gravável pelo grupo → app limpa depois

SELF_KILL_GRACE_S = 1


def _arm_self_kill(timeout_s: int) -> None:
    """O filho se limita sozinho: em produção o pai (`app`, sem CAP_KILL) não consegue matar um filho que já
    caiu para `app-fetch` (killpg/kill dão EPERM). Ao fim do prazo o próprio worker manda SIGKILL ao SEU grupo
    (ele + netos yt-dlp/ffmpeg, todos app-fetch). O alarme sobrevive à troca de uid. Só mata o grupo se for o
    líder (setsid deu certo); senão mata só a si mesmo, para nunca atingir o grupo do pai."""
    def _on_alarm(_sig, _frm):
        try:
            if os.getpgrp() == os.getpid():
                os.killpg(os.getpid(), signal.SIGKILL)
        finally:
            os.kill(os.getpid(), signal.SIGKILL)
    signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(max(1, int(timeout_s)) + SELF_KILL_GRACE_S)


def _worker(q, fn, args, kwargs, env, timeout_s=None):
    try:
        os.setsid()                                  # líder do grupo: killpg mata netos (yt-dlp/ffmpeg) também
    except OSError:
        pass
    if timeout_s:
        _arm_self_kill(timeout_s)                    # antes da queda de privilégio
    os.environ.clear(); os.environ.update(env); _drop_privileges()
    try:
        q.put(("ok", fn(*args, **kwargs)))
    except Exception as e:
        q.put(("err", f"{type(e).__name__}: {e}"))

def _run_isolated(fn, args, kwargs, timeout_s: int, env: dict):
    # "spawn", não "fork": o filho é um interpretador novo e não herda a memória do orquestrador (tokens em
    # objetos Python). Só recebe o que vai em `args` (cfg sem segredos + a chave daquele fetcher). Exige `fn`
    # e argumentos serializáveis (funções de módulo, não lambdas).
    ctx = mp.get_context("spawn"); q = ctx.Queue()
    p = ctx.Process(target=_worker, args=(q, fn, args, kwargs, env, timeout_s)); p.start()
    try:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                status, val = q.get(timeout=0.5)      # poll: nunca fica preso no timeout inteiro
                break
            except queue.Empty:
                if not p.is_alive():
                    # filho morreu (os._exit/segfault/OOM) antes de dar put() — falha rápido,
                    # mas confere uma última vez pra evitar corrida com o put() em andamento
                    try:
                        status, val = q.get_nowait()
                        break
                    except queue.Empty:
                        p.join(5)
                        return f"erro: processo morreu sem resultado (exit {p.exitcode})"
                if time.monotonic() > deadline:
                    try:
                        os.killpg(p.pid, signal.SIGKILL)   # mata o grupo inteiro (worker + netos yt-dlp/ffmpeg)
                    except (ProcessLookupError, PermissionError, OSError):
                        p.kill()
                    p.join(5)
                    if p.is_alive():
                        print(f"fetcher: aviso — processo filho pid={p.pid} sobreviveu ao SIGKILL", file=sys.stderr)
                    return "timeout"
        p.join(5)
        if p.is_alive():
            p.kill(); p.join(5)
        return val if status == "ok" else f"erro: {val}"
    finally:
        try:
            q.close(); q.join_thread()
        except Exception:
            pass

def fetch(item: dict, work: Path, cfg, secrets: dict, journal, fns: dict | None = None, isolate: bool = True) -> FetchResult:
    from lib.fetch_video import fetch_video
    from lib.fetch_article import fetch_article, fetch_pdf
    from lib.fetch_social import fetch_instagram, fetch_linkedin, fetch_x
    f = {"video": fetch_video, "article": fetch_article, "pdf": fetch_pdf, "instagram": fetch_instagram,
         "linkedin": fetch_linkedin, "x": fetch_x, **(fns or {})}
    plat = route(item["link"], item.get("type", "link"))
    if plat in ("youtube", "tiktok"):
        # o yt-dlp não passa pelo safe_open: mesma guarda anti-SSRF antes de entregar a URL a ele
        try:
            check_url(item["link"])
        except Exception as e:
            return FetchResult(platform=plat, method="none", error=f"url bloqueada: {e}")
    cfg = public_cfg(cfg)
    work = Path(work); work.mkdir(parents=True, exist_ok=True); _hand_work_dir_to_app_fetch(work)
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "TZ": cfg.tz, "HOME": str(work)}
    if os.environ.get("YTDLP_PROXY"): env["YTDLP_PROXY"] = os.environ["YTDLP_PROXY"]
    if os.environ.get("YTDLP_POT_URL"): env["YTDLP_POT_URL"] = os.environ["YTDLP_POT_URL"]
    used = journal.count_groq_seconds_today(cfg.tz) if journal else 0
    groq = secrets.get("groq") or ""
    if plat in ("youtube", "tiktok"):
        call = (f["video"], (item["link"], work, cfg, groq, plat), {"groq_seconds_used_today": used, "apify_token": secrets.get("apify")})
    elif plat == "instagram":
        call = (f["instagram"], (item["link"], work, cfg, secrets.get("apify"), groq), {"groq_seconds_used_today": used})
    elif plat == "linkedin":
        call = (f["linkedin"], (item["link"], cfg, secrets.get("apify")), {})
    elif plat == "x":
        call = (f["x"], (item["link"], cfg), {})
    elif plat == "pdf":
        call = (f["pdf"], (item["link"], work, cfg), {})
    else:
        call = (f["article"], (item["link"], cfg, secrets.get("jina"), secrets.get("firecrawl")), {})
    fn, args, kw = call
    if isolate:
        res = _run_isolated(fn, args, kw, cfg.fetch_timeout_s, env)
    else:
        try:
            res = fn(*args, **kw)
        except Exception as e:
            res = f"{type(e).__name__}: {e}"
    if isinstance(res, str) or res is None:
        return FetchResult(platform=plat, method="none", error=str(res))
    if plat == "x" and res.method != "none" and res.extra.get("video_url"):
        vargs = (res.extra["video_url"], work / "xvid", cfg, groq, "x")
        try:
            check_url(res.extra["video_url"])
            vblocked = None
        except Exception as e:
            vblocked = f"url bloqueada: {e}"
        if vblocked:
            v = vblocked
        elif isolate:
            v = _run_isolated(f["video"], vargs, {"groq_seconds_used_today": used}, cfg.fetch_timeout_s, env)
        else:
            try:
                v = f["video"](*vargs, groq_seconds_used_today=used)
            except Exception as e:
                v = f"{type(e).__name__}: {e}"
        if not isinstance(v, str) and v is not None:
            if v.text:
                res.text += "\n\nTranscrição do vídeo: " + v.text; res.frames = v.frames; res.duration_s = v.duration_s
                res.extra["video_method"] = v.method
            if journal and v.method == "groq" and v.duration_s:
                journal.add_groq_seconds(int(v.duration_s))
    if journal and res.method == "groq" and res.duration_s:
        journal.add_groq_seconds(int(res.duration_s))
    # Instagram: o resultado é "apify", mas a transcrição do vídeo pode ter gasto Groq — entra no teto diário
    if journal and plat == "instagram" and res.extra.get("video_method") == "groq" and res.duration_s:
        journal.add_groq_seconds(int(res.duration_s))
    return res
