"""Pós-processamento de texto e legendagem para português do Brasil.

O Whisper entrega texto cru: com espaços tortos antes da pontuação, frases
sem maiúscula e, em trechos de silêncio, repetições inventadas. Este módulo
limpa tudo isso e monta legendas com quebras profissionais.
"""
import re
import unicodedata
from typing import Any, Dict, List, Optional

import config

# --- Expressões usadas na limpeza ---------------------------------------
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?…%»)\]])")
_SPACE_AFTER_OPEN = re.compile(r"([«(\[])\s+")
_MULTI_SPACE = re.compile(r"[ \t ]{2,}")
_ELLIPSIS = re.compile(r"\.\s*\.\s*\.+")
_MULTI_PUNCT = re.compile(r"([,;:])\1+")
# Vírgula que gruda na palavra seguinte, exceto entre dígitos (1,5 / 10:30).
_PUNCT_NEEDS_SPACE = re.compile(r"(?<=[^\d\s])([,;:])(?=[^\s\d])")
_SENTENCE_END_NEEDS_SPACE = re.compile(
    r"(?<=[a-záàâãéêíóôõúüç])([.!?])(?=[A-ZÁÀÂÃÉÊÍÓÔÕÚÜÇ])"
)
# Três ou mais repetições da mesma palavra: sinal clássico de alucinação.
_WORD_LOOP = re.compile(r"\b(\w+)(?:[ ,]+\1\b){2,}", re.IGNORECASE)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")
_STRONG_PUNCT = ("...", "…", ".", "!", "?")
_SOFT_PUNCT = (",", ";", ":", "—", "–")

# Frases que o Whisper inventa sobre silêncio ou música em vídeos brasileiros.
_HALLUCINATION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^legendas?\s+(pela|por)\s+comunidade.*$",
        r"^amara\.org\.?$",
        r"^legendado\s+por.*$",
        r"^tradu(ção|zido)\s+(e\s+legendas?\s+)?por.*$",
        r"^subtitles?\s+by.*$",
        r"^(se\s+)?inscreva-?\s*se\s+no\s+canal.*$",
        r"^ative\s+o\s+sininho.*$",
        r"^até\s+(a\s+|o\s+)?próximo\s+vídeo[.!]?$",
        r"^obrigad[oa]\s+por\s+assistir[.!]?$",
        r"^\W*$",
    )
]


def _strip_accents_lower(text: str) -> str:
    norm = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in norm if unicodedata.category(c) != "Mn")


def clean_text(text: str) -> str:
    """Normaliza espaçamento, pontuação e repetições de um trecho em pt-BR."""
    if not text:
        return ""
    text = text.replace("​", "").replace("﻿", "")
    text = text.replace(" ", " ").strip()
    text = _ELLIPSIS.sub("...", text)
    text = _MULTI_PUNCT.sub(r"\1", text)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _SPACE_AFTER_OPEN.sub(r"\1", text)
    text = _PUNCT_NEEDS_SPACE.sub(r"\1 ", text)
    text = _SENTENCE_END_NEEDS_SPACE.sub(r"\1 ", text)
    text = _WORD_LOOP.sub(r"\1 \1", text)
    text = _MULTI_SPACE.sub(" ", text)
    return text.strip()


def capitalize_sentences(text: str) -> str:
    """Garante inicial maiúscula no começo e depois de cada ponto final."""
    if not text:
        return ""
    rebuilt: List[str] = []
    for part in _SENTENCE_SPLIT.split(text):
        part = part.strip()
        if not part:
            continue
        # Não mexe em siglas já em caixa alta (ex.: "CNPJ do cliente").
        if part[0].islower():
            part = part[0].upper() + part[1:]
        rebuilt.append(part)
    return " ".join(rebuilt)


def is_hallucination(text: str, no_speech_prob: float = 0.0, avg_logprob: float = 0.0) -> bool:
    """Detecta os bordões que o modelo inventa sobre silêncio ou música."""
    stripped = text.strip()
    if not stripped:
        return True
    for pattern in _HALLUCINATION_PATTERNS:
        if pattern.match(stripped):
            return True
    # Segmento muito improvável e ainda classificado como "sem fala".
    return no_speech_prob > 0.85 and avg_logprob < -1.0


