"""Leitura automática da transcrição: resumo, temas, capítulos e pendências.

Tudo aqui é estatístico e roda offline, sem modelo de linguagem: uma hora de
reunião é analisada em menos de um segundo e o resultado é determinístico —
rodar duas vezes dá exatamente o mesmo texto.

O resumo usa **TextRank**: as frases viram vetores TF-IDF, a semelhança entre
elas forma um grafo e o PageRank encontra as frases que melhor representam o
conjunto. A vantagem sobre "pegar as primeiras frases" é grande em fala
espontânea, onde o começo costuma ser cumprimento e ajuste de microfone.

Os capítulos saem da mesma matemática por outro ângulo: onde o vocabulário
muda bruscamente entre dois blocos vizinhos, o assunto mudou.
"""
import math
import re
import unicodedata
from typing import Any, Dict, List, Optional, Sequence, Tuple

import config

# --- Vocabulário de apoio ----------------------------------------------

# Palavras sem conteúdo temático. Uma lista curada rende resumos melhores que
# uma lista genérica: aqui entram também os vícios de fala do português falado,
# que aparecem centenas de vezes numa reunião e não dizem nada sobre o assunto.
STOPWORDS = frozenset("""
a as o os um uma uns umas de do da dos das em no na nos nas por pelo pela pelos pelas
para pra pro com sem sob sobre entre ate após ante desde contra e ou mas porem contudo
todavia entretanto porque pois porquanto que quem qual quais quando onde como quanto
quantos quantas se ja nao sim tambem so apenas mesmo mesma mesmos mesmas outro outra
outros outras algum alguma alguns algumas nenhum nenhuma todo toda todos todas cada
qualquer varios varias muito muita muitos muitas pouco pouca poucos poucas mais menos
tanto tanta tantos tantas eu tu ele ela nos vos eles elas me te se lhe nos vos lhes
meu minha meus minhas teu tua teus tuas seu sua seus suas nosso nossa nossos nossas
este esta estes estas esse essa esses essas aquele aquela aqueles aquelas isto isso
aquilo ser estar ter haver ir vir fazer poder dever querer sou es e somos sao era eram
foi foram sera serao seja sejam sendo sido esta estao estava estavam esteve estiveram
tem tem temos tem tinha tinham teve tiveram tenha tenham tendo tido ha havia houve
vai vao vamos ia iam foi foram indo va vao fez fizeram faz fazem fazendo feito pode
podem podia podiam pude pode posso deve devem devia deviam quer querem queria queriam
la ai aqui ali cá agora hoje ontem amanha entao ainda depois antes sempre nunca talvez
bem mal muito pouco bastante quase certo certa acho achei tipo assim entao ne ta tah
ok oh ah eh uh hum hein olha veja vejam entende entendeu sabe sabes cara gente pessoal
coisa coisas negocio jeito parte vez vezes ano anos mes meses dia dias hora horas
minuto minutos segundo segundos sr sra dr dra
""".split())

# Verbos e locuções que sinalizam compromisso assumido em voz alta.
_COMMIT_PATTERNS = [
    (re.compile(r"\bfic(ou|amos|o)\s+de\b", re.I), 3.0),
    (re.compile(r"\b(vou|vamos|irei|iremos)\s+\w+(ar|er|ir)\b", re.I), 2.5),
    (re.compile(r"\b(precis|necessit)(a|o|amos|am)\b", re.I), 2.0),
    (re.compile(r"\b(tem|temos|tenho|terá|teremos)\s+que\b", re.I), 2.0),
    (re.compile(r"\b(envi|mand|manda|marc|agend|confirm|verific|revis|prepar|valid)\w*\b", re.I), 1.5),
    (re.compile(r"\b(prazo|deadline|entrega|até\s+(segunda|terça|quarta|quinta|sexta|sábado|domingo|amanhã|hoje|dia\s+\d+))\b", re.I), 2.5),
    (re.compile(r"\b(pendente|pendência|responsáve(l|is)|encarregad)\w*\b", re.I), 2.0),
    (re.compile(r"\b(fazer|criar|abrir|fechar|resolver|corrigir|ajustar)\b", re.I), 1.0),
]

