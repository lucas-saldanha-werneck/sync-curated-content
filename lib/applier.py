"""Aplicador determinístico: valida a saída do modelo, monta operações com âncora exata e aplica via CAS."""
from __future__ import annotations
import re

import hashlib
from dataclasses import dataclass, field

from lib.grammar import GrammarError, validate_text, validate_triggers, validate_type
from lib.snapshot import Snap

class LimitError(Exception): ...

HUBS = {"wiki/2olhares.md", "wiki/luxvolt.md", "wiki/pessoal.md"}
WIKI_PAGE_RE = re.compile(r"^wiki/[a-z0-9-]+\.md$")   # mesmo `pattern` de claims[].page/supersedes[].page no schema
NEW_PAGE_RE = re.compile(r"^[a-z0-9-]{3,60}$")        # mesmo `pattern` de new_page.name no schema
MIN_OLD_TEXT = 8                                       # old_text vazio/curto casaria em qualquer lugar (vazio = topo do arquivo)


@dataclass
class Op:
    path: str
    kind: str                      # "update" | "add"
    before: bytes | None
    after: bytes
    rev_before: str | None
    item_ids: list
    delta_sha: str
    rev_after: str | None = None


@dataclass
class Manifest:
    run_id: str
    ops: list = field(default_factory=list)
    per_item: dict = field(default_factory=dict)     # per_item[id] = {"decision","pages","catalog","required","done"}
    warnings: list = field(default_factory=list)


def _one_line(text: str) -> str:
    """Claim é UMA linha: o marcador `<!-- curated:rN -->` cobre só a linha seguinte (undo/idempotência dependem disso).
    Quebras de linha viram espaço; cabeçalho markdown no início ("## ") cai — bullet não é título."""
    return re.sub(r"^#+\s*", "", " ".join(text.split()))


def marker(item_id: int, today: str, run_id: str | None = None) -> str:
    """O run_id no marcador separa "já aplicado NESTE lote" (re-plan após conflito: idempotente) de
    "contribuição de um lote anterior" (retry/recapture/redo: é trocada pela nova)."""
    return f"<!-- curated:r{item_id} {today}{' ' + run_id if run_id else ''} -->"


def strip_item_claims(text: str, item_id: int) -> str:
    """Remove as claims (marcador + bullet com link para o raw) que o item deixou em lotes anteriores.
    Só casa a linha de claim (`- ... ([raw](../...))`), nunca o corpo de uma página nova criada pelo item."""
    return re.sub(rf"<!-- curated:r{item_id} [^>]*-->\n- [^\n]*\(\[raw\]\(\.\./[^)\n]*\)\)[ \t]*(?:\n|$)", "", text)


def safe_title(title) -> str:
    """Título do Raindrop vai cru para a linha do log da wiki: tira o que vira markdown ativo
    (link, imagem, wikilink, HTML, código, tabela) e quebras de linha."""
    t = " ".join(str(title or "").split())
    t = re.sub(r"\b(?:https?|javascript|vbscript|data|file|obsidian):[^\s)\]>]*", "", t, flags=re.I)   # antes de tirar ( [ <
    t = re.sub(r"[\[\]()<>!`|\\]", "", t)
    return " ".join(t.split())[:80]


TAIL_HEADINGS = ("## fontes", "## ver também")


