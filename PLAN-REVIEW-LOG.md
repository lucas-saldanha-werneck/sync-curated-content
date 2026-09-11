# Plan Review Log: sync-curated-content (DESIGN.md v0.1)
Started 2026-09-10 22:40 (America/Sao_Paulo). MAX_ROUNDS=3. PLAN_FILE=DESIGN.md. Codex read-only.

## Round 1 — Codex (thread 01a08e10-2477-7ae3-a59e-c5c1099a45e0, 2026-09-10 22:35)

Problemas materiais encontrados:

- **Três escritores concorrentes em `wiki/`, `INDEX.md`, `log.md` e catálogo permitem overwrite silencioso; o precedente arquitetural já recomenda não fazer ingest na VPS.** Correção: VPS deve publicar apenas raw/drafts isolados, ou usar Dropbox API com precondição por `rev` + merge determinístico por arquivo.
- **Hash-before/upload não é compare-and-swap: o Mac/iPhone pode gravar entre a checagem e `rclone copyto`, que sobrescreve a edição remota.** Correção: substituir o push de wiki por upload condicional no `rev` esperado; em mismatch, nunca sobrescrever.
- **O retry “pull+ingest uma vez” pode duplicar ou alterar novamente fatos, catálogo e log.** Correção: tornar ingest determinístico por `raindrop_id` e registrar uma chave idempotente por item em cada alteração.
- **`log.md` e `ferramentas-e-ideias.md` são hotspots inevitáveis; “conflito” também precisa escrevê-los e volta a conflitar.** Correção: migrar para arquivos imutáveis por item/execução e gerar índices/catálogos por reconciliação exclusiva.
- **Criar `<nome>.conflito-vps.md` pode colidir, não preserva a versão remota nem é um alerta confiável.** Correção: guardar candidato e bases em uma quarentena imutável com IDs/version IDs e emitir alerta acionável.
- **`rclone hashsum dropbox` está subespecificado e uma verificação pós-upload não detecta overwrite concorrente.** Correção: usar explicitamente o hash Dropbox ou `rclone check --download`, mas tratar `rev`/CAS como a garantia de integridade.
- **`rclone copy dropbox:… $WORK/wiki` sem limpeza pode manter arquivos locais removidos/renomeados e o modelo pode ressuscitá-los.** Correção: construir um workspace novo por item a partir de snapshot remoto, sem estado residual.
- **O raw “imutável” usa nome por data/plataforma/slug; dois links com mesmo slug podem sobrescrever uma fonte distinta.** Correção: incluir `raindrop_id` estável no nome e recusar overwrite se o conteúdo/ID não coincidir.
- **`_id > last_id` não é um cursor seguro para paginação, itens fora de ordem, importações, edição e reinício.** Correção: paginar por `created` com janela de sobreposição, cursor persistido e dedupe por ID.
- **Atualizar `last_id`/estado em momento errado pode perder itens ou reprocessá-los após crash.** Correção: usar journal transacional por etapa, `fsync`+rename atômico e reconciliação por manifest remoto no startup.
- **Crash após push da wiki e antes de `state.json`/Raindrop causa reingest e entradas duplicadas.** Correção: persistir um outbox durável e consultar marcadores idempotentes remotos antes de cada etapa.
- **A escrita de `state.json` inteiro pode corromper ou perder atualizações em execução manual concorrente.** Correção: usar SQLite local com WAL, lock global de processo e uma única instância do worker.
- **`sync now`, loop e `retry` não têm garantia de exclusão mútua além do lock “por item”.** Correção: implementar singleton lock para toda a fila e transformar comandos em pedidos serializados.
- **`PUT` no Raindrop pode sobrescrever nota/tags que o usuário alterou no celular.** Correção: reler, mesclar somente o marcador próprio de forma idempotente e repetir em conflito.
- **Um item marcado `nowiki` depois de entrar na fila ainda pode ser ingerido.** Correção: revalidar tags e estado do bookmark imediatamente antes de fetch e antes de marcar retorno.
- **Falha total de fetch recebe tag `wiki`, que comunica sucesso apesar de não haver fonte utilizável.** Correção: usar estados distintos (`wiki-partial`, `wiki-erro`, `wiki-skip`) e só aplicar `wiki` após ingest confirmado.
- **“3 falhas e para” cria uma fila morta sem alerta; `status` manual não é observabilidade.** Correção: healthcheck, métricas/heartbeat, alerta de item pendente/erro e resumo diário de backlog.
- **Logs e `runs.jsonl` podem conter URL, erro HTTP ou saída de CLI sensível e são publicados no vault.** Correção: sanitizar/redigir segredos, limitar tamanho e separar logs operacionais privados do histórico público.
- **Não há limite de fila, disco, tamanho de PDF/página/contexto ou retenção de mídia temporária.** Correção: impor quotas, limites de bytes/páginas/tokens, limpeza transacional e backpressure explícito.
- **URLs do Raindrop são entrada arbitrária: `curl`/fetchers podem fazer SSRF para IPs internos, metadata service ou redirects privados.** Correção: validar DNS/IP após cada redirect, bloquear redes privadas/link-local e restringir egress do container.
- **PDFs, HTML e ferramentas de scraping rodam no mesmo container que possui tokens e rclone RW.** Correção: executar fetch/conversão como usuário sem privilégios, com filesystem/recursos/egress isolados e sem segredos de escrita.
- **A instrução “texto é dado” não neutraliza prompt injection quando o modelo tem `Write`/`Edit`; ele pode alterar qualquer arquivo alcançável no workspace.** Correção: LLM deve produzir apenas JSON em sandbox sem escrita, seguido de validador/renderizador determinístico com allowlist estrita de paths e diffs.
- **A lista JSON declarada pelo modelo não é confiável: ele pode editar arquivo não listado ou imprimir JSON inválido.** Correção: calcular diff fora do modelo, validar schema e rejeitar qualquer path/conteúdo fora da política.
- **O processo Claude herda OAuth e demais segredos; conteúdo malicioso pode induzir leitura/exfiltração pela própria resposta.** Correção: separar ambientes e permissões, expondo ao subprocesso somente o token Claude e arquivos explicitamente montados.
- **O catálogo gerado de conteúdo externo é injetado pelo hook em futuros prompts do Mac, criando uma segunda rota de prompt injection.** Correção: hook deve emitir apenas referências/IDs sanitizados de campos com schema rígido, nunca texto livre derivado da fonte.
- **A promessa de OAuth por assinatura em polling contínuo não foi validada contra quota, rate limit, revogação ou termos de automação.** Correção: fazer prova de carga controlada antes do deploy e implementar classificação de erros, circuit breaker, backoff e orçamento diário.
- **Avisar 30 dias antes por data manual em `.env` falha para token revogado, emitido em outra data ou quota esgotada.** Correção: registrar emissão real, testar autenticação periodicamente e alertar por falha efetiva antes de bloquear a fila.
- **Opus como padrão para 5–20 itens/dia é risco de saturar a assinatura e deixar backlog crescer sem política de degradação.** Correção: definir orçamento diário, fila com prioridade, Sonnet como padrão e fallback explícito para “raw pendente”.
- **A prova de produção não testa corrida real Mac+iPhone+VPS, CAS, crash em cada fronteira, token 429/401, nem injection.** Correção: acrescentar testes de concorrência/revision mismatch, fault injection por etapa, payloads adversariais e restore de estado.

