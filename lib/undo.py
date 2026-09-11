"""Undo por marcador: remove só o que o lote inseriu, com CAS no rev pós-push."""
from __future__ import annotations
import re

def strip_markers(text: str, item_ids: list[int]) -> str:
    for iid in item_ids:
        # Remove curated marker block and following line
        text = re.sub(rf"<!-- curated:r{iid} [^>]*-->\n[^\n]*\n?", "", text)
        # Remove supersede mark (allow nested parens)
        text = re.sub(rf"~~(.*?)~~ \(superado em \d{{4}}-\d{{2}}-\d{{2}}, ver r{iid}: (?:[^()]|\([^()]*\))*\)", r"\1", text)
        # Remove catalog rows with marker
        text = re.sub(rf"^.*<!-- r{iid} -->.*$\n?", "", text, flags=re.MULTILINE)
        # Remove log lines with marker
        text = re.sub(rf"^- \d{{4}}-\d{{2}}-\d{{2}} r{iid} .*$\n?", "", text, flags=re.MULTILINE)
    return text

def undo_run(run_id: str, store, journal, cfg) -> dict:
    res = {"reverted": [], "skipped": [], "unchanged": []}
    urun = journal.start_run("undo", [])

    # Group ops by path
    ops_by_path = {}
    for op in journal.ops(run_id):
        if not op.get("rev_after"): continue
        path = op["path"]
        if path not in ops_by_path:
            ops_by_path[path] = []
        ops_by_path[path].append(op)

    # Process each path as a group
    for path, path_ops in ops_by_path.items():
        full = cfg.vault_path(path)
        # CAS target = last op's rev_after
        last_rev = path_ops[-1]["rev_after"]
        ok, cur_rev, _ = store.exists(full)
        if not ok or cur_rev != last_rev:
            res["skipped"].append(path); continue

        data, _ = store.download(full)
        old = data.decode("utf-8")
        # ids = all item_ids on path
        ids = sorted(set(op["item_id"] for op in path_ops))
        new = strip_markers(old, ids)
        # kind = "add" if ANY op was "add"
        if any(op["op"] == "add" for op in path_ops):
            new = f"# removido por undo {run_id}\n"

        # If nothing changed, don't write and report in unchanged
        if new == old:
            res["unchanged"].append(path); continue

        rev = store.update(full, new.encode("utf-8"), last_rev)
        # Record undo ops for each item_id on this path
        for op in path_ops:
            oid = journal.add_op(urun, op["item_id"], path, "undo", last_rev, "")
            journal.confirm_op(oid, rev)
        res["reverted"].append(path)

    journal.finish_run(urun, "ok", res)
    return res
