"""Lote de destilação: snapshot → claude (só leitura) → aplicador → CAS → registros → tags."""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from lib.applier import LimitError, apply, plan_ops
from lib.distill import DistillError, build_workspace, render_prompt, run_claude as _run_claude
from lib.dropbox_store import ConflictError
from lib.queue import is_nowiki
from lib.snapshot import Snap


@dataclass
class BatchDeps:
    cfg: object; store: object; records: object; raindrop: object; journal: object; snapshot: object; undo_repo: object
    token: str = ""; prompt_template: str = ""; schema_path: Path = Path("schema/distill.json")
    triggers_vocab: set = field(default_factory=set)
    run_claude: object = _run_claude
    now: object = datetime.now


def should_run(pending: list, first_pending_ts, now: datetime, cfg, runs_today: int) -> bool:
    if not pending:
        return False
    local = now.astimezone(ZoneInfo(cfg.tz)) if now.tzinfo else now
    if runs_today >= cfg.max_batches_per_day and not (local.hour == 3):
        return False
    if len(pending) >= cfg.batch_min_items:
        return True
    return first_pending_ts is not None and (now - first_pending_ts).total_seconds() >= cfg.batch_max_wait_min * 60


def _tag(deps, iid, add, remove, note=None):
    if os.environ.get("SR_DRY_TAGS"):
        return
    try:
        deps.raindrop.merge_tags(iid, add, remove, note)
    except Exception as e:
        deps.journal.upsert_item(iid, last_error=f"tag: {e}")