VERDICT: REVISE
### Claude's response — Round 1 (2026-09-10 22:50)

**Aceito (entra na v0.2):**
1. Hash-antes-do-push não é CAS → trocar rclone por **API do Dropbox** com `WriteMode.update(rev)` + `strict_conflict=True` para wiki/INDEX/log/catálogo, e `WriteMode.add` (nunca sobrescreve) para `raw/curated/`. Confirmado na doc do SDK Python (`dropbox.files.WriteMode`, `CommitInfo.strict_conflict`). `rclone` sai do container.
2. Workspace **novo por item** a partir de snapshot remoto (nada residual).
3. Nome do raw inclui `raindrop_id`: `AAAA-MM-DD_<plataforma>-<slug>-r<raindrop_id>.md`; `WriteMode.add` recusa overwrite.
4. Cursor por `created` com sobreposição de 10 min + dedupe por `_id` em SQLite (WAL). Estado por etapa (journal), lock singleton de processo; `now`/`retry` viram pedidos enfileirados.
5. Idempotência: cada alteração na wiki carrega marcador `<!-- curated:r<raindrop_id> -->`; antes de ingerir, o validador checa se o marcador já existe (crash entre push e state).
6. Raindrop: reler antes do PUT, mesclar só tags/nota próprias. Estados distintos: `wiki` (ingest confirmado), `wiki-parcial` (só metadados), `wiki-skip`, `wiki-erro`. Revalidar `nowiki` antes do fetch e antes do PUT.
7. Prompt injection: `claude -p` roda **sem Write/Edit** — só `Read/Glob/Grep` sobre o workspace e devolve **JSON** (páginas + conteúdo novo por seção). Um **aplicador determinístico** valida schema, allowlist de paths (`wiki/*.md`, `INDEX.md`, `log.md`, `ferramentas-e-ideias.md`), limites de tamanho, exige citação do raw, calcula o diff fora do modelo e aplica via CAS. Subprocesso recebe env mínimo (só `CLAUDE_CODE_OAUTH_TOKEN`, `CLAUDE_CONFIG_DIR`), sem segredos do Dropbox/Raindrop/Groq.
8. Hook do Mac injeta só campos com schema (nome, data, tipo, link da página), sanitizados, ≤ 120 chars por item; nunca texto livre da fonte.
9. SSRF: resolver DNS e bloquear IP privado/link-local após cada redirect; fetchers rodam como usuário sem privilégio; limites de bytes/páginas/duração/disco; limpeza de mídia temporária.
10. Observabilidade: healthcheck Docker; `status.md` (resumo diário: fila, erros, quota Groq, validade do token) publicado em `raw/curated/_state/`; hook `SessionStart` no Mac avisa se erros > 0 ou backlog > N; `runs.jsonl` fica privado na VPS, sanitizado.
11. Cota: modelo padrão **Sonnet 5** (Opus opt-in por tag `opus`), orçamento diário de itens, backlog degrada para "raw pendente"; teste de auth diário (`claude -p` trivial), alerta em 401/429 real, não por data manual.
12. Testes: adicionar corrida (rev mismatch), crash em cada fronteira, payload adversarial no raw, restore de estado.

