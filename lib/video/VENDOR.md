# Vendored from bradautomates/claude-video (MIT)

commit: 83da59fa78c3eee9e20f515fe75c438bb5166efd
source: skills/watch/scripts/{download,transcribe,whisper,frames}.py + LICENSE
files: download.py transcribe.py whisper.py frames.py LICENSE

## Patches locais (3 — ver nota sobre o 3º abaixo)

1. `download.py`: em cada `cmd = ["yt-dlp", ...]` (dentro de `fetch_captions` e de
   `download_url`), logo após `"yt-dlp",`, inserido:
   ```python
   *(["--proxy", os.environ["YTDLP_PROXY"]] if os.environ.get("YTDLP_PROXY") else []),
   *(["--extractor-args", "youtubepot-bgutilhttp:base_url=" + os.environ["YTDLP_POT_URL"]] if os.environ.get("YTDLP_POT_URL") else []),
   ```
   (+ `import os` no topo do arquivo). `YTDLP_PROXY` roteia o yt-dlp por um proxy
   quando definido; `YTDLP_POT_URL` aponta para um provedor de PO-token
   (bgutil-ytdlp-pot-provider) — o YouTube bloqueia o IP da VPS sem um PO-token,
   então este é o hook para um serviço rodando nessa URL.

2. `whisper.py`: `GROQ_MODEL = "whisper-large-v3"` → `GROQ_MODEL = "whisper-large-v3-turbo"`.

3. **(fix round 1, adicionado por instrução explícita do team-lead na revisão — excede o teto de "2 patches"
   da tarefa original; documentado aqui para auditoria, não decidido unilateralmente)** `download.py`:
   `download_url()` ganhou um parâmetro `max_filesize_mb: int | None = None`, inserindo, no mesmo padrão
   condicional dos patches 1/2, logo após eles no `cmd`:
   ```python
   *(["--max-filesize", f"{max_filesize_mb}M"] if max_filesize_mb else []),
   ```
   Motivo: a revisão do task 8 pediu um teto de tamanho de download (`cfg.media_max_mb`) como segunda linha de
   defesa além do teto de duração (que já é checado ANTES de qualquer download em `fetch_video`, usando
   `r.duration_s` obtido no passo de legendas) — cobre o caso de metadado de duração ausente/errado.

Nada além disso foi alterado nos arquivos vendorados.

## Assinaturas reais encontradas (guiam os wrappers em `lib/fetch_video.py`)

- `download.py`
  - `fetch_captions(url: str, out_dir: Path) -> dict` — `{"video_path": None,
    "subtitle_path": str|None, "info": {"title","uploader","duration","url"},
    "downloaded": False}`. Bate exatamente com o esperado no brief.
  - `download_url(url: str, out_dir: Path, audio_only: bool = False) -> dict` —
    mesmo shape de retorno, com `"video_path"` real e `"downloaded": True`. Bate
    com o esperado no brief.

- `transcribe.py`
  - `parse_vtt(path: str) -> list[dict]` e `format_transcript(segments) -> str`
    idênticos ao assumido no brief (dedupe de cues rolantes do YouTube incluso).

- `whisper.py` — **diferente do assumido no brief**, não existe
  `transcribe_chunks(audio, backend=..., api_key=...)` com essa assinatura. O
  que existe:
  - `extract_audio(video_path: str, out_path: Path) -> Path` (bate com o brief).
  - `audio_duration(audio_path: Path) -> float` (bate com o brief).
  - `transcribe_chunks(chunks: list[tuple[Path, float]], transcribe_one) -> list[dict]`
    — nível baixo: recebe uma lista de `(path_do_chunk, offset)` e um callable
    `transcribe_one(path) -> list[dict]`; não recebe `backend`/`api_key`
    diretamente.
  - `transcribe_video(video_path: str, audio_out: Path, backend: str|None=None,
    api_key: str|None=None) -> tuple[list[dict], str]` — função de alto nível
    que já faz `extract_audio` + planeja/faz chunking (`plan_chunks`/
    `split_audio`) quando o áudio passa de 24 MB + chama `transcribe_chunks`
    internamente + retries em 429. **Wrapper adaptado**: `groq_transcribe` usa
    `transcribe_video(...)` (não `transcribe_chunks` direto) por já encapsular
    o chunking/retry que o brief queria e por ser a função pensada para uso
    externo (é o entrypoint do `__main__` do próprio módulo).

- `frames.py` — **sem função única `extract(video_or_url, detail=...)`** como o
  brief especulava; o brief já previa esse caso ("se frames.py não expuser uma
  função única, envolver extract_keyframes/select_frames"). O que existe:
  - `extract(video_path, out_dir, fps, resolution=512, max_frames=100, ...)` —
    amostragem uniforme por fps, exige `fps` explícito.
  - `extract_keyframes(video_path, out_dir, resolution=512, max_frames=50,
    start_seconds=None, end_seconds=None, dedup=True) -> tuple[list[dict], dict]`
    — decodifica só keyframes (I-frames, via `-skip_frame nokey`), "the cheap,
    near-instant tier" (docstring do próprio vendor); cai para uniforme se
    detectar poucos keyframes. **Esse é o tier "efficient"** pedido no brief.
  - `extract_scene_or_uniform(...)` — tier caro (decode completo p/ detecção de
    cena); não usado aqui (reservado para um detail level "complete" futuro).
  - Nenhuma das funções aceita URL, só `video_path` local (`ffmpeg -i` precisa
    de arquivo, não de uma página web). **Wrapper adaptado**: `extract_frames`
    primeiro resolve `video_or_url` — se já é um arquivo local existente, usa
    direto; caso contrário baixa o vídeo real via `download.download_url(url,
    out_dir, audio_only=False)` (yt-dlp resolve a URL de stream) — e só então
    chama `frames.extract_keyframes(video_path, out_dir, max_frames=max_frames)`.
    Isso é necessário porque `fetch_captions` (usado no caminho de legendas) não
    baixa vídeo nenhum (`--skip-download`), então sem esse fallback não haveria
    arquivo local para extrair frame algum.
