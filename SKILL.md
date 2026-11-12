---
name: sync-curated-content
description: Controla o serviço de conteúdo curado que roda na VPS nova (container `sync-raindrop`) — puxa itens salvos no Raindrop, captura e destila automaticamente no vault (raw/curated/ + claims na wiki). Raindrop é a fonte atual; fontes seguidas (newsletters, canais, criadores) estão em design. Use SEMPRE que o usuário disser "sync raindrop", "sync curated", "curated content", "conteúdo curado", "status do raindrop", "o que entrou na wiki", "retry raindrop", "undo lote", "backfill raindrop", ou pedir para checar/forçar/reprocessar/desfazer a sincronização de conteúdo curado.
---

# sync-curated-content

Nome anterior: `sync-raindrop` (renomeada em 2026-09-16; Raindrop passou a ser uma fonte entre outras). O container na VPS continua chamado `sync-raindrop`.

Serviço que roda como container Docker na VPS `nova` (`/opt/sync-raindrop/app`, gerenciado por `docker compose`). Ele lê os itens salvos no Raindrop, captura o conteúdo, destila com Claude e grava no vault (`raw/curated/` + claims na wiki). Este skill não roda nada localmente — só chama o CLI do container via SSH e resume o resultado para o Lucas.

## Comandos (sempre via SSH, sempre este formato)

```
ssh nova "docker exec sync-raindrop sh -c 'set -a; . /secrets/.env; set +a; exec setpriv --reuid app --regid app --init-groups --inh-caps +setuid,+setgid --ambient-caps +setuid,+setgid python3 /app/bin/sync.py <comando> [args]'"
```

É a mesma troca de usuário do `entrypoint.sh`. Não use `gosu`: ele limpa as capabilities, e aí a queda para `app-fetch`/`app-claude` (lib/privs.py) falha.

As aspas duplas de fora são obrigatórias. O SSH junta os argumentos e o shell da VPS lê tudo de novo. Sem elas, as aspas simples somem: só o `set` roda no container (e imprime o ambiente dele) e o resto roda no host, onde `/secrets/.env` e o usuário `app` não existem.

- `status` — mostra o `status.md` mais recente (última rodada, pendências, erros, quota do Groq).
- `now` — na próxima volta do loop (até `poll_seconds`), destila o que já estiver capturado sem esperar o mínimo de itens nem a espera do lote. O teto diário de lotes e a trava de lotes inválidos continuam valendo.
- `retry <id>` — reenfileira um item do Raindrop pelo id (número obrigatório), zerando o contador de erro. Item já destilado é re-destilado, e as claims antigas dele na wiki são trocadas pelas novas (não somadas).
- `recapture <id>` — reabre o item e manda refazer a CAPTURA (raw novo), não só a destilação. Use quando o raw veio vazio ou errado; `retry` só reenfileira.
- `undo <run_id>` — desfaz um lote de destilação: tira da wiki as claims, as linhas do catálogo e do log e as páginas novas que o lote gravou (só nos arquivos que ninguém editou depois; os outros aparecem em `skipped`). Grava `r<id>.undoneN.json` em `raw/curated/_catalog/` para o item não voltar ao catálogo no lote seguinte. O raw capturado fica. Para destilar de novo, `retry <id>`.
- `backfill --all | --since AAAA-MM-DD [--dry-run] [--continue]` — varre o Raindrop (inteiro, ou desde a data) atrás de itens pendentes. `--dry-run` só mostra a contagem; sem `--continue` ele para a cada 20 itens processados (checkpoint) para o Lucas revisar a wiki antes de seguir. Sem `--dry-run`, o backfill pega o mesmo lock do serviço; com o serviço em loop ele recusa ("o lock está com outra instância") e sai com código 2. Para rodar: pare o serviço, rode o backfill num container avulso e suba o serviço de novo:
  `ssh nova "cd /opt/sync-raindrop/app && docker compose stop && docker compose run --rm sync-raindrop backfill --all; docker compose start"`
  (o `run` passa pelo `entrypoint.sh`, que já carrega os segredos e faz a troca de usuário).

Comando ou flag fora desta lista (ex.: `--bare`) dá erro, sem fallback silencioso. `run` e `once` também existem, mas são do próprio serviço (o loop do container e uma rodada síncrona de debug) — o serviço já roda em loop, não chame no dia a dia.

## Como usar

1. Rode o comando pedido via SSH.
2. Leia a saída e resuma para o Lucas em português simples: o que aconteceu, quantos itens, se teve erro.
3. Para "o que entrou na wiki" — em vez do CLI, leia `wiki/log/AAAA-MM_curated.md` (mês corrente) direto no vault e resuma as entradas novas.
4. Para investigar um item específico (por que não capturou, por que ficou em erro), olhe `raw/curated/_state/status.md` no vault — ele lista os últimos erros por id.
5. O catálogo de ferramentas/ideias fica em `wiki/ferramentas-e-ideias.md` — é a página que o serviço mantém a partir dos itens destilados.

## Nunca

- **Nunca editar arquivos em `raw/curated/`** — é saída automática do serviço; edição manual quebra o rastreamento de versão dele. Se algo estiver errado, use `retry` ou `undo`, não edite na mão.
- **Nunca rodar duas instâncias do serviço** — ele já roda como container único na VPS; não subir uma segunda cópia local ou remota.