**Rejeitado (com motivo):**
- "Não fazer ingest na VPS" — restrição explícita do dono (não depender do Mac). CAS + aplicador determinístico cobrem o risco.
- "Trocar `log.md`/catálogo por arquivos imutáveis + reconciliação" — muda a convenção do vault (append-only em 1 arquivo) que outras skills usam. Com CAS o append falha e re-aplica; 3 tentativas com re-pull bastam para 1 usuário.
- "Dois containers / isolamento de rede" — dono pediu 1 container. Compromisso: usuário sem privilégio para fetchers + env mínimo para o `claude`.
- "Quarentena com version IDs" — substituído por: em conflito CAS persistente, o item fica `ingest_conflict` na fila com o JSON proposto guardado localmente; `retry` reaplica. Nada de `.conflito-vps.md` no vault.

## Round 1b — claude-council (4 membros Opus 4.8, revisão cruzada anonimizada; run wf_555850e7-923, 2026-09-10 22:55)

Ranking cruzado (menor = melhor): A Arquiteto 1,0 · D Pesquisador 2,25 · B Cético 2,75 · C Pragmático 4,0. Confiança: todos "high".

Consenso (4/4): separar captura por item (imediata, sem LLM) de destilação em lote; cortar faster-whisper do v1; hash-check não é CAS; um dono por arquivo (VPS nunca toca `log.md` nem `INDEX.md`); fila = tags do próprio Raindrop, não `last_id`; env mínimo e sem escrita no subprocesso do Claude; hook casa contra gatilhos escritos pelo ingest, não grep.
Achados exclusivos: Arquiteto — undo via repo git FORA do vault; Raindrop como SPOF (gravar JSON completo no raw); heartbeat + UptimeRobot; §12 Prova. Pesquisador — Dropbox API `files/upload mode=update(rev)` é o CAS real; yt-dlp de IP de datacenter cai no bot-wall do YouTube (`bgutil-ytdlp-pot-provider`); `--json-schema` existe no CLI; nunca `--bare`. Cético — diluição da wiki por 5–20 TikToks/dia; launchd rclone NÃO está carregado. Pragmático — spike de 2 h antes de tudo; token OAuth é da MESMA conta → lote estrangula sessão interativa.
Dissenso: Cético quer VPS escrevendo só em namespace `wiki/curated/` com promoção humana; Arquiteto/Pesquisador aceitam páginas de tópico via CAS em lote. Decisão do chairman: CAS em lote + marcadores + undo git + piso de qualidade (`wiki-skip`), porque namespace separado quebra a regra "1 página por tópico" do vault. Modelo: Lucas decidiu Fable 5.1 (não Sonnet/Opus); o lote mantém 1–12 chamadas/dia.

