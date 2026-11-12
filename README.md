# sync-raindrop — guia do Lucas

## O que é

Um serviço automático que roda 24h na VPS `nova`, dentro de um container Docker chamado `sync-raindrop` (`/opt/sync-raindrop/app`). Ele olha o que você salva no Raindrop, baixa o conteúdo, resume com Claude, e grava tudo no vault: o material bruto em `raw/curated/` e as conclusões (claims) nas páginas da wiki.

Você não precisa fazer nada para ele funcionar. Só salve links no Raindrop como sempre fez.

## Como ver o que ele fez

O serviço marca cada item do Raindrop com uma tag, para você acompanhar direto no Raindrop:

| Tag | Significado |
|---|---|
| `sr:raw` | Item capturado, aguardando ou já destilado |
| `sr:retry` | Deu erro, vai tentar de novo sozinho |
| `sr:wiki` | Já entrou na wiki |
| `sr:parcial` | Entrou, mas só parcialmente (algo falhou no meio) |
| `sr:skip` | Ignorado de propósito (ex: já existia) |
| `sr:erro` | Erro que não se resolveu sozinho — vale olhar |

Outros lugares para checar:
- `raw/curated/` no vault — material bruto capturado.
- `raw/curated/_catalog/` — registros estruturados de cada item.
- `raw/curated/_state/status.md` — status da última rodada e erros recentes.
- `wiki/ferramentas-e-ideias.md` — catálogo de ferramentas e ideias, mantido automaticamente.
- `wiki/log/AAAA-MM_curated.md` — log mensal do que entrou na wiki.

## Como controlar

- **Não quer que um item específico entre na wiki?** Coloque a tag `nowiki` nele no Raindrop. O serviço pula.
- **Quer marcar um item para ação futura?** Coloque a tag `implementar`. Hoje isso só aparece com um ⚡ no catálogo (`wiki/ferramentas-e-ideias.md`); uma versão futura vai agir sobre ela sozinha.
- **Quer desfazer um lote que entrou errado?** Peça no chat "desfaz o lote X" (ou rode `undo <run_id>` — veja `SKILL.md`).
- **Quer forçar uma rodada agora** (sem esperar o ciclo automático)? Peça "roda o sync raindrop agora".

## No Mac: sugestão automática de catálogo

Um hook (`Projects/.claude/hooks/curated-suggest.sh`) roda no início de cada sessão e a cada mensagem sua, e sugere itens do catálogo que podem ser relevantes para o que você está fazendo. O log dele fica em `~/.claude/curated-suggest.log`.

## Setup das chaves (feito em 2026-09-11)

Tudo abaixo já foi configurado. Fica registrado aqui para quando precisar trocar uma chave.

### Raindrop — feito
App "sync-raindrop" criado no Raindrop, token de teste (Test token) gerado e já colocado no `.env` da VPS. Nada a fazer.

### Dropbox — feito
Não criamos um app Dropbox próprio. O serviço usa a configuração `rclone` que já existe na VPS, mas só o remote `[dropbox]` dela (o `deploy.sh` grava esse remote, e nenhum outro, em `/opt/sync-raindrop/rclone/rclone.conf` com `host/rclone_only_remote.py`) (pasta própria, dona `app`, montada no container como `/secrets/rclone`). O `rclone` renova o token do Dropbox sozinho a cada 4 h e **regrava o arquivo** — por isso a pasta inteira é montada, nunca só o arquivo. Nada a fazer.

### UptimeRobot — feito
Monitor do tipo KEYWORD, apontando para `https://files.2olhares.com/sync-raindrop/health.json`, avisa se o serviço parar de responder.
ID do monitor: `803965554`.

### Claude — feito (2026-09-11 05:15)
`CLAUDE_CODE_OAUTH_TOKEN` no `.env` da VPS é um token de 1 ano gerado com `claude setup-token` (token de sessão do Mac **não serve**: é revogado quando o Mac renova o dele).

**Quando vencer (setembro de 2027) ou se for revogado:**
1. No Mac, rode `claude setup-token`, faça o login no link e copie o token. (Pelo iPhone via Remote Control também dá: o Claude roda o comando num `tmux`, você abre o link, cola o código no chat e ele grava o token direto na VPS.)
2. Troque o valor de `CLAUDE_CODE_OAUTH_TOKEN` em `/opt/sync-raindrop/.env`.
3. Rode: `ssh nova 'cd /opt/sync-raindrop/app && docker compose restart'`

### Groq — feito (2026-09-11 05:20)
`GROQ_API_KEY` no `.env` da VPS é uma chave própria do `sync-raindrop` (não é mais a do LensReport). Para trocar: nova chave em `console.groq.com/keys`, editar o `.env`, `docker compose restart`.


## Credits

The idea of a wiki vault that Claude keeps up to date from your sources came from Austin Marchese's video [How to Build A Self-Improving System with Claude](https://www.youtube.com/watch?v=2fc0NX9vIJ8).

The Raindrop part is my own and is not in the video: items I save in Raindrop are captured, transcribed and distilled into the vault by a service that runs on my VPS.

## License

MIT. See [LICENSE](LICENSE).
