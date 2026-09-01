"""Fixtures compartilhadas.

Os testes rodam sem modelo de IA e sem FFmpeg: tudo que depende de rede ou de
binário externo fica de fora, e o que sobra — limpeza de texto, legendagem,
análise, exportação, índice — é justamente onde os erros silenciosos moram.
"""
import os
import sys

import pytest

# Permite `import ptbr` etc. com os testes rodando de qualquer diretório.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _palavras(texto: str, inicio: float, passo: float = 0.4):
    palavras = []
    tempo = inicio
    for palavra in texto.split():
        palavras.append({
            "word": palavra,
            "start": round(tempo, 3),
            "end": round(tempo + passo, 3),
            "probability": 0.94,
        })
        tempo += passo
    return palavras


@pytest.fixture
def segmentos():
    """Cinco trechos com tempo por palavra, como o motor devolve."""
    import ptbr

    falas = [
        (0.0, "Bom dia a todos, vamos começar a reunião de hoje."),
        (4.0, "O contrato da prefeitura precisa ser revisado até sexta-feira."),
        (9.5, "Quem ficou de enviar a nota fiscal para o setor financeiro?"),
        (14.0, "Eu vou enviar a nota fiscal ainda hoje, sem problema."),
        (18.5, "Perfeito. Então o prazo do contrato fica mantido para sexta."),
    ]
    saida = []
    for indice, (inicio, texto) in enumerate(falas, start=1):
        palavras = _palavras(texto, inicio)
        saida.append({
            "id": indice,
            "start": palavras[0]["start"],
            "end": palavras[-1]["end"],
            "start_str": ptbr.seconds_to_short(palavras[0]["start"]),
            "end_str": ptbr.seconds_to_short(palavras[-1]["end"]),
            "text": texto,
            "words": palavras,
            "confidence": 0.91,
            "no_speech_prob": 0.02,
        })
    return saida


@pytest.fixture
def resultado(segmentos):
    """Um resultado completo, no formato que os exportadores esperam."""
    import analysis
    import ptbr

    cues = ptbr.build_cues(segmentos)
    paragrafos = ptbr.build_paragraphs(segmentos)
    texto = ptbr.capitalize_sentences(" ".join(s["text"] for s in segmentos))
    return {
        "success": True,
        "duration": 24.0,
        "speech_duration": 22.0,
        "language": "pt",
        "language_probability": 0.99,
        "model": "large-v3-turbo",
        "profile": "equilibrado",
        "device": "cpu",
        "compute_type": "int8",
        "segments": segmentos,
        "cues": cues,
        "paragraphs": paragrafos,
        "dialogue": [],
        "full_text": "\n\n".join(paragrafos),
        "plain_text": texto,
        "word_count": len(texto.split()),
        "avg_confidence": 0.91,
        "discarded_segments": 0,
        "audio_preview": None,
        "waveform": [0.2, 0.8, 0.5],
        "media_info": {"container": "mov,mp4", "audio_codec": "aac", "channels": 2, "sample_rate": 48000},
        "diarization": None,
        "analysis": analysis.analyze(segmentos, 24.0),
        "legendas_qa": ptbr.subtitle_report(cues),
        "vocabulary": [],
        "processing_seconds": 6.0,
        "speed_factor": 4.0,
    }
