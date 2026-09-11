#!/usr/bin/env python3
"""sync-raindrop — CLI. run|once|now|status|retry <id>|backfill|undo <run_id>"""
from __future__ import annotations
import argparse, json, os, shutil, signal, sys, time, traceback
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.config import load_config
from lib.journal import Journal
from lib.raindrop import RaindropClient
from lib.dropbox_store import DropboxStore, RcloneTokenProvider, EnvTokenProvider
from lib.records import Records
from lib.queue import pending, needs_capture, needs_distill, is_nowiki
from lib.capture import Deps as CapDeps, capture_one
from lib.batch import BatchDeps, run_batch, should_run
from lib.snapshot import Snapshot, UndoRepo
from lib.health import run_checks, write_health_files, ping_uptimerobot, status_md, claude_auth_probe
from lib.undo import undo_run
from lib.fetcher import fetch

STOP = {"flag": False}
SKILL = Path(__file__).resolve().parents[1]

def make_raindrop(cfg): return RaindropClient(cfg.raindrop_token)
def make_store(cfg, env):
    conf = Path(env.get("RCLONE_CONF", "/opt/sync-raindrop/rclone.conf"))
    prov = RcloneTokenProvider(conf) if conf.exists() else EnvTokenProvider(cfg.dropbox_refresh_token, cfg.dropbox_app_key, cfg.dropbox_app_secret)
    return DropboxStore(prov)
def make_fetch_fn(): return fetch