# Marcadores de hesitação: úteis como métrica de fluência da fala.
_FILLERS = ("né", "tipo", "assim", "então", "aí", "hum", "ãã", "éé", "tá", "sabe")

_TOKEN = re.compile(r"[0-9a-zà-öø-ÿ]{2,}", re.IGNORECASE)
_SMALL_WORDS = frozenset("de da do das dos e em no na nos nas a o as os para com por um uma".split())


def _fold(text: str) -> str:
    """Minúsculas sem acento — a chave usada para comparar palavras."""
    norm = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in norm if unicodedata.category(c) != "Mn")


def tokenize(text: str) -> List[str]:
    """Palavras de conteúdo: sem pontuação, sem stopword, sem número solto."""
    words = []
    for match in _TOKEN.findall(_fold(text)):
        if match in STOPWORDS or len(match) < 3 or match.isdigit():
            continue
        words.append(match)
    return words


def _title_case(text: str) -> str:
    """Capitalização de título em português: preposições curtas ficam minúsculas."""
    parts = text.split()
    out = []
    for index, part in enumerate(parts):
        low = part.lower()
        out.append(low if index and low in _SMALL_WORDS else (low[:1].upper() + low[1:]))
    return " ".join(out)


# --- Núcleo TF-IDF ------------------------------------------------------

def _vectors(units: Sequence[str]) -> Tuple[List[Dict[str, float]], Dict[str, float]]:
    """Vetores TF-IDF normalizados de cada unidade de texto, e o IDF global."""
    total = max(1, len(units))
    frequencies: List[Dict[str, int]] = []
    document_freq: Dict[str, int] = {}

    for unit in units:
        counts: Dict[str, int] = {}
        for word in tokenize(unit):
            counts[word] = counts.get(word, 0) + 1
        frequencies.append(counts)
        for word in counts:
            document_freq[word] = document_freq.get(word, 0) + 1

    idf = {
        word: math.log(1.0 + total / freq)
        for word, freq in document_freq.items()
    }

    vectors: List[Dict[str, float]] = []
    for counts in frequencies:
        vector = {
            word: (1.0 + math.log(count)) * idf.get(word, 0.0)
            for word, count in counts.items()
        }
        norm = math.sqrt(sum(value * value for value in vector.values())) or 1.0
        vectors.append({word: value / norm for word, value in vector.items()})
    return vectors, idf


def _cosine(left: Dict[str, float], right: Dict[str, float]) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(word, 0.0) for word, value in left.items())


def _textrank(vectors: Sequence[Dict[str, float]], damping: float = 0.85,
              iterations: int = 32) -> List[float]:
    """PageRank sobre o grafo de semelhança entre frases.

    A matriz é montada por índice invertido em vez de comparar todos com
    todos: duas frases sem nenhuma palavra em comum têm semelhança zero e nem
    precisam ser visitadas. Numa transcrição de duas horas isso é a diferença
    entre milissegundos e minutos.
    """
    n = len(vectors)
    if n <= 1:
        return [1.0] * n

    postings: Dict[str, List[int]] = {}
    for index, vector in enumerate(vectors):
        for word in vector:
            postings.setdefault(word, []).append(index)

    # Termos presentes em quase todo lugar não separam nada e explodiriam o
    # custo quadrático; o corte por frequência documental resolve os dois.
    limit = max(2, int(n * 0.35))
    weights: List[Dict[int, float]] = [dict() for _ in range(n)]
    for word, holders in postings.items():
        if len(holders) > limit:
            continue
        for position, i in enumerate(holders):
            value_i = vectors[i][word]
            for j in holders[position + 1:]:
                contribution = value_i * vectors[j][word]
                weights[i][j] = weights[i].get(j, 0.0) + contribution
                weights[j][i] = weights[j].get(i, 0.0) + contribution

    totals = [sum(row.values()) for row in weights]
    scores = [1.0 / n] * n
    for _ in range(iterations):
        updated = [(1.0 - damping) / n] * n
        for i, row in enumerate(weights):
            if totals[i] <= 0:
                continue
            share = damping * scores[i] / totals[i]
            for j, weight in row.items():
                updated[j] += share * weight
        scores = updated
    return scores


