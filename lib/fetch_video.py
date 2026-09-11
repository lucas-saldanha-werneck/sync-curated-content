"""Vídeo: legendas (yt-dlp) → Groq (áudio) → sem transcrição. Frames opcionais (efficient).

Os módulos vendorados em lib/video/ não se importam entre si (só stdlib), então
não precisamos do truque de sys.path — import qualificado normal basta.
"""
from __future__ import annotations
import re
import subprocess
from pathlib import Path

from lib.fetch_types import FetchResult
from lib.video.download import fetch_captions as _fetch_captions, download_url as _download_url
from lib.video.transcribe import parse_vtt, format_transcript
from lib.video import whisper as _whisper
from lib.video import frames as _frames


def fetch_captions(url: str, out_dir: Path) -> dict:
    return _fetch_captions(url, Path(out_dir))


def download_media(url: str, out_dir: Path, audio_only: bool, max_mb: int | None = None) -> str:
    """Ponto único de download (áudio-only OU vídeo completo), com --max-filesize opcional.

    fetch_video nunca baixa duas vezes: chama isto no máximo uma vez por
    execução, e reaproveita o arquivo local resultante tanto para a
    transcrição Groq (whisper.extract_audio funciona em qualquer arquivo de
    mídia, vídeo incluso) quanto para os frames.
    """
    dl = _download_url(url, Path(out_dir), audio_only=audio_only, max_filesize_mb=max_mb)
    return dl["video_path"]


def groq_transcribe(audio_path: str, key: str) -> tuple[str, float]:
    """Devolve (texto, segundos de áudio).

    Usa whisper.transcribe_video do vendor: já faz extract_audio + chunking
    (plan_chunks/split_audio, quando o áudio passa de 24 MB) + retry em 429
    internamente — é o entrypoint de alto nível do próprio módulo (usado pelo
    seu __main__), então não há necessidade de dirigir transcribe_chunks na mão.
    audio_path pode ser um arquivo de áudio OU de vídeo — extract_audio extrai
    a trilha de áudio de qualquer um dos dois via ffmpeg.
    """
    audio_out = Path(audio_path).with_suffix(".16k.mp3")
    segments, _backend = _whisper.transcribe_video(audio_path, audio_out, backend="groq", api_key=key)
    dur = _whisper.audio_duration(audio_out)
    return format_transcript(segments), float(dur)


def extract_frames(video_path: str, out_dir: Path, max_frames: int) -> list[tuple[str, bytes]]:
    """Extrai frames de um arquivo de vídeo LOCAL já baixado — sem rede aqui.

    Via frames.extract_keyframes (tier "efficient": keyframes, near-instant;
    frames.py não expõe uma função única com parâmetro "detail"). Quem decide
    baixar o vídeo (e reaproveitar um já baixado para a transcrição) é
    fetch_video, através de download_media — extract_frames não baixa nada.
    """
    out_dir = Path(out_dir)
    frames, _meta = _frames.extract_keyframes(video_path, out_dir, max_frames=max_frames)
    return [(Path(f["path"]).name, Path(f["path"]).read_bytes()) for f in frames]


_CHANNEL_RE = re.compile(r"youtube\.com/(?:@[^/?#]+|channel/|c/|user/|playlist(?:[?#]|$))", re.I)


def is_channel_or_playlist(url: str) -> bool:
    """Página de canal/playlist do YouTube: não é um vídeo. Sem isso o yt-dlp percorre o canal inteiro
    (143 vídeos = ~5 min por tentativa, tudo bot-wall) — visto em produção 2026-09-11."""
    return bool(_CHANNEL_RE.search(url or ""))


def fetch_video(url: str, work: Path, cfg, groq_key: str, platform: str, run=subprocess.run,
                groq_seconds_used_today: int = 0, journal_add_groq=None) -> FetchResult:
    work = Path(work); work.mkdir(parents=True, exist_ok=True)
    r = FetchResult(platform=platform)
    if is_channel_or_playlist(url):
        return FetchResult(platform=platform, method="none", error="canal/playlist do YouTube, não é um vídeo: sem transcrição (só metadados)")
    try:
        cap = fetch_captions(url, work)
    except (Exception, SystemExit) as e:
        return FetchResult(platform=platform, method="none", error=f"yt-dlp: {e}")
    info = cap.get("info") or {}
    r.title, r.author = info.get("title"), info.get("uploader")
    r.duration_s = float(info["duration"]) if info.get("duration") else None

    # Teto de duração checado ANTES de qualquer download (não depois) — a
    # duração já é conhecida pelo passo de legendas, sem custo de rede extra.
    too_long = bool(r.duration_s) and r.duration_s > cfg.media_max_min * 60
    want_frames = cfg.frames_max > 0 and not too_long
    media_path: str | None = None  # arquivo local já baixado, se algum — nunca baixa 2x

    if cap.get("subtitle_path"):
        r.text, r.method = format_transcript(parse_vtt(cap["subtitle_path"])), "captions"
    elif too_long:
        r.method, r.error = "none", f"duração {r.duration_s/60:.0f}min > limite {cfg.media_max_min}min"
    else:
        need = int(r.duration_s or 0) or 600
        if groq_seconds_used_today + need > cfg.groq_daily_seconds:
            r.method, r.error = "none", "groq_quota"
        else:
            try:
                # Se frames também serão extraídos, baixa o vídeo completo (não
                # só áudio) para servir os dois usos com um download só.
                media_path = download_media(url, work, audio_only=not want_frames, max_mb=cfg.media_max_mb)
                r.text, secs = groq_transcribe(media_path, groq_key); r.method = "groq"
                if journal_add_groq: journal_add_groq(int(secs))
            except (Exception, SystemExit) as e:
                r.method, r.error = "none", f"groq/áudio: {e}"

    if want_frames and r.method != "none":
        # Sem transcrição (quota estourada ou falha) não vale a pena baixar o
        # vídeo inteiro só para frames — conserva banda/orçamento.
        try:
            if media_path is None:
                # Legendas já deram a transcrição — vídeo ainda não foi baixado.
                media_path = download_media(url, work, audio_only=False, max_mb=cfg.media_max_mb)
            cap_n = cfg.frames_short_max if (r.duration_s or 0) < 60 else cfg.frames_max
            r.frames = extract_frames(media_path, work / "frames", cap_n)
        except (Exception, SystemExit) as e:
            r.extra["frames_error"] = str(e)
    elif too_long:
        r.extra["frames_skipped"] = "duration>max"
    return r