class App:
    def __init__(self, cfg, env):
        self.cfg, self.env = cfg, env
        self.work = Path(cfg.work_dir); self.work.mkdir(parents=True, exist_ok=True)
        self.journal = Journal(self.work / "journal.db")
        self.raindrop = make_raindrop(cfg); self.store = make_store(cfg, env); self.records = Records(self.store, cfg)
        secrets = {"groq": cfg.groq_api_key, "apify": cfg.apify_token, "jina": cfg.jina_api_key, "firecrawl": cfg.firecrawl_api_key}
        self.cap = CapDeps(cfg=cfg, raindrop=self.raindrop, store=self.store, records=self.records, journal=self.journal, fetch_fn=make_fetch_fn(), secrets=secrets)
        vocab = {l.strip().lower() for l in (SKILL / cfg.triggers_vocab_path).read_text(encoding="utf-8").replace(",", "\n").split("\n") if l.strip()}
        self.bat = BatchDeps(cfg=cfg, store=self.store, records=self.records, raindrop=self.raindrop, journal=self.journal,
                             snapshot=Snapshot(self.store, cfg), undo_repo=UndoRepo(self.work / "wiki-git"), token=cfg.claude_oauth_token,
                             prompt_template=(SKILL / "prompts/distill.md").read_text(encoding="utf-8"), schema_path=SKILL / "schema/distill.json", triggers_vocab=vocab)
        self.last_poll = None; self.auth_ok = None; self.first_pending = None; self.public_dir = Path(env.get("HEALTH_PUBLIC_DIR", "/public/sync-raindrop"))

    def now(self): return datetime.now(timezone.utc).astimezone(ZoneInfo(self.cfg.tz))

    def cycle(self, max_capture: int = 20) -> dict:
        cfg = self.cfg; out = {"captured": 0, "batches": 0}
        recs = self.records.refresh()
        items = self.raindrop.list_all()
        self.last_poll = self.now()
        q, full = pending(items, recs, cfg.queue_max)
        todo = [it for it in q if needs_capture(it, recs.get(it["id"], set()))][-max_capture:][::-1]   # mais antigos primeiro dentro do lote
        for it in todo:
            if STOP["flag"]: break
            try:
                st = capture_one(it, self.cap, self.work / "capture"); out["captured"] += st == "captured"
            except Exception as e:
                self.journal.upsert_item(it["id"], last_error=f"capture: {e}")
            shutil.rmtree(self.work / "capture" / f"r{it['id']}", ignore_errors=True)
        recs = self.records.refresh()
        ready = [iid for iid, k in recs.items() if needs_distill(k)]
        if ready and self.first_pending is None: self.first_pending = self.now()
        if not ready: self.first_pending = None
        while ready and not STOP["flag"] and should_run(ready, self.first_pending, self.now(), cfg, self.journal.runs_today("distill", cfg.tz)):
            batch = sorted(ready)[: cfg.batch_max_items]
            try:
                res = run_batch(batch, self.bat, self.work / "distill"); out["batches"] += 1
                shutil.rmtree(self.work / "distill" / res["run_id"], ignore_errors=True)
                if res.get("status") == "split":
                    for iid in batch:
                        try:
                            run_batch([iid], self.bat, self.work / "distill")
                        except Exception as e:
                            self.journal.upsert_item(iid, last_error=f"batch: {e}")
            except Exception as e:
                print(f"erro no lote {batch}: {type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}", file=sys.stderr, flush=True)
                for iid in batch: self.journal.upsert_item(iid, last_error=f"batch: {type(e).__name__}: {e}")
                break
            recs = self.records.refresh(); ready = [iid for iid, k in recs.items() if needs_distill(k)]
            self.first_pending = self.now() if ready else None
            if res.get("status") in ("distill_invalid", "distill_conflict"): break
        for req in self.journal.pop_requests():
            if req["cmd"] == "retry": self.retry(int(req["arg"]))
            elif req["cmd"] == "undo": undo_run(req["arg"], self.store, self.journal, cfg)
        self.health(recs)
        return out

    def health(self, recs):
        cfg = self.cfg
        if self.auth_ok is None or (self.now() - self.auth_ok).total_seconds() > 86400 / 2:
            ok, _ = claude_auth_probe(cfg, cfg.claude_oauth_token)
            if ok: self.auth_ok = self.now()
        free = shutil.disk_usage(self.work).free / shutil.disk_usage(self.work).total * 100
        h = run_checks(self.journal, recs, cfg, self.now(), self.last_poll, free, self.auth_ok, self.work)
        write_health_files(h, self.public_dir, self.work); ping_uptimerobot(cfg.uptimerobot_url, h)
        md = status_md(self.journal, recs, cfg, h, self.journal.count_groq_seconds_today(cfg.tz))
        (self.work / "status.md").write_text(md, encoding="utf-8")
        p = cfg.vault_path(f"{cfg.state_dir}/status.md")
        try:
            ok, rev, _ = self.store.exists(p)
            self.store.update(p, md.encode(), rev) if ok else self.store.add(p, md.encode())
        except Exception as e:
            self.journal.upsert_item(0, last_error=f"status.md: {e}")
        # catalog-index.jsonl: 1 linha por item com registro "distill" — alimenta o hook do Mac (Tarefa 24)
        try:
            lines = []
            for iid, kinds in sorted(recs.items()):
                d = self.records.latest_distill(iid)
                if not d: continue
                meta = self.records.read(iid, "meta") or {}
                cat = d.get("catalog") or {}
                lines.append(json.dumps({"id": iid, "type": cat.get("type"), "title": meta.get("title"),
                                         "triggers": cat.get("triggers"), "date": d.get("date")}, ensure_ascii=False))
            idx = "\n".join(lines) + ("\n" if lines else "")
            (self.work / "catalog-index.jsonl").write_text(idx, encoding="utf-8")
            ip = cfg.vault_path(f"{cfg.state_dir}/catalog-index.jsonl")
            ok, rev, _ = self.store.exists(ip)
            self.store.update(ip, idx.encode(), rev) if ok else self.store.add(ip, idx.encode())
        except Exception as e:
            self.journal.upsert_item(0, last_error=f"catalog-index.jsonl: {e}")

    def retry(self, iid: int):
        # item já destilado (ex.: depois de um undo): registros são imutáveis → abre geração nova (redoN) para re-destilar
        self.records.refresh(); redo = self.records.request_redo(iid)
        self.journal.upsert_item(iid, retries=0, last_error=None, state="captured")
        if redo: print(f"retry r{iid}: {redo} gravado (re-destilação)", flush=True)

    def run(self):
        if not self.journal.acquire_lock(): print("outra instância rodando"); return 2
        signal.signal(signal.SIGTERM, lambda *a: STOP.__setitem__("flag", True))
        while not STOP["flag"]:
            t0 = time.time()
            try: self.cycle()
            except Exception as e: self.journal.upsert_item(0, last_error=f"cycle: {e}"); print("erro no ciclo:", e, file=sys.stderr)
            time.sleep(max(1, self.cfg.poll_seconds - (time.time() - t0)))
        return 0

    def backfill(self, since: str | None, dry: bool, cont: bool):
        items = self.raindrop.list_all(since_iso=since)
        recs = self.records.refresh(); q, _ = pending(items, recs, 10**6)
        by_plat = {}
        for it in q: by_plat[it.get("domain", "")] = by_plat.get(it.get("domain", ""), 0) + 1
        print(f"pendentes: {len(q)}; por domínio: {json.dumps(by_plat, ensure_ascii=False)}")
        if dry: return 0
        done = 0
        for it in q:                                            # mais recentes primeiro
            if STOP["flag"]: break
            self.records.write(it["id"], "backfill", {}); capture_one(it, self.cap, self.work / "capture"); done += 1
            if done % 5 == 0:
                recs = self.records.refresh(); ready = [i for i, k in recs.items() if needs_distill(k)][: self.cfg.batch_max_items]
                if ready: run_batch(ready, self.bat, self.work / "distill")
            if done == 20 and not cont:
                print("checkpoint: 20 itens. Leia a wiki e rode `backfill --all --continue` para seguir."); return 0
        return 0

def main(argv=None, env=None) -> int:
    env = dict(os.environ if env is None else env)
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["run", "once", "now", "status", "retry", "backfill", "undo"])
    ap.add_argument("arg", nargs="?"); ap.add_argument("--config", default=str(SKILL / "config.json"))
    ap.add_argument("--all", action="store_true"); ap.add_argument("--since"); ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--continue", dest="cont", action="store_true")
    a = ap.parse_args(argv)
    cfg = load_config(Path(a.config), env); app = App(cfg, env)
    if a.cmd == "run": return app.run()
    if a.cmd == "once": print(json.dumps(app.cycle(), ensure_ascii=False)); return 0
    if a.cmd == "now": app.journal.enqueue_request("now", None); print("pedido enfileirado"); return 0
    if a.cmd == "status": print((app.work / "status.md").read_text() if (app.work / "status.md").exists() else "sem status ainda"); return 0
    if a.cmd == "retry": app.journal.enqueue_request("retry", a.arg); print("retry enfileirado"); return 0
    if a.cmd == "undo": print(json.dumps(undo_run(a.arg, app.store, app.journal, cfg), ensure_ascii=False)); return 0
    if a.cmd == "backfill": return app.backfill(None if a.all else a.since, a.dry_run, a.cont)
    return 1

if __name__ == "__main__":
    sys.exit(main())
