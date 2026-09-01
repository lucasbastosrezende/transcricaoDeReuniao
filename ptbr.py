"""Pós-processamento de texto e legendagem para português do Brasil.

O Whisper entrega texto cru: com espaços tortos antes da pontuação, frases
sem maiúscula e, em trechos de silêncio, repetições inventadas. Este módulo
limpa tudo isso e monta legendas com quebras profissionais.

Duas decisões guiam o que entra aqui: **nunca inventar palavra que não foi
dita** e **nunca perder um caractere que muda o sentido**. Por isso a correção
é sempre tipográfica ou de forma (espaço, maiúscula, caixa de sigla), nunca
semântica — o texto continua sendo o que a pessoa falou.
"""
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional

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

# Espaçamento de moeda e porcentagem, que o modelo erra com frequência.
_CURRENCY = re.compile(r"\bR\$\s*(\d)")
_PERCENT = re.compile(r"(\d)\s+%")
# Numeral seguido de unidade colada ("10km") ou separada demais ("10  km").
_UNIT_SPACING = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(km|kg|mg|ml|cm|mm|gb|mb|kb|tb|hz|khz|w|kw)\b", re.I)

# Abreviações que terminam em ponto sem encerrar a frase. Sem esta lista,
# "Dr. Marcela explicou" viraria duas frases e o clique no trecho levaria o
# áudio para o lugar errado.
ABBREVIATIONS = frozenset("""
sr sra srta dr dra profa prof exmo exma ilmo eng arq adv
av r al rod km n nº no cep cx ltda me epp sa cia
etc ex obs pag pags fl fls art arts inc par cap
seg ter qua qui sex sab dom jan fev mar abr mai jun jul ago set out nov dez
""".split())
_ABBREV_END = re.compile(r"(?:^|[\s(\[\"'])([\wáàâãéêíóôõúüç]{1,5})\.$", re.IGNORECASE)

# Frases que o Whisper inventa sobre silêncio ou música em vídeos brasileiros.
_HALLUCINATION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^legendas?\s+(pela|por)\s+comunidade.*$",
        r"^amara\.org\.?$",
        r"^legendado\s+por.*$",
        r"^tradu(ção|zido)\s+(e\s+legendas?\s+)?por.*$",
        r"^revis(ão|ado)\s+por.*$",
        r"^subtitles?\s+(by|provided).*$",
        r"^transcri(ption|ção)\s+by.*$",
        r"^(se\s+)?inscreva-?\s*se\s+no\s+canal.*$",
        r"^ative\s+o\s+sininho.*$",
        r"^deixe\s+seu\s+like.*$",
        r"^até\s+(a\s+|o\s+)?próximo\s+vídeo[.!]?$",
        r"^obrigad[oa]\s+por\s+assistir[.!]?$",
        r"^\[?\s*(música|musica|music|aplausos|risos|silêncio)\s*\]?[.!]?$",
        r"^\W*$",
    )
]

# Marcadores de hesitação removidos apenas quando o usuário pede.
_FILLER_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(é|eh|ééé+|ãã+|hum+|uhum|ahn+)\b[,]?\s*",
        r"\b(né|tá|tipo assim|quer dizer|digamos assim)\b[,]?\s*",
        r"\bou seja,?\s+ou seja\b",
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
    text = _CURRENCY.sub(r"R$ \1", text)
    text = _PERCENT.sub(r"\1%", text)
    text = _UNIT_SPACING.sub(lambda m: f"{m.group(1)} {m.group(2).lower()}", text)
    text = _MULTI_SPACE.sub(" ", text)
    return text.strip()


def ends_sentence(token: str) -> bool:
    """Diz se um token encerra a frase de verdade.

    Ponto de abreviação ("Dr."), de enumeração ("1.") e de inicial ("J.") são
    os três casos que enganam qualquer separador ingênuo em português.
    """
    token = token.strip()
    if not token.endswith(_STRONG_PUNCT):
        return False
    if not token.endswith("."):
        return True
    if re.fullmatch(r"\d+\.", token):
        return False
    match = _ABBREV_END.search(token)
    if match:
        word = _strip_accents_lower(match.group(1))
        if word in ABBREVIATIONS or len(word) == 1:
            return False
    return True


def capitalize_sentences(text: str) -> str:
    """Garante inicial maiúscula no começo e depois de cada ponto final."""
    if not text:
        return ""
    rebuilt: List[str] = []
    capitalize_next = True
    for part in _SENTENCE_SPLIT.split(text):
        part = part.strip()
        if not part:
            continue
        # Não mexe em siglas já em caixa alta (ex.: "CNPJ do cliente").
        if capitalize_next and part[0].islower():
            part = part[0].upper() + part[1:]
        # Depois de uma abreviação a frase continua: a próxima palavra não
        # deve ganhar maiúscula só por ter vindo depois de um ponto.
        capitalize_next = ends_sentence(part.split()[-1]) if part.split() else True
        rebuilt.append(part)
    return " ".join(rebuilt)