def drop_repeated_segments(segments: List[Dict[str, Any]], max_repeats: int = 2) -> List[Dict[str, Any]]:
    """Remove laços em que o modelo repete a mesma frase indefinidamente."""
    cleaned: List[Dict[str, Any]] = []
    streak_key: Optional[str] = None
    streak = 0
    for seg in segments:
        key = _strip_accents_lower(re.sub(r"[^\w\s]", "", seg["text"])).strip()
        if key and key == streak_key:
            streak += 1
            if streak > max_repeats:
                continue
        else:
            streak_key = key
            streak = 1
        cleaned.append(seg)
    return cleaned


def split_into_sentences(
    segments: List[Dict[str, Any]],
    max_duration: float = 13.0,
    max_chars: int = 210,
) -> List[Dict[str, Any]]:
    """Reparte trechos longos em frases, usando o tempo de cada palavra.

    O processamento em lote costuma devolver blocos de 30 segundos ou mais.
    São ótimos para o modelo e péssimos para quem vai navegar pelo texto: um
    clique no trecho joga o áudio meio minuto antes do ponto desejado. Aqui os
    blocos viram frases, com o tempo exato em que cada uma começa.
    """
    result: List[Dict[str, Any]] = []

    def emit(source: Dict[str, Any], words: List[Dict[str, Any]]) -> None:
        text = clean_text(" ".join(w["word"] for w in words))
        if not text:
            return
        piece = dict(source)
        piece.update(
            start=round(words[0]["start"], 3),
            end=round(words[-1]["end"], 3),
            start_str=seconds_to_short(words[0]["start"]),
            end_str=seconds_to_short(words[-1]["end"]),
            text=text,
            words=words,
            id=len(result) + 1,
        )
        result.append(piece)

    for seg in segments:
        words = seg.get("words") or []
        if not words:
            piece = dict(seg)
            piece["id"] = len(result) + 1
            result.append(piece)
            continue

        buffer: List[Dict[str, Any]] = []
        length = 0
        for word in words:
            buffer.append(word)
            length += len(word["word"])
            token = word["word"].strip()
            duration = word["end"] - buffer[0]["start"]

            ends_sentence = token.endswith(_STRONG_PUNCT) and not re.fullmatch(
                r"\d+\.", token  # "1." de uma enumeração não encerra frase
            )
            too_long = duration >= max_duration or length >= max_chars
            # Sem ponto final à vista: corta na vírgula, ou à força se o trecho
            # já ficou longo demais para servir de ponto de navegação.
            forced = too_long and (token.endswith(_SOFT_PUNCT) or duration >= max_duration * 1.8)

            if ends_sentence or forced:
                emit(seg, buffer)
                buffer, length = [], 0

        if buffer:
            emit(seg, buffer)

    return result


def build_paragraphs(
    segments: List[Dict[str, Any]], pause: float = 1.2, max_sentences: int = 5
) -> List[str]:
    """Agrupa os segmentos em parágrafos legíveis, quebrando em pausas longas."""
    paragraphs: List[str] = []
    buffer: List[str] = []
    sentences_in_buffer = 0
    prev_end: Optional[float] = None

    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue
        gap = 0.0 if prev_end is None else seg["start"] - prev_end
        ends_sentence = bool(buffer) and buffer[-1].rstrip().endswith(_STRONG_PUNCT)

        if buffer and ends_sentence and (gap >= pause or sentences_in_buffer >= max_sentences):
            paragraphs.append(" ".join(buffer))
            buffer = []
            sentences_in_buffer = 0

        buffer.append(text)
        if text.rstrip().endswith(_STRONG_PUNCT):
            sentences_in_buffer += 1
        prev_end = seg["end"]

    if buffer:
        paragraphs.append(" ".join(buffer))
    return [capitalize_sentences(clean_text(p)) for p in paragraphs if p.strip()]


