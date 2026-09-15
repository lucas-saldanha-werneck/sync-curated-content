"""Lote de destilação: snapshot → claude (só leitura) → aplicador → CAS → registros → tags."""
from __future__ import annotations
import os
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from lib.applier import LimitError, apply, plan_ops
from lib.distill import DistillError, build_workspace, render_prompt, run_claude as _run_claude
from lib.dropbox_store import ConflictError
from lib.queue import is_nowiki
from lib.records import capture_kind, gen_kind
from lib.snapshot import Snap


@dataclass
class BatchDeps:
    cfg: object; store: object; records: object; raindrop: object; journal: object; snapshot: object; undo_repo: object
    token: str = ""; prompt_template: str = ""; schema_path: Path = Path("schema/distill.json")
    triggers_vocab: set = field(default_factory=set)
    run_claude: object = _run_claude
    now: object = datetime.now


BREAKER_COOLDOWN_MIN = 30  # a trava desliza: falha transitória (cota 429, rede) volta sozinha, sem restart
BREAKER_INVALID = 3        # lotes distill_invalid seguidos (nenhum item progride) que travam a destilação
NIGHT_WINDOW_FACTOR = 2    # a janela das 3h drena o backlog (DESIGN §79), mas com teto próprio


def breaker_since(boot: str, now: datetime) -> str:
    """Início da janela que a trava enxerga: o boot do processo (restart = intervenção do operador)
    ou os últimos BREAKER_COOLDOWN_MIN minutos, o que for mais recente. Sem isso uma falha transitória
    deixaria a destilação parada até alguém reiniciar o container (bug 2026-09-12, cota 429)."""
    desliza = (now - timedelta(minutes=BREAKER_COOLDOWN_MIN)).isoformat(timespec="seconds")
    return max(boot, desliza)


def should_run(pending: list, first_pending_ts, now: datetime, cfg, runs_today: int, consecutive_invalid: int = 0) -> bool:
    if not pending:
        return False
    # trava: lote que morre antes de produzir saída válida (claude rc!=0, workspace sem trust, token vencido)
    # repetia para sempre — na janela das 3h, sem teto nenhum (85 chamadas queimadas em 2026-09-12)
    if consecutive_invalid >= BREAKER_INVALID:
        return False
    local = now.astimezone(ZoneInfo(cfg.tz)) if now.tzinfo else now
    cap = cfg.max_batches_per_day * (NIGHT_WINDOW_FACTOR if local.hour == 3 else 1)
    if runs_today >= cap:
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


def _retry_or_escalate(deps, cfg, res, iid, error_msg):
    n = int((deps.journal.get_item(iid) or {}).get("retries") or 0) + 1
    deps.journal.upsert_item(iid, retries=n, last_error=error_msg)
    if n >= cfg.retry_max:
        deps.records.write(iid, gen_kind("erro", deps.records.kinds(iid)), {"error": error_msg}); _tag(deps, iid, ["sr:erro"], ["sr:raw"])
        res["items"][iid] = "erro"
    else:
        res["items"][iid] = "retry"


