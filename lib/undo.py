"""Undo por marcador: remove só o que o lote inseriu, com CAS no rev pós-push."""
from __future__ import annotations
import re

def strip_markers(text: str, item_ids: list[int]) -> str:
    for iid in item_ids:
        text = re.sub(rf"<!-- curated:r{iid} [^>]*-->\n[^\n]*\n?", "", text)
        text = re.sub(rf"~~(.*?)~~ \(superado em [0-9-]+, ver r{iid}: [^)]*\)", r"\1", text)
    return text

def undo_run(run_id: str, store, journal, cfg) -> dict:
    res = {"reverted": [], "skipped": []}
    urun = journal.start_run("undo", [])
    for op in journal.ops(run_id):
        if not op.get("rev_after"): continue
        full = cfg.vault_path(op["path"])
        ok, cur_rev, _ = store.exists(full)
        if not ok or cur_rev != op["rev_after"]:
            res["skipped"].append(op["path"]); continue
        data, _ = store.download(full)
        ids = [r["item_id"] for r in journal.ops(run_id) if r["path"] == op["path"]]
        new = strip_markers(data.decode("utf-8"), sorted(set(ids)))
        if op["op"] == "add":
            new = f"# removido por undo {run_id}\n"
        rev = store.update(full, new.encode("utf-8"), op["rev_after"])
        oid = journal.add_op(urun, op["item_id"], op["path"], "undo", op["rev_after"], ""); journal.confirm_op(oid, rev)
        res["reverted"].append(op["path"])
    journal.finish_run(urun, "ok", res)
    return res
