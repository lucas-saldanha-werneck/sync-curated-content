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
Não criamos um app Dropbox próprio. O serviço usa a configuração `rclone` que já existe na VPS (mesmas credenciais de outros serviços). Nada a fazer.

### UptimeRobot — feito
Monitor do tipo KEYWORD, apontando para `https://files.2olhares.com/sync-raindrop/health.json`, avisa se o serviço parar de responder.
ID do monitor: `PENDENTE (ver task-22-report.md)`.

### Claude — **precisa trocar**
A chave que está no `.env` da VPS hoje (`CLAUDE_CODE_OAUTH_TOKEN`) é um token de sessão temporário, válido só até por volta das 08:00 (horário de Brasília) de 2026-09-11. Depois disso o serviço para de conseguir destilar.

**O que fazer:**
1. No Mac, rode: `claude setup-token`
2. Copie o valor que ele mostrar.
3. Abra `/opt/sync-raindrop/.env` na VPS e troque o valor de `CLAUDE_CODE_OAUTH_TOKEN` por esse.
4. Rode: `ssh nova 'cd /opt/sync-raindrop/app && docker compose restart'`

### Groq — **precisa trocar**
A chave (`GROQ_API_KEY`) que está no `.env` hoje é **compartilhada com o LensReport**. Isso é arriscado: os dois serviços competem pela mesma cota, e um consumo alto de um afeta o outro.

**O que fazer:**
1. Abra `console.groq.com/keys` e crie uma chave nova, só para o `sync-raindrop`.
2. Abra `/opt/sync-raindrop/.env` na VPS e troque `GROQ_API_KEY` pela chave nova.
3. Rode: `ssh nova 'cd /opt/sync-raindrop/app && docker compose restart'`