### Claude's response — v0.2 escrita (2026-09-10 23:05)
Incorporado Codex R1 (12 itens) + conselho. Renomeado para `sync-raindrop` (skill separada de `sync-curated-content`). Ver DESIGN.md v0.2.

## Round 2 — Codex (2026-09-10 23:12)

A v0.2 corrige os riscos centrais da rodada anterior: CAS real no Dropbox, raw com ID e `add`, workspace limpo, LLM sem escrita, aplicador restrito, isolamento de env e `log.md` fora do alcance da VPS. Ainda há problemas materiais:

- **`wiki-parcial` é estado terminal na definição da fila, mas §10 promete retry automático no dia seguinte; após receber a tag o item deixa de ser visto.** Correção: manter `raw`/`retry-pending` até esgotar retries e só então aplicar `wiki-parcial` terminal.
- **A fila por tags não especifica paginação nem recuperação após indisponibilidade; uma volta com mais itens que a primeira página pode deixar itens para trás.** Correção: paginar integralmente todos os pendentes, com limite operacional e alerta de fila cheia, sem assumir que o poll de 30 s basta.
- **Tags genéricas (`raw`, `wiki`, `wiki-erro`) podem já existir por escolha do Lucas e silenciosamente excluir um bookmark da fila.** Correção: reservar tags namespaced, por exemplo `sr:raw`, `sr:wiki` e `sr:erro`.
- **`raw/curated/_catalog.jsonl` viola o contrato de `raw/` imutável e é um arquivo append concorrente sem protocolo de CAS/idempotência definido.** Correção: gravar um registro de catálogo imutável por `raindrop_id` com `WriteMode.add` e renderizar o catálogo a partir deles.
- **O passo “Push CAS cada arquivo” não funciona para página nova, log mensal novo, catálogo inicial ou status inicial: `WriteMode.update(rev)` só atualiza arquivo existente.** Correção: definir explicitamente `add` para criação e, em conflito de criação, re-snapshot/reconciliar antes de seguir.
- **CAS por arquivo não torna o lote atômico: metade das páginas pode subir, outra falhar, e um retry pode marcar o item como completo com alterações faltando.** Correção: persistir manifest do lote com operações esperadas e só aplicar tag final quando todas estiverem confirmadas; testar falha entre cada upload.
- **“Reaplica o delta” após conflito está subespecificado e pode inserir em seção apagada/renomeada pelo Mac ou duplicar conteúdo sem um anchor estável.** Correção: cada operação deve ter anchor/trecho-base exato e falhar para revisão quando o contexto não corresponder.
- **O marcador por item evita duplicação em um arquivo, mas não prova que todas as claims, catálogo e página nova daquele item foram aplicados.** Correção: registrar no manifest os artefatos obrigatórios por item e reconciliá-los antes de considerar `wiki`.
- **`undo <run_id>` via Git não é automaticamente seguro nem completo: não há regra para deletar página nova, reverter só blocos do lote, ou respeitar edição posterior do Mac.** Correção: implementar undo como patches reversos por marcador com CAS no `rev` pós-push; se divergir, abortar sem tocar no arquivo.
- **O hook ainda injeta texto livre semântico (`nome`, `when`) produzido a partir de fonte hostil; regex remove caracteres, não instruções como “Ignore instruções anteriores”.** Correção: injetar apenas ID/título determinístico e caminho, ou escolher de um vocabulário fechado de gatilhos/ações.
- **A proteção SSRF continua declarativa (“guarda SSRF”): usuário sem privilégio não impede acesso a metadata service, redes privadas ou redirects internos.** Correção: especificar e testar bloqueio de IPs privados/link-local após cada resolução/redirect e regra de egress no host/container.
- **Todos os segredos continuam no mesmo container que executa parsers e fetchers de conteúdo arbitrário; `read_only` não protege contra comprometimento de processo.** Correção: separar privilégios por subprocesso, montar segredos somente onde necessários e aplicar `no-new-privileges`, seccomp e limites de processo/rede.
- **O teste de Claude no spike só valida resposta; não valida que `permissions.deny` realmente impede leitura fora do workspace nem que Fable suporta esses flags com OAuth.** Correção: adicionar ao spike payloads que tentem ler `/proc`, volume e `.env`, e falhar o deploy se qualquer leitura escapar.
- **Não há tamanho máximo explícito do lote, embora cada item possa emitir 3–7 claims e o applier aceite no máximo seis arquivos/8 KB por arquivo; lotes válidos podem falhar indefinidamente.** Correção: fixar tamanho máximo do lote e política de split/degradação quando exceder limites.
- **A promessa “wiki em até 15 min” contradiz o teto de 12 lotes/dia e o adiamento para 03:00.** Correção: declarar SLA best-effort e expor ETA/backlog, ou reservar capacidade suficiente para cumprir o prazo.
- **`WriteMode.add` tratar qualquer arquivo existente como sucesso aceita um arquivo manual/corrompido com o mesmo nome.** Correção: confirmar `content_hash` e `raindrop_id` do arquivo remoto antes de marcar captura concluída.
- **Heartbeat/status podem dizer “vivo” enquanto a fila está permanentemente travada em conflito ou autenticação; UptimeRobot só detecta ausência de ping.** Correção: pingar o monitor apenas após checks de loop, fila, autenticação e idade máxima de pendências, com alerta distinto para degradação.