def run_batch(item_ids: list[int], deps: BatchDeps, work: Path) -> dict:
    cfg = deps.cfg
    now = deps.now()
    today = now.astimezone(ZoneInfo(cfg.tz)).strftime("%Y-%m-%d") if now.tzinfo else now.strftime("%Y-%m-%d")
    run_id = deps.journal.start_run("distill", item_ids)
    res = {"run_id": run_id, "items": {}, "ops": 0, "warnings": [], "status": "ok"}
    items: dict[int, dict] = {}
    for iid in item_ids:
        fresh = deps.raindrop.get(iid)
        if fresh and is_nowiki(fresh):
            deps.records.write(iid, "nowiki", {}); res["items"][iid] = "nowiki"; _tag(deps, iid, [], ["sr:raw"]); continue
        cap = deps.records.read(iid, "captured"); meta = deps.records.read(iid, "meta") or {}
        if not cap:
            res["items"][iid] = "sem_captured"; continue
        items[iid] = {"raindrop_id": iid, "raw": cap["raw"], "frames": cap.get("frames", []), "link": meta.get("link"), "title": meta.get("title"),
                      "note": (meta.get("raindrop") or {}).get("note", ""), "tags": meta.get("tags", []), "method": cap.get("method")}
    if not items:
        deps.journal.finish_run(run_id, "empty", res); return res
    snap = deps.snapshot.pull(); deps.undo_repo.commit_snapshot(snap, f"pull {run_id}")
    raws = {}; frames = {}; nimg = 0
    for iid, info in items.items():
        data, _ = deps.store.download(cfg.vault_path(info["raw"])); raws[info["raw"]] = data
        lst = []
        for name in info["frames"]:
            if nimg >= cfg.max_images_per_batch:
                break
            try:
                blob, _ = deps.store.download(cfg.vault_path(f"{info['raw'][:-3]}/frames/{name}")); lst.append((name, blob)); nimg += 1
            except FileNotFoundError:
                pass
        if lst:
            frames[info["raw"][:-3]] = lst
    ws = build_workspace(Path(work) / run_id, snap, raws, frames)
    pages = sorted(rel for rel in snap.files if rel.startswith("wiki/") and rel.endswith(".md") and not rel.startswith("wiki/log/"))
    prompt = render_prompt(deps.prompt_template, [{k: v for k, v in i.items() if k in ("raindrop_id", "raw", "note", "title", "tags", "method")} for i in items.values()],
                           pages, sorted(deps.triggers_vocab), cfg.types_vocab)
    try:
        output = deps.run_claude(ws, cfg, deps.token, prompt, deps.schema_path)
    except DistillError as e:
        res["status"] = "distill_invalid"; res["warnings"].append(str(e)); deps.journal.finish_run(run_id, "distill_invalid", res); return res
    deps.records.refresh()
    existing = deps.records.catalog_entries()
    manifest = None
    for attempt in range(3):
        try:
            manifest = plan_ops(output, snap, items, cfg, run_id, today, deps.triggers_vocab, existing_catalog=existing)
        except LimitError as e:
            if len(items) > 1:
                res["status"] = "split"; res["warnings"].append(str(e)); deps.journal.finish_run(run_id, "split", res); return res
            iid = next(iter(items)); deps.records.write(iid, "erro", {"error": f"limite: {e}"}); _tag(deps, iid, ["sr:erro"], ["sr:raw"])
            res["items"][iid] = "erro"; deps.journal.finish_run(run_id, "erro", res); return res
        for iid in list(items):
            fresh = deps.raindrop.get(iid)
            if fresh and is_nowiki(fresh):
                deps.records.write(iid, "nowiki", {}); res["items"][iid] = "nowiki"; manifest.per_item.pop(iid, None)
                manifest.ops = [op for op in manifest.ops if not (op.item_ids == [iid])]
        try:
            apply(manifest, deps.store, deps.journal, cfg); break
        except ConflictError as e:
            res["warnings"].append(f"conflito CAS (tentativa {attempt + 1}): {e}")
            snap = deps.snapshot.pull(); deps.undo_repo.commit_snapshot(snap, f"re-pull {run_id}"); manifest = None
    if manifest is None:
        res["status"] = "distill_conflict"; deps.journal.finish_run(run_id, "distill_conflict", res); return res
    after = Snap({op.path: (op.after, op.rev_after) for op in manifest.ops})
    deps.undo_repo.commit_snapshot(Snap({**snap.files, **after.files}), f"push {run_id}")
    res["ops"] = len(manifest.ops); res["warnings"] += manifest.warnings
    for iid, pi in manifest.per_item.items():
        info = items[iid]
        if pi["decision"] == "retry":
            n = int((deps.journal.get_item(iid) or {}).get("retries") or 0) + 1
            deps.journal.upsert_item(iid, retries=n, last_error=pi.get("error"))
            if n >= cfg.retry_max:
                deps.records.write(iid, "erro", {"error": pi.get("error")}); _tag(deps, iid, ["sr:erro"], ["sr:raw"]); res["items"][iid] = "erro"
            else:
                res["items"][iid] = "retry"
            continue
        complete = set(pi["required"]) <= set(pi["done"])
        if not complete:
            res["items"][iid] = "incompleto"; res["warnings"].append(f"r{iid}: faltou {set(pi['required']) - set(pi['done'])}"); continue
        deps.records.write(iid, "distill", {"decision": pi["decision"], "pages": pi["pages"], "catalog": pi["catalog"], "raw": info["raw"], "date": today, "run_id": run_id})
        final = {"wiki": "wiki", "parcial": "parcial", "skip": "skip"}[pi["decision"]]
        deps.records.write(iid, final, {"pages": pi["pages"], "run_id": run_id, "reason": pi.get("reason")})
        deps.journal.upsert_item(iid, state=final)
        note = ("→ wiki: " + ", ".join(f"[[{p[5:-3]}]]" for p in pi["pages"])) if pi["pages"] else (f"sr:{final}: {pi.get('reason','')}" if final != "wiki" else None)
        _tag(deps, iid, [f"sr:{final}"], ["sr:raw", "sr:retry"], note)
        res["items"][iid] = final
    deps.journal.finish_run(run_id, "ok", {k: v for k, v in res.items() if k != "items"} | {"items": {str(k): v for k, v in res["items"].items()}})
    return res