def insert_after_anchor(text: str, anchor: str, block: str) -> str | None:
    if anchor == "" or anchor.strip().lower() in TAIL_HEADINGS:
        # sem âncora (ou âncora "Fontes"/"Ver também"): entra antes do bloco final dessas seções, nunca dentro dele (bug 2026-10-02)
        lines = text.rstrip("\n").split("\n")
        cut = len(lines)
        for i in range(len(lines) - 1, -1, -1):
            h = lines[i].strip().lower()
            if h.startswith("## "):
                if h not in TAIL_HEADINGS:
                    break
                cut = i
        if cut == len(lines):
            return "\n".join(lines) + "\n" + block + "\n"
        return "\n".join(lines[:cut] + [block, ""] + lines[cut:]) + "\n"
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if ln.strip() == anchor.strip():
            return "\n".join(lines[: i + 1] + [block] + lines[i + 1:])
    return None


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _cell(s) -> str:
    """Escapa um valor para caber numa célula de tabela markdown de uma linha só."""
    return str(s).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def render_catalog(entries: list[dict]) -> bytes:
    head = ["# Ferramentas e ideias (curadoria automática)", "",
            "Gerado por `sync-raindrop` a partir de `raw/curated/_catalog/`. Não editar à mão: é regenerado a cada lote.", "",
            "| Data | Nome | Tipo | Para que serve | Quando usar | Gatilhos | Raw | ⚡ |", "|---|---|---|---|---|---|---|---|"]
    rows = []
    # decisão "skip" grava "catalog": null — não tem linha no catálogo e não pode derrubar a regeração (bug 2026-09-11)
    entries = [e for e in entries if isinstance(e.get("catalog"), dict)]
    for e in sorted(entries, key=lambda x: (x.get("date", ""), x.get("id", 0)), reverse=True):
        c = e["catalog"]
        rows.append(
            f"| {e.get('date', '')} | {_cell(c.get('name', ''))} | {_cell(c.get('type', ''))} | {_cell(c.get('what', ''))} | {_cell(c.get('when', ''))} | "
            f"{_cell(', '.join(c.get('triggers', [])))} | [raw](../{e.get('raw', '')}) | {'⚡' if c.get('implement') else ''} |"
            f" <!-- r{e.get('id')} -->"
        )
    return ("\n".join(head + rows) + "\n").encode("utf-8")