def apply_vocabulary(text: str, terms: Iterable[str]) -> str:
    """Restaura a grafia exata dos termos informados pelo usuário.

    O parâmetro `hotwords` faz o modelo *esperar* a palavra, mas ele ainda a
    escreve com a caixa que achar melhor: "siscomex", "Siscomex". Aqui a forma
    digitada pelo usuário vence, comparando sem acento e sem caixa.
    """
    cleaned = [t.strip() for t in terms if t and t.strip()]
    if not cleaned or not text:
        return text

    for term in sorted(cleaned, key=len, reverse=True):
        pattern = re.compile(
            r"\b" + r"\s+".join(re.escape(part) for part in term.split()) + r"\b",
            re.IGNORECASE,
        )
        # A comparação ignora acento; a substituição devolve o termo original.
        if pattern.search(text):
            text = pattern.sub(lambda _m, value=term: value, text)
            continue
        folded_term = _strip_accents_lower(term)
        folded_text = _strip_accents_lower(text)
        start = folded_text.find(folded_term)
        while start != -1:
            before = folded_text[start - 1] if start else " "
            after_index = start + len(folded_term)
            after = folded_text[after_index] if after_index < len(folded_text) else " "
            if not before.isalnum() and not after.isalnum():
                text = text[:start] + term + text[after_index:]
                folded_text = _strip_accents_lower(text)
            start = folded_text.find(folded_term, start + max(1, len(folded_term)))
    return text


