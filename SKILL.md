---
name: sync-raindrop
description: Controla o serviço sync-raindrop que roda na VPS nova — puxa itens salvos no Raindrop, captura e destila automaticamente no vault (raw/curated/ + claims na wiki). Use SEMPRE que o usuário disser "sync raindrop", "status do raindrop", "o que entrou na wiki", "retry raindrop", "undo lote", "backfill raindrop", ou pedir para checar/forçar/reprocessar/desfazer a sincronização do Raindrop.
---

# sync-raindrop

Serviço que roda como container Docker na VPS `nova` (`/opt/sync-raindrop/app`, gerenciado por `docker compose`). Ele lê os itens salvos no Raindrop, captura o conteúdo, destila com Claude e grava no vault (`raw/curated/` + claims na wiki). Este skill não roda nada localmente — só chama o CLI do container via SSH e resume o resultado para o Lucas.

## Comandos (sempre via SSH, sempre este formato)

```
ssh nova docker exec sync-raindrop sh -c 'set -a; . /secrets/.env; set +a; exec python3 /app/bin/sync.py <comando> [args]'
```

- `status` — mostra o `status.md` mais recente (última rodada, pendências, erros, quota do Groq).
- `now` — enfileira uma rodada imediata (o serviço roda em loop; isso pula a fila).
- `retry <id>` — reenfileira um item do Raindrop pelo id, zerando o contador de erro.
- `undo <run_id>` — desfaz um lote de destilação (reverte o commit da wiki e os arquivos em `raw/curated/`).
- `backfill --all [--dry-run] [--continue]` — varre o Raindrop inteiro atrás de itens pendentes. `--dry-run` só mostra a contagem; sem `--continue` ele para a cada 20 itens processados (checkpoint) para o Lucas revisar a wiki antes de seguir.

## Como usar

1. Rode o comando pedido via SSH.
2. Leia a saída e resuma para o Lucas em português simples: o que aconteceu, quantos itens, se teve erro.
3. Para "o que entrou na wiki" — em vez do CLI, leia `wiki/log/AAAA-MM_curated.md` (mês corrente) direto no vault e resuma as entradas novas.
4. Para investigar um item específico (por que não capturou, por que ficou em erro), olhe `raw/curated/_state/status.md` no vault — ele lista os últimos erros por id.
5. O catálogo de ferramentas/ideias fica em `wiki/ferramentas-e-ideias.md` — é a página que o serviço mantém a partir dos itens destilados.

## Nunca

- **Nunca editar arquivos em `raw/curated/`** — é saída automática do serviço; edição manual quebra o rastreamento de versão dele. Se algo estiver errado, use `retry` ou `undo`, não edite na mão.
- **Nunca rodar duas instâncias do serviço** — ele já roda como container único na VPS; não subir uma segunda cópia local ou remota.
- **Nunca passar `--bare`** — flag não suportada pelo CLI atual; não existe fallback silencioso, só dá erro.
