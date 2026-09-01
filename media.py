"""Camada de mídia: localizar o FFmpeg, ler metadados, extrair e ler áudio.

Isolado do transcritor para que a lógica de IA não precise saber nada sobre
linha de comando, e para que os testes de ambiente, a diarização e o desenho
da forma de onda possam usar as mesmas funções.
"""
import json
import logging
import os
import shutil
import subprocess
import threading
import wave
from typing import Any, Callable, Dict, Iterator, List, Optional

import config

logger = logging.getLogger("media")

# No Windows, evita que cada chamada ao FFmpeg pisque uma janela de console.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

_binary_cache: Dict[str, str] = {}


class TranscriptionCancelled(Exception):
    """Sinaliza que o usuário interrompeu a tarefa — não é um erro de fato."""


# --- Localização dos binários -------------------------------------------

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


def ffmpeg_version() -> str:
    """Primeira linha do `ffmpeg -version`, para o diagnóstico do ambiente."""
    try:
        result = subprocess.run(
            [find_ffmpeg(), "-version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
        )
        return (result.stdout or "").splitlines()[0] if result.stdout else ""
    except (OSError, IndexError):
        return ""


# --- Inspeção -----------------------------------------------------------

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
    container = data.get("format", {})
    tags = {str(k).lower(): v for k, v in (container.get("tags") or {}).items()}

    duration = 0.0
    for candidate in (container.get("duration"), (audio or {}).get("duration")):
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
        "video_codec": (video or {}).get("codec_name"),
        "sample_rate": int((audio or {}).get("sample_rate") or 0),
        "channels": int((audio or {}).get("channels") or 0),
        "bitrate": int(container.get("bit_rate") or 0),
        "container": container.get("format_name"),
        "titulo": tags.get("title") or None,
        "size_bytes": int(container.get("size") or 0),
    }


def get_duration(file_path: str) -> float:
    return probe(file_path).get("duration", 0.0)


# --- Extração -----------------------------------------------------------