def run_batch(item_ids: list[int], deps: BatchDeps, work: Path) -> dict:
    run_id = deps.journal.start_run("distill", item_ids)
    try:
        return _run_batch(item_ids, deps, work, run_id)
    except Exception as e:
        # nunca deixar run "running" órfão: fecha como aborted com o traceback (o chamador só guarda str(e))
        deps.journal.finish_run(run_id, "aborted", {"run_id": run_id, "error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()[-2000:]})
        raise


def _run_batch(item_ids: list[int], deps: BatchDeps, work: Path, run_id: str) -> dict:
    cfg = deps.cfg
    now = deps.now()
    today = now.astimezone(ZoneInfo(cfg.tz)).strftime("%Y-%m-%d") if now.tzinfo else now.strftime("%Y-%m-%d")
    res = {"run_id": run_id, "items": {}, "ops": 0, "warnings": [], "status": "ok"}
    items: dict[int, dict] = {}
    for iid in item_ids:
        fresh = deps.raindrop.get(iid)
        if fresh and is_nowiki(fresh):
            deps.records.write(iid, "nowiki", {}); res["items"][iid] = "nowiki"; _tag(deps, iid, [], ["sr:raw"]); continue
        # geração atual: depois de uma recaptura o raw bom é o `capturedN`, não o de geração 0
        ck = capture_kind(deps.records.kinds(iid))
        cap = deps.records.read(iid, ck) if ck else None; meta = deps.records.read(iid, "meta") or {}
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
            iid = next(iter(items)); deps.records.write(iid, gen_kind("erro", deps.records.kinds(iid)), {"error": f"limite: {e}"}); _tag(deps, iid, ["sr:erro"], ["sr:raw"])
            res["items"][iid] = "erro"; deps.journal.finish_run(run_id, "erro", res); return res
        # revalida nowiki ANTES do push: item que virou nowiki sai do lote inteiro (catálogo/log/página) e o plano é refeito
        # — ops compartilhadas carregam todos os ids, editar o manifesto "cirurgicamente" deixava linha no catálogo e no log (review Opus M4)
        late = [iid for iid in list(items) if (fresh := deps.raindrop.get(iid)) and is_nowiki(fresh)]
        if late:
            for iid in late:
                deps.records.write(iid, "nowiki", {}); res["items"][iid] = "nowiki"; items.pop(iid, None); _tag(deps, iid, [], ["sr:raw"])
            output = {**output, "items": [it for it in output.get("items", []) if str(it.get("raindrop_id")) not in {str(i) for i in late}]}
            manifest = None
            if not items:
                deps.journal.finish_run(run_id, "empty", res); return res
            continue
        try:
            apply(manifest, deps.store, deps.journal, cfg); break
        except ConflictError as e:
            res["warnings"].append(f"conflito CAS (tentativa {attempt + 1}): {e}")
            snap = deps.snapshot.pull(); deps.undo_repo.commit_snapshot(snap, f"re-pull {run_id}"); manifest = None
    if manifest is None:
        res["status"] = "distill_conflict"; deps.journal.finish_run(run_id, "distill_conflict", res); return res
    merged = {**snap.files, **{op.path: (op.after, op.rev_after) for op in manifest.ops}}
    deps.undo_repo.commit_snapshot(Snap(merged), f"push {run_id}")
    final_content = {rel: data for rel, (data, _rev) in merged.items()}
    res["ops"] = len(manifest.ops); res["warnings"] += manifest.warnings
    log_rel = cfg.log_curated_fmt.format(ym=today[:7])
    for iid, pi in manifest.per_item.items():
        info = items[iid]
        if pi["decision"] == "retry":
            _retry_or_escalate(deps, cfg, res, iid, pi.get("error")); continue
        # conclusão por CONTEÚDO, não só pelas ops desta rodada: um item pode já estar
        # inteiramente refletido no vault (marcador/linha já presentes) sem gerar op nova
        # nesta rodada (render idêntico / inserção idempotente) — sem isso o item ficaria
        # "incompleto" para sempre, preso em "captured".
        done = set(pi["done"])
        marker_txt = f"curated:r{iid} "
        for rel, data in final_content.items():
            if (rel.startswith("wiki/") and rel.endswith(".md") and rel != cfg.catalog_page
                    and not rel.startswith("wiki/log/") and marker_txt in data.decode("utf-8", "ignore")):
                done.add("claims"); break
        if f"<!-- r{iid} -->" in final_content.get(cfg.catalog_page, b"").decode("utf-8", "ignore"):
            done.add("catalog")
        if f" r{iid} " in final_content.get(log_rel, b"").decode("utf-8", "ignore"):
            done.add("log")
        pi["done"] = sorted(done)
        missing = set(pi["required"]) - done
        if missing:
            res["warnings"].append(f"r{iid}: faltou {missing}")
            _retry_or_escalate(deps, cfg, res, iid, f"incompleto: faltou {sorted(missing)}")
            continue
        kinds = deps.records.kinds(iid)
        deps.records.write(iid, gen_kind("distill", kinds), {"decision": pi["decision"], "pages": pi["pages"], "catalog": pi["catalog"], "raw": info["raw"], "date": today, "run_id": run_id})
        final = {"wiki": "wiki", "parcial": "parcial", "skip": "skip"}[pi["decision"]]
        deps.records.write(iid, gen_kind(final, kinds), {"pages": pi["pages"], "run_id": run_id, "reason": pi.get("reason")})
        deps.journal.upsert_item(iid, state=final)
        note = ("→ wiki: " + ", ".join(f"[[{p[5:-3]}]]" for p in pi["pages"])) if pi["pages"] else (f"sr:{final}: {pi.get('reason','')}" if final != "wiki" else None)
        _tag(deps, iid, [f"sr:{final}"], ["sr:raw", "sr:retry"], note)
        res["items"][iid] = final
    deps.journal.finish_run(run_id, "ok", {k: v for k, v in res.items() if k != "items"} | {"items": {str(k): v for k, v in res["items"].items()}})
    return res