# --- Resumo -------------------------------------------------------------

def summarize(sentences: Sequence[Dict[str, Any]], max_sentences: int) -> List[Dict[str, Any]]:
    """Escolhe as frases mais representativas, sem repetir conteúdo.

    Depois do TextRank vem um passo de diversidade (MMR): uma frase só entra
    se acrescentar algo em relação às já escolhidas. Sem isso o resumo de uma
    reunião vira cinco versões da mesma frase, porque quem fala repete.
    """
    usable = [s for s in sentences if len(s.get("text", "").split()) >= 5]
    if not usable:
        return []
    if len(usable) <= max_sentences:
        return [dict(s) for s in usable]

    texts = [s["text"] for s in usable]
    vectors, _ = _vectors(texts)
    ranks = _textrank(vectors)

    # Frases muito curtas ganham score alto por acidente (poucos termos, todos
    # raros); o fator de comprimento corrige isso sem descartá-las.
    scored = []
    for index, (sentence, rank) in enumerate(zip(usable, ranks)):
        words = len(sentence["text"].split())
        length_factor = min(1.0, words / 14.0) * (0.7 if words > 45 else 1.0)
        scored.append((rank * length_factor, index))
    scored.sort(reverse=True)

    chosen: List[int] = []
    for _score, index in scored:
        if len(chosen) >= max_sentences:
            break
        if any(_cosine(vectors[index], vectors[other]) > 0.55 for other in chosen):
            continue
        chosen.append(index)

    chosen.sort()
    return [dict(usable[index]) for index in chosen]


# --- Palavras-chave -----------------------------------------------------

def keywords(sentences: Sequence[Dict[str, Any]], top: int) -> List[Dict[str, Any]]:
    """Termos e expressões que caracterizam o assunto.

    Além das palavras isoladas, pares que aparecem sempre juntos ("nota
    fiscal", "prazo de entrega") viram uma entrada só — que é como uma pessoa
    descreveria o tema.
    """
    texts = [s.get("text", "") for s in sentences]
    if not texts:
        return []

    _vecs, idf = _vectors(texts)
    counts: Dict[str, int] = {}
    originals: Dict[str, str] = {}
    bigrams: Dict[str, int] = {}
    bigram_original: Dict[str, str] = {}

    for text in texts:
        raw_words = _TOKEN.findall(text)
        folded = [_fold(w) for w in raw_words]
        content = [
            (fold, raw) for fold, raw in zip(folded, raw_words)
            if fold not in STOPWORDS and len(fold) >= 3 and not fold.isdigit()
        ]
        for fold, raw in content:
            counts[fold] = counts.get(fold, 0) + 1
            originals.setdefault(fold, raw)
        for i in range(len(content) - 1):
            key = f"{content[i][0]} {content[i + 1][0]}"
            bigrams[key] = bigrams.get(key, 0) + 1
            bigram_original.setdefault(key, f"{content[i][1]} {content[i + 1][1]}")

    scores: Dict[str, Tuple[float, int, str]] = {}
    for word, count in counts.items():
        weight = (1.0 + math.log(count)) * idf.get(word, 0.5)
        scores[word] = (weight, count, originals.get(word, word))

    # Um par só substitui suas partes se de fato aparecer quase sempre junto.
    for pair, count in bigrams.items():
        if count < 3:
            continue
        left, right = pair.split(" ", 1)
        cohesion = count / max(1, min(counts.get(left, 1), counts.get(right, 1)))
        if cohesion < 0.6:
            continue
        weight = (1.0 + math.log(count)) * (idf.get(left, 0.5) + idf.get(right, 0.5)) * 0.75
        scores[pair] = (weight, count, bigram_original.get(pair, pair))
        scores.pop(left, None)
        scores.pop(right, None)

    ranked = sorted(scores.items(), key=lambda item: item[1][0], reverse=True)[:top]
    peak = ranked[0][1][0] if ranked else 1.0
    return [
        {
            "termo": _title_case(display) if " " in display else display,
            "ocorrencias": count,
            "peso": round(weight / peak, 3),
        }
        for _key, (weight, count, display) in ranked
    ]