def extract_audio(
    input_file: str,
    wav_path: str,
    preview_path: Optional[str] = None,
    threads: int = 0,
    duration: float = 0.0,
    on_progress: Optional[Callable[[float], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    filters: Optional[str] = None,
) -> None:
    """Extrai o áudio para WAV 16 kHz mono (o formato que o Whisper espera).

    Quando `preview_path` é informado, o mesmo passo grava também um AAC leve
    usado pelo player da interface — assim o vídeo original pode ser apagado
    sem perder a reprodução sincronizada.

    `filters` é uma cadeia de filtros do FFmpeg aplicada **apenas** ao WAV que
    alimenta o reconhecimento: corte de graves e nivelamento de volume ajudam
    o modelo em gravações de reunião, mas deixariam o player com um som
    diferente do original, o que confundiria quem confere o texto ouvindo.

    O progresso é lido de `-progress pipe:1`, que o FFmpeg emite em pares
    `chave=valor` estáveis entre versões.
    """
    os.makedirs(os.path.dirname(wav_path) or ".", exist_ok=True)

    cmd = [find_ffmpeg(), "-y", "-nostdin", "-hide_banner", "-loglevel", "error"]
    if threads:
        cmd += ["-threads", str(threads)]
    cmd += ["-progress", "pipe:1", "-i", input_file]

    # Saída 1: WAV PCM 16 kHz mono para a transcrição.
    cmd += ["-vn", "-map", "0:a:0"]
    if filters:
        cmd += ["-af", filters]
    cmd += ["-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", wav_path]

    if preview_path:
        # Saída 2: áudio comprimido, sem filtros, para o player do navegador.
        cmd += ["-vn", "-map", "0:a:0", "-c:a", "aac", "-b:a", "64k",
                "-ar", "22050", "-ac", "1", preview_path]

    logger.info("FFmpeg: extraindo áudio de %s", os.path.basename(input_file))
    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
    )

    cancelled = False
    try:
        for line in process.stdout:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                process.terminate()
                break
            if on_progress and duration > 0 and line.startswith("out_time_us="):
                raw = line.split("=", 1)[1].strip()
                if raw.isdigit():
                    on_progress(min(1.0, int(raw) / 1_000_000 / duration))
    finally:
        process.stdout.close()
        stderr = process.stderr.read()
        process.stderr.close()
        process.wait()

    if cancelled:
        raise TranscriptionCancelled("Extração de áudio cancelada pelo usuário.")
    if process.returncode != 0:
        raise RuntimeError(
            f"O FFmpeg não conseguiu extrair o áudio deste arquivo.\n{stderr.strip()[:500]}"
        )
    if not os.path.exists(wav_path) or os.path.getsize(wav_path) < 1024:
        raise RuntimeError("O arquivo não contém uma faixa de áudio utilizável.")


# --- Leitura do WAV -----------------------------------------------------

def wav_info(wav_path: str) -> Dict[str, Any]:
    """Canais, taxa de amostragem e número de quadros do WAV extraído."""
    with wave.open(wav_path, "rb") as handle:
        return {
            "channels": handle.getnchannels(),
            "sample_rate": handle.getframerate(),
            "frames": handle.getnframes(),
            "sample_width": handle.getsampwidth(),
            "duration": handle.getnframes() / float(handle.getframerate() or 1),
        }


def read_wav_blocks(wav_path: str, block_frames: int = 480_000) -> Iterator[Any]:
    """Percorre o WAV em blocos, entregando amostras float32 entre -1 e 1.

    Um áudio de três horas tem 170 milhões de amostras; materializá-lo inteiro
    em float32 custaria 700 MB de RAM à toa. Blocos de 30 segundos mantêm o
    consumo em alguns megabytes e não mudam o resultado de nada que os use.
    """
    import numpy as np

    with wave.open(wav_path, "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        if width != 2:
            raise ValueError(f"WAV com {width * 8} bits não suportado (esperado 16).")

        while True:
            raw = handle.readframes(block_frames)
            if not raw:
                return
            samples = np.frombuffer(raw, dtype="<i2")
            if channels > 1:
                samples = samples.reshape(-1, channels).mean(axis=1)
            yield (samples.astype(np.float32) / 32768.0)


def waveform_peaks(wav_path: str, points: int = config.WAVEFORM_POINTS) -> List[float]:
    """Envelope do áudio em `points` amostras, para desenhar a forma de onda.

    Cada ponto é o pico absoluto do intervalo que ele representa — é o que faz
    a onda "parecer" o som, em vez de virar um borrão quando a média achata os
    transientes da fala.
    """
    try:
        import numpy as np
    except ImportError:
        return []

    try:
        info = wav_info(wav_path)
    except (OSError, wave.Error) as exc:
        logger.warning("Não foi possível ler %s: %s", wav_path, exc)
        return []

    total = max(1, info["frames"])
    points = max(50, min(points, 4000))
    per_point = max(1, total // points)

    peaks: List[float] = []
    leftover = None
    try:
        for block in read_wav_blocks(wav_path, block_frames=per_point * 200):
            data = np.concatenate((leftover, block)) if leftover is not None else block
            usable = (data.size // per_point) * per_point
            if usable:
                chunk = np.abs(data[:usable]).reshape(-1, per_point).max(axis=1)
                peaks.extend(float(v) for v in chunk)
            leftover = data[usable:]
    except (OSError, ValueError, wave.Error) as exc:
        logger.warning("Forma de onda indisponível para %s: %s", wav_path, exc)
        return []

    if leftover is not None and leftover.size:
        peaks.append(float(np.abs(leftover).max()))
    if not peaks:
        return []

    # Normalizar pelo percentil 99 em vez do máximo evita que um único
    # estalo de microfone achate a onda inteira.
    reference = float(np.percentile(np.array(peaks), 99)) or 1.0
    return [round(min(1.0, value / reference), 3) for value in peaks[:points]]
