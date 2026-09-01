"""Camada de mídia: localizar o FFmpeg, ler metadados e extrair áudio.

Isolado do transcritor para que a lógica de IA não precise saber nada sobre
linha de comando, e para que os testes de ambiente possam usar as mesmas
funções.
"""
import json
import logging
import os
import shutil
import subprocess
import threading
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("media")

# No Windows, evita que cada chamada ao FFmpeg pisque uma janela de console.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

_binary_cache: Dict[str, str] = {}


def _search_winget(exe: str) -> Optional[str]:
    """Procura o executável nos diretórios em que o WinGet instala o FFmpeg."""
    home = os.path.expanduser("~")
    direct = os.path.join(home, "AppData", "Local", "Microsoft", "WinGet", "Links", exe)
    if os.path.exists(direct):
        return direct

    packages = os.path.join(home, "AppData", "Local", "Microsoft", "WinGet", "Packages")
    if os.path.isdir(packages):
        for root, _dirs, files in os.walk(packages):
            if exe in files:
                return os.path.join(root, exe)
    return None


def find_binary(name: str) -> str:
    """Localiza ffmpeg/ffprobe no PATH ou na instalação do WinGet (Windows)."""
    if name in _binary_cache:
        return _binary_cache[name]

    exe = f"{name}.exe" if os.name == "nt" else name
    found = shutil.which(name) or shutil.which(exe)
    if not found and os.name == "nt":
        found = _search_winget(exe)

    resolved = found or name
    _binary_cache[name] = resolved
    return resolved


def find_ffmpeg() -> str:
    return find_binary("ffmpeg")


def find_ffprobe() -> str:
    return find_binary("ffprobe")


def ffmpeg_available() -> bool:
    binary = find_ffmpeg()
    if os.path.isabs(binary):
        return os.path.exists(binary)
    return shutil.which(binary) is not None


def probe(file_path: str) -> Dict[str, Any]:
    """Lê duração, codecs e canais via ffprobe, em JSON.

    Muito mais confiável que raspar a saída de texto do FFmpeg, que muda de
    formato entre versões e depende do idioma do sistema.
    """
    cmd = [
        find_ffprobe(),
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        file_path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", creationflags=_NO_WINDOW,
        )
        if result.returncode != 0:
            logger.warning("ffprobe falhou em %s: %s", file_path, result.stderr.strip())
            return {}
        data = json.loads(result.stdout or "{}")
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Não foi possível inspecionar %s: %s", file_path, exc)
        return {}

    streams = data.get("streams", [])
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    video = next((s for s in streams if s.get("codec_type") == "video"), None)

    duration = 0.0
    for candidate in (data.get("format", {}).get("duration"), (audio or {}).get("duration")):
        try:
            duration = float(candidate)
            break
        except (TypeError, ValueError):
            continue

    return {
        "duration": duration,
        "has_audio": audio is not None,
        "has_video": video is not None,
        "audio_codec": (audio or {}).get("codec_name"),
        "sample_rate": int((audio or {}).get("sample_rate") or 0),
        "channels": int((audio or {}).get("channels") or 0),
        "size_bytes": int(data.get("format", {}).get("size") or 0),
    }


def get_duration(file_path: str) -> float:
    return probe(file_path).get("duration", 0.0)


def extract_audio(
    input_file: str,
    wav_path: str,
    preview_path: Optional[str] = None,
    threads: int = 0,
    duration: float = 0.0,
    on_progress: Optional[Callable[[float], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> None:
    """Extrai o áudio para WAV 16 kHz mono (o formato que o Whisper espera).

    Quando `preview_path` é informado, o mesmo passo grava também um AAC leve
    usado pelo player da interface — assim o vídeo original pode ser apagado
    sem perder a reprodução sincronizada.

    O progresso é lido de `-progress pipe:1`, que o FFmpeg emite em pares
    `chave=valor` estáveis entre versões.
    """
    os.makedirs(os.path.dirname(wav_path) or ".", exist_ok=True)

    cmd = [find_ffmpeg(), "-y", "-nostdin", "-hide_banner", "-loglevel", "error"]
    if threads:
        cmd += ["-threads", str(threads)]
    cmd += ["-progress", "pipe:1", "-i", input_file]
    # Saída 1: WAV PCM 16 kHz mono para a transcrição.
    cmd += ["-vn", "-map", "0:a:0", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", wav_path]
    if preview_path:
        # Saída 2: áudio comprimido para o player do navegador.
        cmd += ["-vn", "-map", "0:a:0", "-c:a", "aac", "-b:a", "64k", "-ar", "22050", "-ac", "1", preview_path]

    logger.info("FFmpeg: extraindo áudio de %s", os.path.basename(input_file))
    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
    )

    try:
        for line in process.stdout:
            if cancel_event is not None and cancel_event.is_set():
                process.terminate()
                raise TranscriptionCancelled("Extração de áudio cancelada pelo usuário.")
            if on_progress and duration > 0 and line.startswith("out_time_us="):
                raw = line.split("=", 1)[1].strip()
                if raw.isdigit():
                    on_progress(min(1.0, int(raw) / 1_000_000 / duration))
    finally:
        process.stdout.close()
        stderr = process.stderr.read()
        process.stderr.close()
        process.wait()

    if process.returncode != 0:
        raise RuntimeError(
            f"O FFmpeg não conseguiu extrair o áudio deste arquivo.\n{stderr.strip()[:500]}"
        )
    if not os.path.exists(wav_path) or os.path.getsize(wav_path) < 1024:
        raise RuntimeError("O arquivo não contém uma faixa de áudio utilizável.")


class TranscriptionCancelled(Exception):
    """Sinaliza que o usuário interrompeu a tarefa — não é um erro de fato."""