Sobre as rejeições: rejeitar “não fazer ingest na VPS” e “dois containers” é defensável dadas as restrições do dono, porque CAS + applier reduzem muito o risco. Já a rejeição de substituir o catálogo por registros imutáveis precisa ser revista: a v0.2 preservou um `_catalog.jsonl` mutável dentro de `raw/`, exatamente onde o contrato do vault exige imutabilidade.

VERDICT: REVISE
### Claude's response — Round 2 → v0.3 (2026-09-10 23:20)
Aceitos todos os 17 pontos: tags namespaced `sr:*`; `sr:retry` não-terminal (máx. 3) antes de `sr:parcial`; paginação completa dos pendentes com teto 200; catálogo vira registros imutáveis `raw/curated/_catalog/r<id>.json` (+`.distill.json`) e o `.md` é renderizado (rejeição anterior revista, Codex tinha razão); `add` para criação e conflito de criação → re-snapshot; manifesto por lote com operações e artefatos obrigatórios, tag final só com manifesto completo, `retry` retoma do manifesto; âncora exata por operação (falha → `sr:retry`); undo = patches reversos por marcador com CAS no `rev` pós-push, aborta se divergir; hook injeta só id/tipo (vocabulário fechado)/título ≤ 60 chars/link; SSRF especificado (lista de redes, re-check por redirect, proxy local para yt-dlp, egress iptables, 4 testes); segredos por subprocesso, `no-new-privileges`, `cap_drop ALL`, `pids_limit`; spike com payloads adversariais de leitura; lote máx. 5 itens + split/degradação; SLA best-effort com ETA no `status`; `add` em existente confere `content_hash` + `raindrop_id`; ping ao UptimeRobot só após checks (loop, poll, auth, idade de pendências, conflitos, disco).