def plan_ops(output: dict, snap: Snap, items: dict, cfg, run_id: str, today: str, triggers_vocab: set[str],
             existing_catalog: list | None = None) -> Manifest:
    m = Manifest(run_id=run_id)
    files = {rel: bytearray(data) for rel, (data, _r) in snap.files.items()}
    revs = {rel: r for rel, (_d, r) in snap.files.items()}
    touched: dict[str, set] = {}
    pages_allowed = {rel[5:-3] for rel in files if rel.startswith("wiki/") and rel.endswith(".md")}
    catalog_entries = list(existing_catalog or [])
    log_lines = []

    for it in output.get("items", []):
        try:
            iid = int(it["raindrop_id"])
        except (KeyError, TypeError, ValueError):
            m.warnings.append(f"r-1: raindrop_id inválido ou ausente: {it.get('raindrop_id')!r}")
            continue                                        # id malformado não pode abortar o lote inteiro
        info = items.get(iid)
        if not info:
            m.warnings.append(f"r{iid}: item desconhecido na saída")
            continue
        src = info.get("link")
        raw_rel = info.get("raw")
        pi = {"decision": it.get("decision"), "pages": [], "catalog": None, "required": [], "done": []}
        m.per_item[iid] = pi
        mk = marker(iid, today, run_id)

        # edições deste item ficam num staging local; só entram no `files`/`touched` compartilhados
        # se o item inteiro terminar sem erro — evita vazar edição parcial de um item que caiu para "retry".
        item_files: dict[str, bytearray] = {}
        item_touched: set[str] = set()

        try:
            if it["decision"] == "skip":
                pi["reason"] = validate_text(it.get("reason", ""), 200, pages_allowed)
                pi["required"] = ["log"]
            else:
                cat = it["catalog"]
                pi["catalog"] = {
                    "name": validate_text(cat["name"], 80, pages_allowed),
                    "type": validate_type(cat["type"], cfg.types_vocab),
                    "what": validate_text(cat["what"], 200, pages_allowed),
                    "when": validate_text(cat["when"], 200, pages_allowed),
                    "triggers": validate_triggers(cat.get("triggers", []), triggers_vocab),
                    "implement": bool(cat.get("implement")),
                }
                pi["required"] = ["catalog", "log"] + (["claims"] if it["decision"] == "wiki" else [])

            # retry/recapture/redo: claims que o item deixou em lote anterior saem de toda página que esta
            # saída não tocar (nas páginas tocadas, a troca acontece na inserção, logo abaixo)
            targets = {c.get("page") for c in it.get("claims", [])} if it["decision"] == "wiki" else set()
            for rel in list(files):
                if (rel in targets or not WIKI_PAGE_RE.match(rel) or rel == cfg.catalog_page
                        or f"<!-- curated:r{iid} ".encode() not in bytes(files[rel])):
                    continue
                old_txt = bytes(files[rel]).decode("utf-8", "ignore")
                if mk in old_txt:
                    continue                                    # já é deste lote (re-plan)
                new_txt = strip_item_claims(old_txt, iid)
                if new_txt != old_txt:
                    item_files[rel] = bytearray(new_txt.encode("utf-8")); item_touched.add(rel)

            if it["decision"] == "wiki":
                blocks: dict[str, list] = {}
                for c in it.get("claims", []):
                    page = c["page"]
                    # repete o `pattern` do schema: a saída pode vir pelo fallback sem validação do CLI (`result` em texto)
                    if (not isinstance(page, str) or not WIKI_PAGE_RE.match(page) or page in HUBS
                            or page == cfg.catalog_page or page not in files):
                        raise GrammarError(f"página inválida: {page}")
                    text = _one_line(validate_text(c["text"], 600, pages_allowed, source_url=src))
                    blocks.setdefault(page, []).append((c.get("anchor", ""), f"- {text} ([raw]({'../' + raw_rel}))"))

                if it.get("new_page"):
                    np = it["new_page"]
                    if not isinstance(np.get("name"), str) or not NEW_PAGE_RE.match(np["name"]):
                        raise GrammarError(f"nome de página nova inválido: {np.get('name')!r}")
                    rel = f"wiki/{np['name']}.md"
                    already = files.get(rel)                                 # snapshot: bytearray da página, ou None
                    own = already is not None and f"<!-- curated:r{iid} ".encode() in bytes(already)
                    if rel in HUBS or rel == cfg.catalog_page or (already is not None and not own):
                        raise GrammarError(f"página nova já existe: {rel}")     # colisão real: existe e não tem o marcador deste item
                    # re-plan após conflito: este item já criou a página NESTE lote → não recria, mas segue com
                    # claims/catálogo/log (o `continue` antigo pulava o item inteiro). Página de lote anterior do
                    # mesmo item (retry/redo) é regerada com o conteúdo novo.
                    if not (own and mk.encode() in bytes(already)):
                        body = validate_text(np["body"], 6000, pages_allowed | {np["name"]}, source_url=src)
                        summ = validate_text(np["summary"], 200, pages_allowed)
                        item_files[rel] = bytearray(f"# {np['name']}\n\n{summ}\n\n{mk}\n{body}\n".encode())
                        item_touched.add(rel)
                    pi["pages"].append(rel)

                for page, lst in blocks.items():
                    base = (item_files[page] if page in item_files else files[page]).decode("utf-8")
                    if mk in base:
                        # idempotência: este item já deixou marcador nesta página NESTE lote (re-plan após conflito).
                        # (checado UMA VEZ contra o texto anterior às inserções deste item, não a cada claim,
                        # senão o 1º claim inserido faria o 2º claim do MESMO item se autodetectar como "já aplicado")
                        if page not in pi["pages"]:
                            pi["pages"].append(page)
                        continue
                    txt = strip_item_claims(base, iid)          # claims de lote anterior do item são trocadas, não somadas
                    for anchor, line in lst:
                        block = f"{mk}\n{line}"
                        new = insert_after_anchor(txt, anchor, block)
                        if new is None:
                            raise GrammarError(f"âncora não encontrada em {page}: {anchor!r}")
                        txt = new
                    item_files[page] = bytearray(txt.encode("utf-8"))
                    item_touched.add(page)
                    if page not in pi["pages"]:
                        pi["pages"].append(page)

                for sp in it.get("supersedes", []):
                    page = sp["page"]
                    # mesma barreira dos claims, mais catálogo e logs: o modelo não pode riscar CLAUDE.md, hubs ou logs
                    if (not isinstance(page, str) or not WIKI_PAGE_RE.match(page) or page in HUBS
                            or page == cfg.catalog_page or page.startswith("wiki/log/") or page not in files):
                        raise GrammarError(f"supersedes: página inválida: {page}")
                    if not isinstance(sp["old_text"], str) or len(sp["old_text"].strip()) < MIN_OLD_TEXT:
                        raise GrammarError(f"supersedes: old_text vazio ou curto demais em {page}")
                    base = item_files[page] if page in item_files else files.get(page)
                    if base is not None and sp["old_text"] in base.decode("utf-8"):
                        note = validate_text(sp["note"], 200, pages_allowed)
                        txt = base.decode("utf-8").replace(
                            sp["old_text"], f"~~{sp['old_text']}~~ (superado em {today}, ver r{iid}: {note})", 1
                        )
                        item_files[page] = bytearray(txt.encode())
                        item_touched.add(page)
                    else:
                        m.warnings.append(f"r{iid}: supersedes não encontrado em {page}")
        except (GrammarError, KeyError, TypeError) as e:
            pi["decision"] = "retry"
            pi["error"] = f"{type(e).__name__}: {e}"
            pi["pages"] = []
            pi["catalog"] = None
            m.warnings.append(f"r{iid}: {e}")
            # item_files/item_touched são descartados aqui: nenhuma edição parcial deste item
            # chega a tocar `files`/`touched` compartilhados.
        else:
            for rel, data in item_files.items():
                files[rel] = data
                revs.setdefault(rel, None)
            for rel in item_touched:
                touched.setdefault(rel, set()).add(iid)
            if pi["catalog"]:
                catalog_entries = [e for e in catalog_entries if e.get("id") != iid]
                catalog_entries.append({"id": iid, "date": today, "raw": raw_rel, "catalog": pi["catalog"], "pages": pi["pages"]})
            log_lines.append(
                f"- {today} r{iid} {it['decision']}: {safe_title(info.get('title'))} → "
                f"{', '.join('[[' + p[5:-3] + ']]' for p in pi['pages']) or '—'}"
            )

    # catálogo renderizado
    cat_rel = cfg.catalog_page
    if any(v.get("catalog") for v in m.per_item.values()):
        files[cat_rel] = bytearray(render_catalog(catalog_entries))
        touched.setdefault(cat_rel, set()).update(k for k, v in m.per_item.items() if v.get("catalog"))
        revs.setdefault(cat_rel, None)

    # log curated do mês
    if log_lines:
        log_rel = cfg.log_curated_fmt.format(ym=today[:7])
        old = files.get(log_rel, bytearray(f"# Log curated {today[:7]}\n\n".encode())).decode("utf-8")
        # idempotência: linha "- <data> r<id> ..." que já está no log (apply parcial + re-plan) não é reescrita
        fresh = [ln for ln in log_lines if ln not in old.split("\n")]
        if fresh:
            files[log_rel] = bytearray((old.rstrip("\n") + "\n" + "\n".join(fresh) + "\n").encode())
            revs.setdefault(log_rel, None)
            touched.setdefault(log_rel, set()).update(m.per_item.keys())

    # ops
    for rel, ids in touched.items():
        if not ids:
            continue
        before = snap.files.get(rel, (None, None))[0]
        after = bytes(files[rel])
        if before == after:
            continue
        if before is not None and abs(len(after) - len(before)) > cfg.max_delta_bytes:
            raise LimitError(f"{rel}: delta {abs(len(after) - len(before))} > {cfg.max_delta_bytes}")
        m.ops.append(Op(path=rel, kind="update" if before is not None else "add", before=before, after=after,
                         rev_before=revs.get(rel), item_ids=sorted(ids), delta_sha=_sha(after)))
    if len(m.ops) > cfg.max_files_per_batch:
        raise LimitError(f"{len(m.ops)} arquivos > {cfg.max_files_per_batch}")
    return m


def apply(manifest: Manifest, store, journal, cfg) -> Manifest:
    for op in manifest.ops:
        if op.rev_after:
            continue
        full = cfg.vault_path(op.path)
        # uma linha de op POR ITEM (o undo deriva os ids a apagar dessas linhas — review Opus M1)
        op_ids = [journal.add_op(manifest.run_id, iid, op.path, op.kind, op.rev_before, op.delta_sha) for iid in (op.item_ids or [0])] if journal else []
        if op.kind == "update":
            rev = store.update(full, op.after, op.rev_before)
        else:
            rev = store.add(full, op.after)
        op.rev_after = rev
        for op_id in op_ids:
            journal.confirm_op(op_id, rev)
        for iid in op.item_ids:
            pi = manifest.per_item.get(iid)
            if pi is not None:
                key = "catalog" if op.path == cfg.catalog_page else "log" if op.path.startswith("wiki/log/") else "claims"
                if key in pi["required"] and key not in pi["done"]:
                    pi["done"].append(key)
    return manifest