def seconds_to_timestamp(seconds: float, srt: bool = True) -> str:
    """Converte segundos em 00:00:00,000 (SRT) ou 00:00:00.000 (VTT)."""
    total_ms = int(round(max(0.0, float(seconds)) * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    separator = "," if srt else "."
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def seconds_to_short(seconds: float) -> str:
    """Formato curto HH:MM:SS usado na interface."""
    return seconds_to_timestamp(seconds)[:8]


# --- Legendagem ---------------------------------------------------------

def wrap_balanced(text: str, max_chars: int, max_lines: int) -> List[str]:
    """Quebra o texto em linhas de comprimento equilibrado.

    Legenda profissional evita a "escadinha" (uma linha cheia e outra com duas
    palavras); por isso buscamos o corte mais próximo do meio, com preferência
    por cair logo depois de uma pontuação.
    """
    text = text.strip()
    if len(text) <= max_chars or max_lines <= 1:
        return [text]

    words = text.split()
    if len(words) < 2:
        return [text]

    target = len(text) / max_lines
    best_index: Optional[int] = None
    best_score = float("inf")

    running = 0
    for i, word in enumerate(words[:-1]):
        running += len(word) + (1 if i else 0)
        remainder = len(text) - running - 1
        score = abs(running - target)
        estourou = running > max_chars or (max_lines == 2 and remainder > max_chars)
        if estourou:
            # Respeitar o limite de colunas vem antes de qualquer estética.
            score += max_chars * 4
        elif word.endswith(_SOFT_PUNCT) or word.endswith(_STRONG_PUNCT):
            # Entre cortes válidos, o que cai depois da pontuação lê melhor.
            score -= max_chars * 0.25
        if score < best_score:
            best_score = score
            best_index = i

    if best_index is None:
        return [text]

    first = " ".join(words[: best_index + 1])
    rest = " ".join(words[best_index + 1:])
    lines = [first]
    if max_lines > 2:
        lines.extend(wrap_balanced(rest, max_chars, max_lines - 1))
    else:
        lines.append(rest)
    return [ln for ln in lines if ln]


def _cue_from_words(words: List[Dict[str, Any]]) -> Dict[str, Any]:
    text = clean_text(" ".join(w["word"].strip() for w in words))
    return {"start": words[0]["start"], "end": words[-1]["end"], "text": text}


def build_cues(
    segments: List[Dict[str, Any]],
    max_chars: int = config.SUB_MAX_CHARS_PER_LINE,
    max_lines: int = config.SUB_MAX_LINES,
    min_duration: float = config.SUB_MIN_DURATION,
    max_duration: float = config.SUB_MAX_DURATION,
    split_gap: float = config.SUB_SPLIT_GAP,
) -> List[Dict[str, Any]]:
    """Transforma segmentos em blocos de legenda com duração e quebra corretas.

    Usa os tempos por palavra quando disponíveis; sem eles, cai para o segmento
    inteiro (que ainda assim é quebrado em linhas equilibradas).
    """
    # Uma folga de ~8% dá espaço para a quebra cair numa fronteira de palavra
    # sem que nenhuma das linhas estoure o limite de colunas.
    budget = int(max_chars * max_lines * 0.92)
    cues: List[Dict[str, Any]] = []

    for seg in segments:
        words = seg.get("words") or []
        if not words:
            cues.append({"start": seg["start"], "end": seg["end"], "text": seg["text"]})
            continue

        current: List[Dict[str, Any]] = []
        current_len = 0
        for word in words:
            token = word["word"].strip()
            if not token:
                continue
            added = len(token) + (1 if current else 0)
            gap = word["start"] - current[-1]["end"] if current else 0.0
            duration = (word["end"] - current[0]["start"]) if current else 0.0

            if current and (
                current_len + added > budget or gap > split_gap or duration > max_duration
            ):
                cues.append(_cue_from_words(current))
                current, current_len = [], 0
                added = len(token)

            current.append(word)
            current_len += added

            # Fecha em ponto final quando já há texto suficiente para o bloco.
            if token.endswith(_STRONG_PUNCT) and current_len >= budget * 0.5:
                cues.append(_cue_from_words(current))
                current, current_len = [], 0

        if current:
            cues.append(_cue_from_words(current))

    # Estica blocos curtos demais sem deixar um invadir o seguinte.
    for i, cue in enumerate(cues):
        if cue["end"] - cue["start"] < min_duration:
            limit = cues[i + 1]["start"] if i + 1 < len(cues) else cue["start"] + min_duration
            cue["end"] = min(cue["start"] + min_duration, max(limit - 0.02, cue["end"]))
        if cue["end"] <= cue["start"]:
            cue["end"] = cue["start"] + 0.4

    result: List[Dict[str, Any]] = []
    for cue in cues:
        text = cue["text"].strip()
        if not text:
            continue
        result.append(
            {
                "id": len(result) + 1,
                "start": cue["start"],
                "end": cue["end"],
                "lines": wrap_balanced(text, max_chars, max_lines),
                "text": text,
            }
        )
    return result


def chars_per_second(cue: Dict[str, Any]) -> float:
    """Caracteres por segundo — acima de ~21 a legenda fica ilegível."""
    return len(cue["text"]) / max(0.001, cue["end"] - cue["start"])