## Round 3 — Codex (final, 2026-09-10 23:28)

Restam quatro problemas materiais:

- **Retry de fetch é impossível como descrito: na primeira falha grava-se um raw só de metadados; se a nova tentativa obtiver texto, o mesmo nome terá `content_hash` diferente e o passo 3 marca `sr:erro`.** Correção: separar um envelope raw imutável do conteúdo obtido por tentativa, ou criar um artefato imutável adicional `-attemptN` e referenciar o exitoso.
- **`nowiki` só é revalidado antes do fetch; se Lucas aplicar a tag após `sr:raw`, o lote ainda pode destilar e escrever na wiki.** Correção: revalidar `nowiki` antes de chamar Claude e imediatamente antes de qualquer upload; se presente, abortar o manifesto sem escrever.
- **O merge Raindrop continua com corrida TOCTOU: reler tags/nota e fazer `PUT` pode apagar alteração feita no celular entre essas duas operações.** Correção: usar atualização condicional por revisão se a API oferecer; se não oferecer, mover o estado autoritativo para registros duráveis próprios e deixar tags/notas como espelho best-effort.
- **O aplicador ainda aceita texto Markdown arbitrário do modelo; prompt injection pode inserir marcadores `curated:` falsos, embeds/links Obsidian ou conteúdo que corrompa idempotência e undo.** Correção: gerar marcadores exclusivamente no aplicador e impor uma gramática/escaping de Markdown que proíba HTML, embeds, links de esquema especial e marcadores reservados em todo campo do modelo.

VERDICT: REVISE
### Claude's response — Round 3 → v0.4 (2026-09-10 23:35)
MAX_ROUNDS=3 atingido com VERDICT: REVISE. Não houve convergência formal. Os 4 pontos foram aceitos e aplicados na v0.4, **sem re-verificação do Codex**:
1. Raw escrito uma única vez (sucesso ou após 3ª falha); tentativas viram registros imutáveis `r<id>.retryN.json`.
2. `nowiki` revalidado antes do Claude e antes do 1º upload; presente → aborta manifesto.
3. Estado autoritativo = registros imutáveis no Dropbox + journal; fila = Raindrop menos registros terminais; tags `sr:*` são espelho best-effort.
4. Gramática de Markdown restrita imposta pelo aplicador; marcadores `curated:` gerados só pelo aplicador; ocorrência vinda do modelo invalida o item.
Decisões do Lucas incorporadas: nome ok; escopo = todo o histórico (backfill no v1 com dry-run e checkpoint humano após 20 itens); CAS nas páginas de tópico.

### Adendo v0.4.1 — indicação do Lucas (2026-09-10 23:50)
`bradautomates/claude-video` (MIT, 17.060 ★, push 2026-07-01) = original do plugin `watch` já instalado (fork taoufik123-collab/claude-watch v0.2.0). Scripts stdlib-only vendorados em `lib/video/` (captions, VTT dedupe, Groq chunking com Retry-After, keyframes com dedup). Novo no v1: até 6 keyframes por vídeo em `raw/curated/<item>/frames/`, lidos pelo Fable no lote (limite 30 imagens/lote). Ver DESIGN §6.1. Não re-verificado por Codex/conselho.