# --- Capítulos ----------------------------------------------------------

def chapters(sentences: Sequence[Dict[str, Any]], min_seconds: float) -> List[Dict[str, Any]]:
    """Divide a transcrição em capítulos onde o vocabulário muda de eixo."""
    usable = [s for s in sentences if s.get("text")]
    if len(usable) < 12:
        return []

    # Blocos de tamanho fixo dão uma série temporal estável para comparar.
    block_size = max(3, len(usable) // 60 + 3)
    blocks: List[List[Dict[str, Any]]] = [
        usable[i:i + block_size] for i in range(0, len(usable), block_size)
    ]
    if len(blocks) < 3:
        return []

    vectors, _ = _vectors([" ".join(s["text"] for s in block) for block in blocks])
    similarities = [_cosine(vectors[i], vectors[i + 1]) for i in range(len(blocks) - 1)]
    average = sum(similarities) / len(similarities)
    spread = math.sqrt(sum((s - average) ** 2 for s in similarities) / len(similarities))
    cutoff = average - 0.45 * spread

    boundaries = [0]
    for index, value in enumerate(similarities):
        if value > cutoff:
            continue
        # Só um mínimo local conta: uma queda isolada é troca de assunto,
        # uma sequência baixa é só uma conversa dispersa.
        before = similarities[index - 1] if index else 1.0
        after = similarities[index + 1] if index + 1 < len(similarities) else 1.0
        if value > before or value > after:
            continue
        start_time = blocks[index + 1][0]["start"]
        if start_time - blocks[boundaries[-1]][0]["start"] < min_seconds:
            continue
        boundaries.append(index + 1)

    if len(boundaries) < 2:
        return []

    import ptbr  # importado aqui para evitar ciclo na carga dos módulos

    result: List[Dict[str, Any]] = []
    for position, first_block in enumerate(boundaries):
        last_block = boundaries[position + 1] if position + 1 < len(boundaries) else len(blocks)
        chunk = [s for block in blocks[first_block:last_block] for s in block]
        if not chunk:
            continue
        terms = keywords(chunk, 4)
        title = " · ".join(term["termo"] for term in terms[:3]) or "Trecho"
        result.append({
            "id": len(result) + 1,
            "start": round(chunk[0]["start"], 2),
            "end": round(chunk[-1]["end"], 2),
            "start_str": ptbr.seconds_to_short(chunk[0]["start"]),
            "titulo": _title_case(title) if " · " not in title else title,
            "palavras": [term["termo"] for term in terms],
            "frases": len(chunk),
            "abertura": chunk[0]["text"][:160],
        })
    return result


# --- Pendências e perguntas ---------------------------------------------

def action_items(sentences: Sequence[Dict[str, Any]], limit: int = 12) -> List[Dict[str, Any]]:
    """Frases que soam como compromisso assumido: "ficou de", "vou enviar"..."""
    found: List[Tuple[float, Dict[str, Any]]] = []
    for sentence in sentences:
        text = sentence.get("text", "")
        if len(text.split()) < 4:
            continue
        score = sum(weight for pattern, weight in _COMMIT_PATTERNS if pattern.search(text))
        if score < 3.0:
            continue
        found.append((score, {
            "texto": text.strip(),
            "start": sentence.get("start", 0.0),
            "start_str": sentence.get("start_str", ""),
            "falante": sentence.get("speaker"),
            "peso": round(score, 1),
        }))
    found.sort(key=lambda item: item[0], reverse=True)
    selected = [item[1] for item in found[:limit]]
    selected.sort(key=lambda item: item["start"])
    return selected


def questions(sentences: Sequence[Dict[str, Any]], limit: int = 15) -> List[Dict[str, Any]]:
    """Perguntas feitas durante a gravação, na ordem em que aparecem."""
    result = []
    for sentence in sentences:
        text = sentence.get("text", "").strip()
        if not text.endswith("?") or len(text.split()) < 3:
            continue
        result.append({
            "texto": text,
            "start": sentence.get("start", 0.0),
            "start_str": sentence.get("start_str", ""),
            "falante": sentence.get("speaker"),
        })
        if len(result) >= limit:
            break
    return result


# --- Estatísticas -------------------------------------------------------

def statistics(sentences: Sequence[Dict[str, Any]], duration: float,
               low_confidence: float = config.LOW_CONFIDENCE) -> Dict[str, Any]:
    """Números sobre o ritmo e a qualidade da fala transcrita."""
    words: List[str] = []
    speech = 0.0
    fillers = 0
    low = 0
    longest_pause = 0.0
    previous_end: Optional[float] = None

    for sentence in sentences:
        text = sentence.get("text", "")
        words.extend(text.split())
        speech += max(0.0, sentence.get("end", 0.0) - sentence.get("start", 0.0))
        if sentence.get("confidence", 1.0) < low_confidence:
            low += 1
        folded = _fold(text)
        fillers += sum(folded.count(_fold(f)) for f in _FILLERS)
        if previous_end is not None:
            longest_pause = max(longest_pause, sentence.get("start", 0.0) - previous_end)
        previous_end = sentence.get("end", previous_end)

    total_words = len(words)
    unique = len({_fold(w.strip(".,;:!?…\"'()")) for w in words if w.strip(".,;:!?…\"'()")})
    minutes = speech / 60.0

    return {
        "palavras": total_words,
        "frases": len(sentences),
        "vocabulario_unico": unique,
        # Riqueza lexical: proporção de palavras diferentes. Fala espontânea
        # fica entre 0,25 e 0,45; texto lido sobe bastante disso.
        "riqueza_lexical": round(unique / total_words, 3) if total_words else 0.0,
        "palavras_por_minuto": round(total_words / minutes) if minutes > 0.1 else 0,
        "tempo_fala_s": round(speech, 1),
        "tempo_silencio_s": round(max(0.0, duration - speech), 1),
        "proporcao_fala": round(speech / duration, 3) if duration > 0 else 0.0,
        "maior_pausa_s": round(longest_pause, 1),
        "vicios_de_linguagem": fillers,
        "trechos_baixa_confianca": low,
    }


def speaker_statistics(sentences: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Tempo de fala, ritmo e vocabulário característico de cada falante."""
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for sentence in sentences:
        name = sentence.get("speaker")
        if not name:
            continue
        grouped.setdefault(name, []).append(sentence)
    if len(grouped) < 2:
        return []

    result = []
    for name, items in grouped.items():
        speech = sum(max(0.0, s["end"] - s["start"]) for s in items)
        words = sum(len(s.get("text", "").split()) for s in items)
        result.append({
            "nome": name,
            "trechos": len(items),
            "palavras": words,
            "tempo_s": round(speech, 1),
            "palavras_por_minuto": round(words / (speech / 60.0)) if speech > 6 else 0,
            "termos": [term["termo"] for term in keywords(items, 5)],
        })
    result.sort(key=lambda item: item["tempo_s"], reverse=True)
    return result


# --- Fachada ------------------------------------------------------------

def analyze(
    sentences: Sequence[Dict[str, Any]],
    duration: float = 0.0,
    max_sentences: int = config.SUMMARY_SENTENCES,
    top_keywords: int = config.KEYWORDS_TOP,
    chapter_min: float = config.CHAPTER_MIN_SECONDS,
) -> Dict[str, Any]:
    """Roda a análise inteira e devolve o bloco pronto para a interface."""
    sentences = [s for s in sentences if s.get("text", "").strip()]
    if not sentences:
        return {}

    resumo = summarize(sentences, max_sentences)
    return {
        "resumo": [
            {
                "texto": item["text"],
                "start": item.get("start", 0.0),
                "start_str": item.get("start_str", ""),
                "falante": item.get("speaker"),
            }
            for item in resumo
        ],
        "resumo_texto": " ".join(item["text"] for item in resumo),
        "palavras_chave": keywords(sentences, top_keywords),
        "capitulos": chapters(sentences, chapter_min),
        "pendencias": action_items(sentences),
        "perguntas": questions(sentences),
        "estatisticas": statistics(sentences, duration),
        "por_falante": speaker_statistics(sentences),
    }
