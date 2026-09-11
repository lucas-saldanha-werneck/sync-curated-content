"""Roteia a URL para o fetcher certo e roda isolado (processo filho, uid app-fetch, timeout)."""
from __future__ import annotations
import multiprocessing as mp, os, pwd, queue, re, signal, sys, time
from pathlib import Path
from lib.fetch_types import FetchResult

def route(url: str, rtype: str) -> str:
    u = url.lower()
    if re.search(r"(youtube\.com|youtu\.be)", u): return "youtube"
    if "tiktok.com" in u: return "tiktok"
    if "instagram.com" in u: return "instagram"
    if "linkedin.com" in u: return "linkedin"
    if re.search(r"^https?://(www\.|mobile\.)?(x|twitter)\.com/", u): return "x"
    if u.split("?")[0].endswith(".pdf") or rtype == "document": return "pdf"
    return "article"

def _drop_privileges():
    # no Mac/testes app-fetch não existe → no-op; em produção qualquer falha levanta (review Opus M2)
    from lib.privs import drop_to
    drop_to("app-fetch")

def _worker(q, fn, args, kwargs, env):
    try:
        os.setsid()                                  # líder do grupo: killpg mata netos (yt-dlp/ffmpeg) também
    except OSError:
        pass
    os.environ.clear(); os.environ.update(env); _drop_privileges()
    try:
        q.put(("ok", fn(*args, **kwargs)))
    except Exception as e:
        q.put(("err", f"{type(e).__name__}: {e}"))

def _run_isolated(fn, args, kwargs, timeout_s: int, env: dict):
    ctx = mp.get_context("fork"); q = ctx.Queue()
    p = ctx.Process(target=_worker, args=(q, fn, args, kwargs, env)); p.start()
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
    work = Path(work); work.mkdir(parents=True, exist_ok=True)
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "TZ": cfg.tz, "HOME": str(work)}
    if os.environ.get("YTDLP_PROXY"): env["YTDLP_PROXY"] = os.environ["YTDLP_PROXY"]
    if os.environ.get("YTDLP_POT_URL"): env["YTDLP_POT_URL"] = os.environ["YTDLP_POT_URL"]
    used = journal.count_groq_seconds_today(cfg.tz) if journal else 0
    groq = secrets.get("groq") or ""
    if plat in ("youtube", "tiktok"):
        call = (f["video"], (item["link"], work, cfg, groq, plat), {"groq_seconds_used_today": used})
    elif plat == "instagram":
        call = (f["instagram"], (item["link"], work, cfg, secrets.get("apify"), groq), {})
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
        if isolate:
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
    return res
