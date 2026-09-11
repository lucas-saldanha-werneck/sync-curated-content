"""Vídeo: legendas (yt-dlp) → Groq (áudio) → sem transcrição. Frames opcionais (efficient).

Os módulos vendorados em lib/video/ não se importam entre si (só stdlib), então
não precisamos do truque de sys.path — import qualificado normal basta.
"""
from __future__ import annotations
import subprocess
from pathlib import Path

from lib.fetch_types import FetchResult
from lib.video.download import fetch_captions as _fetch_captions, download_url as _download_url
from lib.video.transcribe import parse_vtt, format_transcript
from lib.video import whisper as _whisper
from lib.video import frames as _frames


def fetch_captions(url: str, out_dir: Path) -> dict:
    return _fetch_captions(url, Path(out_dir))


def download_audio(url: str, out_dir: Path, max_min: int) -> str:
    dl = _download_url(url, Path(out_dir), audio_only=True)
    dur = float((dl.get("info") or {}).get("duration") or 0)
    if dur and dur > max_min * 60:
        raise RuntimeError(f"áudio de {dur/60:.0f} min > limite {max_min} min")
    return dl["video_path"]


def groq_transcribe(audio_path: str, key: str) -> tuple[str, float]:
    """Devolve (texto, segundos de áudio).

    Usa whisper.transcribe_video do vendor: já faz extract_audio + chunking
    (plan_chunks/split_audio, quando o áudio passa de 24 MB) + retry em 429
    internamente — é o entrypoint de alto nível do próprio módulo (usado pelo
    seu __main__), então não há necessidade de dirigir transcribe_chunks na mão.
    """
    audio_out = Path(audio_path).with_suffix(".16k.mp3")
    segments, _backend = _whisper.transcribe_video(audio_path, audio_out, backend="groq", api_key=key)
    dur = _whisper.audio_duration(audio_out)
    return format_transcript(segments), float(dur)


def extract_frames(video_or_url: str, out_dir: Path, max_frames: int) -> list[tuple[str, bytes]]:
    """Extrai frames via frames.extract_keyframes (tier "efficient": keyframes, near-instant).

    frames.py não expõe uma função única com um parâmetro "detail" nem aceita
    URL — só um arquivo de vídeo local. Se video_or_url não for um arquivo já
    existente em disco, baixa o vídeo real primeiro (fetch_captions, usado no
    caminho de legendas, não baixa vídeo — só metadados/legenda).
    """
    out_dir = Path(out_dir)
    video_path = video_or_url if Path(video_or_url).exists() else None
    if video_path is None:
        dl = _download_url(video_or_url, out_dir / "src", audio_only=False)
        video_path = dl["video_path"]
    frames, _meta = _frames.extract_keyframes(video_path, out_dir, max_frames=max_frames)
    return [(Path(f["path"]).name, Path(f["path"]).read_bytes()) for f in frames]


def fetch_video(url: str, work: Path, cfg, groq_key: str, platform: str, run=subprocess.run,
                groq_seconds_used_today: int = 0, journal_add_groq=None) -> FetchResult:
    work = Path(work); work.mkdir(parents=True, exist_ok=True)
    r = FetchResult(platform=platform)
    try:
        cap = fetch_captions(url, work)
    except (Exception, SystemExit) as e:
        return FetchResult(platform=platform, method="none", error=f"yt-dlp: {e}")
    info = cap.get("info") or {}
    r.title, r.author = info.get("title"), info.get("uploader")
    r.duration_s = float(info["duration"]) if info.get("duration") else None
    if cap.get("subtitle_path"):
        r.text, r.method = format_transcript(parse_vtt(cap["subtitle_path"])), "captions"
    else:
        need = int(r.duration_s or 0) or 600
        if groq_seconds_used_today + need > cfg.groq_daily_seconds:
            r.method, r.error = "none", "groq_quota"
        else:
            try:
                audio = download_audio(url, work, cfg.media_max_min)
                r.text, secs = groq_transcribe(audio, groq_key); r.method = "groq"
                if journal_add_groq: journal_add_groq(int(secs))
            except (Exception, SystemExit) as e:
                r.method, r.error = "none", f"groq/áudio: {e}"
    if r.method != "none":
        # Sem transcrição (quota estourada ou falha) não vale a pena baixar o
        # vídeo inteiro só para frames — conserva banda/orçamento.
        try:
            cap_n = cfg.frames_short_max if (r.duration_s or 0) < 60 else cfg.frames_max
            r.frames = extract_frames(url, work / "frames", cap_n)
        except (Exception, SystemExit) as e:
            r.extra["frames_error"] = str(e)
    return r
