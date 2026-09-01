"""Diarização e leitura de áudio, sobre um WAV sintético.

Duas "vozes" são fabricadas com envelopes espectrais bem diferentes — o que
uma voz grave e uma aguda fazem na prática. Não substitui um teste com áudio
real, mas garante que o caminho inteiro (ler WAV em blocos, extrair MFCC,
clusterizar, rotular) funciona e devolve rótulos coerentes.
"""
import math
import wave

import pytest

np = pytest.importorskip("numpy")

import diarize  # noqa: E402
import media  # noqa: E402
import ptbr  # noqa: E402

TAXA = 16000
TURNO = 2.0
TURNOS = 10


def _voz(frequencia: float, harmonicos, duracao: float):
    """Onda periódica com um conjunto próprio de harmônicos = um timbre."""
    t = np.linspace(0, duracao, int(TAXA * duracao), endpoint=False)
    onda = np.zeros_like(t)
    for ordem, peso in harmonicos:
        onda += peso * np.sin(2 * math.pi * frequencia * ordem * t)
    # Envelope de sílabas: fala não é um tom contínuo.
    onda *= 0.6 + 0.4 * np.sin(2 * math.pi * 4.0 * t)
    return onda / (np.abs(onda).max() or 1.0)


@pytest.fixture
def audio(tmp_path):
    """WAV com dois falantes se alternando a cada dois segundos."""
    grave = [(1, 1.0), (2, 0.7), (3, 0.5), (4, 0.2), (7, 0.05)]
    aguda = [(1, 0.3), (2, 0.2), (5, 0.9), (6, 0.8), (9, 0.6)]
    blocos = []
    verdade = []
    for indice in range(TURNOS):
        quem = indice % 2
        blocos.append(_voz(110.0 if quem == 0 else 240.0,
                           grave if quem == 0 else aguda, TURNO))
        verdade.append(quem)

    sinal = np.concatenate(blocos)
    ruido = np.random.default_rng(7).normal(0, 0.01, sinal.shape)
    amostras = np.clip((sinal + ruido) * 0.8, -1.0, 1.0)

    caminho = str(tmp_path / "vozes.wav")
    with wave.open(caminho, "wb") as arquivo:
        arquivo.setnchannels(1)
        arquivo.setsampwidth(2)
        arquivo.setframerate(TAXA)
        arquivo.writeframes((amostras * 32767).astype("<i2").tobytes())

    segmentos = []
    for indice in range(TURNOS):
        inicio = indice * TURNO
        segmentos.append({
            "id": indice + 1,
            "start": inicio,
            "end": inicio + TURNO,
            "start_str": ptbr.seconds_to_short(inicio),
            "end_str": ptbr.seconds_to_short(inicio + TURNO),
            "text": f"Trecho número {indice + 1} desta gravação de teste.",
            "confidence": 0.9,
            "words": [],
        })
    return caminho, segmentos, verdade


class TestLeituraDeAudio:
    def test_informacoes_do_wav(self, audio):
        caminho, _segmentos, _verdade = audio
        info = media.wav_info(caminho)
        assert info["sample_rate"] == TAXA
        assert info["channels"] == 1
        assert info["duration"] == pytest.approx(TURNO * TURNOS, abs=0.01)

    def test_leitura_em_blocos_cobre_tudo(self, audio):
        caminho, _segmentos, _verdade = audio
        total = sum(bloco.size for bloco in media.read_wav_blocks(caminho, block_frames=5000))
        assert total == TAXA * TURNO * TURNOS

    def test_amostras_normalizadas(self, audio):
        caminho, _segmentos, _verdade = audio
        bloco = next(media.read_wav_blocks(caminho, block_frames=1000))
        assert bloco.dtype == np.float32
        assert np.abs(bloco).max() <= 1.0

    def test_forma_de_onda(self, audio):
        caminho, _segmentos, _verdade = audio
        picos = media.waveform_peaks(caminho, points=120)
        assert 100 <= len(picos) <= 120
        assert all(0.0 <= p <= 1.0 for p in picos)
        assert max(picos) > 0.5


class TestMfcc:
    def test_dimensoes(self, audio):
        caminho, _segmentos, _verdade = audio
        mfcc = diarize.compute_mfcc(caminho)
        assert mfcc is not None
        assert mfcc.shape[1] == diarize.N_MFCC
        esperado = TURNO * TURNOS * 1000 / diarize.HOP_MS
        assert abs(mfcc.shape[0] - esperado) < 20

    def test_arquivo_inexistente_nao_quebra(self, tmp_path):
        assert diarize.compute_mfcc(str(tmp_path / "nao_existe.wav")) is None


class TestDiarizacao:
    def test_identifica_duas_vozes(self, audio):
        caminho, segmentos, verdade = audio
        resumo = diarize.assign_speakers(caminho, segmentos)
        assert resumo is not None
        assert resumo["total"] == 2

    def test_rotula_todos_os_trechos(self, audio):
        caminho, segmentos, _verdade = audio
        diarize.assign_speakers(caminho, segmentos)
        assert all("speaker" in s and "speaker_id" in s for s in segmentos)

    def test_acerta_a_alternancia(self, audio):
        caminho, segmentos, verdade = audio
        diarize.assign_speakers(caminho, segmentos)
        rotulos = [s["speaker_id"] for s in segmentos]
        # O índice do grupo é arbitrário; o que importa é a partição.
        acertos = sum(1 for r, v in zip(rotulos, verdade) if r == v)
        assert max(acertos, len(verdade) - acertos) >= len(verdade) - 1

    def test_percentuais_somam_cem(self, audio):
        caminho, segmentos, _verdade = audio
        resumo = diarize.assign_speakers(caminho, segmentos)
        assert sum(f["percentual"] for f in resumo["falantes"]) == pytest.approx(100.0, abs=0.5)

    def test_sem_segmentos_devolve_none(self, audio):
        caminho, _segmentos, _verdade = audio
        assert diarize.assign_speakers(caminho, []) is None

    def test_turnos_agrupam_falas_seguidas(self, audio):
        caminho, segmentos, _verdade = audio
        diarize.assign_speakers(caminho, segmentos)
        turnos = diarize.build_turns(segmentos)
        assert len(turnos) <= len(segmentos)
        assert all(t["text"] for t in turnos)