def strip_fillers(text: str) -> str:
    """Remove vícios de linguagem quando o usuário pede um texto enxuto."""
    if not text:
        return ""
    for pattern in _FILLER_PATTERNS:
        text = pattern.sub(" ", text)
    text = _MULTI_SPACE.sub(" ", text).strip()
    text = re.sub(r"^\s*[,;]\s*", "", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return capitalize_sentences(text)


def is_hallucination(text: str, no_speech_prob: float = 0.0, avg_logprob: float = 0.0) -> bool:
    """Detecta os bordões que o modelo inventa sobre silêncio ou música."""
    stripped = text.strip()
    if not stripped:
        return True
    for pattern in _HALLUCINATION_PATTERNS:
        if pattern.match(stripped):
            return True
    # Uma única palavra repetida ocupando o trecho inteiro é laço, não fala.
    words = _strip_accents_lower(re.sub(r"[^\w\s]", "", stripped)).split()
    if len(words) >= 6 and len(set(words)) <= 2:
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
        # A confiança do trecho original não vale para um pedaço dele; quando
        # há probabilidade por palavra, a média local é a medida honesta.
        probabilities = [w.get("probability") for w in words if w.get("probability") is not None]
        if probabilities:
            piece["confidence"] = round(sum(probabilities) / len(probabilities), 3)
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

            closes = ends_sentence(token)
            too_long = duration >= max_duration or length >= max_chars
            # Sem ponto final à vista: corta na vírgula, ou à força se o trecho
            # já ficou longo demais para servir de ponto de navegação.
            forced = too_long and (token.endswith(_SOFT_PUNCT) or duration >= max_duration * 1.8)

            if closes or forced:
                emit(seg, buffer)
                buffer, length = [], 0

        if buffer:
            emit(seg, buffer)

    return result


def build_paragraphs(
    segments: List[Dict[str, Any]], pause: float = 1.2, max_sentences: int = 5
) -> List[str]:
    """Agrupa os segmentos em parágrafos legíveis, quebrando em pausas longas.

    Quando há falantes identificados, a troca de voz também quebra o parágrafo:
    misturar duas pessoas no mesmo bloco tornaria o texto ilegível.
    """
    paragraphs: List[str] = []
    buffer: List[str] = []
    sentences_in_buffer = 0
    prev_end: Optional[float] = None
    prev_speaker: Optional[str] = None

    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue
        gap = 0.0 if prev_end is None else seg["start"] - prev_end
        speaker = seg.get("speaker")
        closed = bool(buffer) and ends_sentence(buffer[-1].rstrip().split()[-1] if buffer[-1].strip() else "")
        changed_speaker = speaker is not None and prev_speaker is not None and speaker != prev_speaker

        if buffer and (changed_speaker or (closed and (gap >= pause or sentences_in_buffer >= max_sentences))):
            paragraphs.append(" ".join(buffer))
            buffer = []
            sentences_in_buffer = 0

        buffer.append(text)
        if text.rstrip().split() and ends_sentence(text.rstrip().split()[-1]):
            sentences_in_buffer += 1
        prev_end = seg["end"]
        prev_speaker = speaker

    if buffer:
        paragraphs.append(" ".join(buffer))
    return [capitalize_sentences(clean_text(p)) for p in paragraphs if p.strip()]


def build_dialogue(segments: List[Dict[str, Any]], pause: float = 1.2) -> List[Dict[str, Any]]:
    """Mesmo agrupamento dos parágrafos, mas preservando quem falou cada bloco."""
    blocks: List[Dict[str, Any]] = []
    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue
        speaker = seg.get("speaker")
        last = blocks[-1] if blocks else None
        same_voice = last is not None and last["speaker"] == speaker
        short_gap = last is not None and seg["start"] - last["end"] < pause * 3
        if same_voice and short_gap and len(last["texto"]) < 900:
            last["texto"] = f"{last['texto']} {text}".strip()
            last["end"] = seg["end"]
            continue
        blocks.append({
            "speaker": speaker,
            "speaker_id": seg.get("speaker_id", 0),
            "start": seg["start"],
            "end": seg["end"],
            "start_str": seg.get("start_str", seconds_to_short(seg["start"])),
            "texto": text,
        })
    for block in blocks:
        block["texto"] = capitalize_sentences(clean_text(block["texto"]))
    return blocks


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


def seconds_to_ass(seconds: float) -> str:
    """Formato H:MM:SS.cc exigido pelo Advanced SubStation Alpha."""
    total_cs = int(round(max(0.0, float(seconds)) * 100))
    hours, remainder = divmod(total_cs, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    secs, centis = divmod(remainder, 100)
    return f"{hours:d}:{minutes:02d}:{secs:02d}.{centis:02d}"


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


def _cue_from_words(words: List[Dict[str, Any]], speaker: Optional[str] = None) -> Dict[str, Any]:
    text = clean_text(" ".join(w["word"].strip() for w in words))
    return {"start": words[0]["start"], "end": words[-1]["end"], "text": text, "speaker": speaker}


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
        speaker = seg.get("speaker")
        words = seg.get("words") or []
        if not words:
            cues.append({"start": seg["start"], "end": seg["end"],
                         "text": seg["text"], "speaker": speaker})
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
                cues.append(_cue_from_words(current, speaker))
                current, current_len = [], 0
                added = len(token)

            current.append(word)
            current_len += added

            # Fecha em ponto final quando já há texto suficiente para o bloco.
            if ends_sentence(token) and current_len >= budget * 0.5:
                cues.append(_cue_from_words(current, speaker))
                current, current_len = [], 0

        if current:
            cues.append(_cue_from_words(current, speaker))

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
        entry = {
            "id": len(result) + 1,
            "start": cue["start"],
            "end": cue["end"],
            "lines": wrap_balanced(text, max_chars, max_lines),
            "text": text,
        }
        if cue.get("speaker"):
            entry["speaker"] = cue["speaker"]
        entry["cps"] = round(len(text) / max(0.001, cue["end"] - cue["start"]), 1)
        result.append(entry)
    return result


def chars_per_second(cue: Dict[str, Any]) -> float:
    """Caracteres por segundo — acima de ~21 a legenda fica ilegível."""
    return len(cue["text"]) / max(0.001, cue["end"] - cue["start"])


def subtitle_report(cues: List[Dict[str, Any]], max_cps: float = config.SUB_MAX_CPS,
                    max_chars: int = config.SUB_MAX_CHARS_PER_LINE) -> Dict[str, Any]:
    """Controle de qualidade das legendas, no vocabulário de quem legenda.

    Nenhum destes achados impede o uso do arquivo; servem para o usuário saber
    onde vale a pena reduzir o texto antes de publicar o vídeo.
    """
    rapidas = [c["id"] for c in cues if chars_per_second(c) > max_cps]
    longas = [c["id"] for c in cues if any(len(line) > max_chars for line in c.get("lines", []))]
    sobrepostas = [
        cues[i + 1]["id"] for i in range(len(cues) - 1)
        if cues[i + 1]["start"] < cues[i]["end"] - 0.001
    ]
    return {
        "blocos": len(cues),
        "acima_do_cps": rapidas[:50],
        "linhas_longas": longas[:50],
        "sobrepostas": sobrepostas[:50],
        "cps_medio": round(
            sum(chars_per_second(c) for c in cues) / len(cues), 1
        ) if cues else 0.0,
    }
