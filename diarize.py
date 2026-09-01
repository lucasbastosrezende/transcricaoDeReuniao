"""Separação de falantes (diarização) offline e sem dependências pesadas.

O caminho tradicional para "quem falou o quê" é o `pyannote.audio`: exige
PyTorch, download de modelos com token do Hugging Face e vários gigabytes de
disco. Isso contraria o objetivo do projeto — rodar em qualquer máquina, sem
conta em lugar nenhum — então aqui a diarização é feita à mão sobre o mesmo
WAV de 16 kHz que a transcrição já usa:

1. **MFCC** (coeficientes cepstrais em escala mel) calculados em streaming,
   com janela de 25 ms e passo de 20 ms. São a representação clássica do timbre
   de uma voz: capturam a forma do trato vocal e ignoram o conteúdo falado.
2. **Assinatura por trecho**: média e desvio-padrão dos coeficientes dentro de
   cada trecho já transcrito, normalizados. Média captura o timbre; desvio
   captura a variação de entonação, que também distingue pessoas.
3. **Clusterização aglomerativa** com ligação média e distância de cosseno. O
   número de falantes não é informado: o dendrograma é cortado quando a menor
   distância entre grupos ultrapassa o limiar configurado.

É uma aproximação honesta. Acerta bem "quantas vozes existem" e "quem falou
mais" em reuniões e entrevistas com microfone razoável; erra mais com vozes
parecidas, muita sobreposição de fala ou ruído alto. Nunca derruba a
transcrição: qualquer falha devolve `None` e o resto do sistema segue igual.
"""
import logging
import math
from typing import Any, Dict, List, Optional, Sequence

import config
import media

logger = logging.getLogger("diarize")

# Parâmetros do extrator. Passo de 20 ms (em vez dos 10 ms usuais em ASR)
# corta o custo pela metade sem perder nada: timbre não muda em 10 ms.
SAMPLE_RATE = 16000
FRAME_MS = 25
HOP_MS = 20
N_MELS = 32
N_MFCC = 20
FMIN = 60.0
FMAX = 7600.0
PRE_EMPHASIS = 0.97
# Acima disto a clusterização quadrática ficaria lenta; trechos excedentes
# entram depois, por proximidade ao centro do grupo mais parecido.
MAX_SEGMENTS_CLUSTER = 1200


def _numpy():
    try:
        import numpy as np

        return np
    except ImportError:  # pragma: no cover - ambiente sem numpy
        return None


# --- Extração de MFCC ---------------------------------------------------

def _mel_filterbank(np, n_filters: int, n_fft: int, sample_rate: int):
    """Banco de filtros triangulares igualmente espaçados na escala mel."""

    def hz_to_mel(hz):
        return 2595.0 * np.log10(1.0 + hz / 700.0)

    def mel_to_hz(mel):
        return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)

    n_bins = n_fft // 2 + 1
    mel_points = np.linspace(hz_to_mel(FMIN), hz_to_mel(min(FMAX, sample_rate / 2)), n_filters + 2)
    hz_points = mel_to_hz(mel_points)
    bins = np.floor((n_fft + 1) * hz_points / sample_rate).astype(int)
    bins = np.clip(bins, 0, n_bins - 1)

    bank = np.zeros((n_filters, n_bins), dtype=np.float32)
    for i in range(n_filters):
        left, center, right = bins[i], bins[i + 1], bins[i + 2]
        if center == left:
            center = min(left + 1, n_bins - 1)
        if right == center:
            right = min(center + 1, n_bins - 1)
        for k in range(left, center):
            bank[i, k] = (k - left) / max(1, center - left)
        for k in range(center, right):
            bank[i, k] = (right - k) / max(1, right - center)
    # Normalizar por largura de banda impede que os filtros agudos, mais
    # largos, dominem a energia total.
    widths = bank.sum(axis=1, keepdims=True)
    widths[widths == 0] = 1.0
    return bank / widths


def _dct_matrix(np, n_out: int, n_in: int):
    """DCT-II ortonormal, a transformada que descorrelaciona as bandas mel."""
    grid = np.arange(n_in, dtype=np.float32)
    matrix = np.cos(np.pi / n_in * (grid[None, :] + 0.5) * np.arange(n_out, dtype=np.float32)[:, None])
    matrix *= math.sqrt(2.0 / n_in)
    matrix[0] *= math.sqrt(0.5)
    return matrix.astype(np.float32)


def compute_mfcc(wav_path: str) -> Optional[Any]:
    """Devolve a matriz (n_quadros × N_MFCC) do arquivo, ou None se não der.

    O cálculo é feito em blocos para que um áudio de três horas não precise
    caber inteiro na memória: só o resultado final, muito menor, é acumulado.
    """
    np = _numpy()
    if np is None:
        return None

    frame_len = int(SAMPLE_RATE * FRAME_MS / 1000)
    hop_len = int(SAMPLE_RATE * HOP_MS / 1000)
    n_fft = 1
    while n_fft < frame_len:
        n_fft *= 2

    window = np.hamming(frame_len).astype(np.float32)
    bank = _mel_filterbank(np, N_MELS, n_fft, SAMPLE_RATE)
    dct = _dct_matrix(np, N_MFCC, N_MELS)

    blocks: List[Any] = []
    carry = np.zeros(0, dtype=np.float32)
    last_sample = 0.0

    try:
        for chunk in media.read_wav_blocks(wav_path, block_frames=SAMPLE_RATE * 30):
            data = np.concatenate((carry, chunk)) if carry.size else chunk
            if data.size < frame_len:
                carry = data
                continue

            # Pré-ênfase: realça as frequências altas, onde ficam as pistas de
            # timbre que separam uma voz da outra.
            emphasized = np.empty_like(data)
            emphasized[0] = data[0] - PRE_EMPHASIS * last_sample
            emphasized[1:] = data[1:] - PRE_EMPHASIS * data[:-1]

            n_frames = 1 + (data.size - frame_len) // hop_len
            strides = (emphasized.strides[0] * hop_len, emphasized.strides[0])
            frames = np.lib.stride_tricks.as_strided(
                emphasized, shape=(n_frames, frame_len), strides=strides
            )

            # Blocos menores mantêm o pico de memória do FFT sob controle.
            for start in range(0, n_frames, 4096):
                piece = frames[start:start + 4096] * window
                spectrum = np.abs(np.fft.rfft(piece, n=n_fft)) ** 2 / n_fft
                mel = spectrum @ bank.T
                np.maximum(mel, 1e-10, out=mel)
                blocks.append((np.log(mel) @ dct.T).astype(np.float32))

            # A amostra guardada é a que antecede o próximo bloco de quadros,
            # não a última do bloco atual: a pré-ênfase precisa continuar
            # exatamente de onde o janelamento vai recomeçar.
            consumido = n_frames * hop_len
            last_sample = float(data[consumido - 1])
            carry = data[consumido:].copy()
    except Exception as exc:  # noqa: BLE001 - diarização nunca é obrigatória
        logger.warning("Falha ao calcular MFCC de %s: %s", wav_path, exc)
        return None

    if not blocks:
        return None
    return np.concatenate(blocks, axis=0)


# --- Assinaturas e clusterização ---------------------------------------

def _embeddings(np, mfcc, spans: Sequence[tuple]):
    """Assinatura vocal de cada trecho: média e desvio dos coeficientes."""
    # Normalização por canal (CMVN): remove o viés do microfone e da sala,
    # que de outro modo pesaria mais que a diferença entre as pessoas.
    mean = mfcc.mean(axis=0, keepdims=True)
    std = mfcc.std(axis=0, keepdims=True) + 1e-6
    normalized = (mfcc - mean) / std

    hop = HOP_MS / 1000.0
    vectors = []
    for start, end in spans:
        first = max(0, int(start / hop))
        last = min(normalized.shape[0], int(math.ceil(end / hop)))
        if last - first < 8:
            vectors.append(None)
            continue
        window = normalized[first:last, 1:]  # o coeficiente 0 é só volume
        vector = np.concatenate((window.mean(axis=0), window.std(axis=0)))
        norm = float(np.linalg.norm(vector))
        vectors.append((vector / norm).astype(np.float32) if norm > 1e-6 else None)
    return vectors


def _agglomerate(np, matrix, threshold: float, max_clusters: int) -> List[int]:
    """Ligação média com distância de cosseno; corta o dendrograma no limiar.

    Devolve o índice do grupo de cada linha da matriz de assinaturas.
    """
    n = matrix.shape[0]
    distances = 1.0 - matrix @ matrix.T
    np.clip(distances, 0.0, 2.0, out=distances)
    np.fill_diagonal(distances, np.inf)

    sizes = np.ones(n, dtype=np.float32)
    active = np.ones(n, dtype=bool)
    owner = list(range(n))
    remaining = n

    while remaining > 1:
        flat = int(np.argmin(distances))
        i, j = divmod(flat, n)
        smallest = float(distances[i, j])
        if not math.isfinite(smallest):
            break
        # Continua fundindo além do limiar apenas enquanto houver mais grupos
        # do que o máximo permitido — é o que impede 30 "falantes" num áudio
        # ruidoso de duas pessoas.
        if smallest > threshold and remaining <= max_clusters:
            break

        total = sizes[i] + sizes[j]
        merged = (sizes[i] * distances[i] + sizes[j] * distances[j]) / total
        distances[i] = merged
        distances[:, i] = merged
        distances[i, i] = np.inf
        distances[j, :] = np.inf
        distances[:, j] = np.inf
        sizes[i] = total
        active[j] = False
        remaining -= 1
        for index, current in enumerate(owner):
            if current == j:
                owner[index] = i

    remap: Dict[int, int] = {}
    for root in owner:
        if root not in remap:
            remap[root] = len(remap)
    return [remap[root] for root in owner]


def assign_speakers(
    wav_path: str,
    segments: List[Dict[str, Any]],
    max_speakers: int = config.DIARIZE_MAX_SPEAKERS,
    min_segment: float = config.DIARIZE_MIN_SEGMENT,
    threshold: float = config.DIARIZE_THRESHOLD,
) -> Optional[Dict[str, Any]]:
    """Marca cada trecho com um falante e resume o tempo de fala de cada um.

    Escreve `speaker` (rótulo exibido) e `speaker_id` (índice estável) em cada
    item de `segments`, no lugar. Devolve o resumo por falante, ou None quando
    a diarização não pôde ser feita — nesse caso nada é alterado.
    """
    np = _numpy()
    if np is None or not segments:
        return None

    mfcc = compute_mfcc(wav_path)
    if mfcc is None or mfcc.shape[0] < 50:
        return None

    spans = [(float(s["start"]), float(s["end"])) for s in segments]
    vectors = _embeddings(np, mfcc, spans)

    # Só trechos com áudio suficiente definem os grupos; os curtos entram
    # depois, para não puxarem o centro de nenhum falante.
    anchors = [
        i for i, vector in enumerate(vectors)
        if vector is not None and spans[i][1] - spans[i][0] >= min_segment
    ]
    if len(anchors) < 3:
        return None
    if len(anchors) > MAX_SEGMENTS_CLUSTER:
        step = len(anchors) / MAX_SEGMENTS_CLUSTER
        anchors = [anchors[int(i * step)] for i in range(MAX_SEGMENTS_CLUSTER)]

    matrix = np.stack([vectors[i] for i in anchors])
    try:
        groups = _agglomerate(np, matrix, threshold, max(1, max_speakers))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Clusterização de falantes falhou: %s", exc)
        return None

    n_groups = max(groups) + 1
    centroids = np.zeros((n_groups, matrix.shape[1]), dtype=np.float32)
    for row, group in zip(matrix, groups):
        centroids[group] += row
    norms = np.linalg.norm(centroids, axis=1, keepdims=True)
    norms[norms < 1e-6] = 1.0
    centroids /= norms

    # Atribuição final: quem tem assinatura vai para o centro mais próximo;
    # quem é curto demais herda o falante do trecho anterior, que é o palpite
    # certo na esmagadora maioria das vezes.
    labels: List[int] = []
    previous = 0
    for index, vector in enumerate(vectors):
        if vector is None:
            labels.append(previous)
            continue
        group = int(np.argmax(centroids @ vector))
        labels.append(group)
        previous = group

    # Rótulos ordenados por tempo de fala: "Falante 1" é sempre quem mais fala.
    talk_time: Dict[int, float] = {}
    for segment, group in zip(segments, labels):
        talk_time[group] = talk_time.get(group, 0.0) + (segment["end"] - segment["start"])
    ordering = sorted(talk_time, key=lambda g: talk_time[g], reverse=True)
    rank = {group: position for position, group in enumerate(ordering)}

    for segment, group in zip(segments, labels):
        position = rank[group]
        segment["speaker_id"] = position
        segment["speaker"] = f"Falante {position + 1}"

    total = sum(talk_time.values()) or 1.0
    resumo = [
        {
            "id": rank[group],
            "nome": f"Falante {rank[group] + 1}",
            "segundos": round(talk_time[group], 1),
            "percentual": round(talk_time[group] / total * 100, 1),
            "trechos": sum(1 for label in labels if rank[label] == rank[group]),
        }
        for group in ordering
    ]
    logger.info("Diarização: %d falante(s) identificado(s)", len(resumo))
    return {"falantes": resumo, "total": len(resumo)}


def build_turns(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Agrupa trechos consecutivos do mesmo falante num único turno de fala.

    É a forma como uma transcrição de reunião é lida: um bloco por vez que
    alguém fala, não uma linha por frase.
    """
    turns: List[Dict[str, Any]] = []
    for segment in segments:
        speaker = segment.get("speaker")
        if turns and turns[-1]["speaker"] == speaker:
            turns[-1]["end"] = segment["end"]
            turns[-1]["text"] = f"{turns[-1]['text']} {segment['text']}".strip()
            turns[-1]["segments"].append(segment["id"])
        else:
            turns.append({
                "id": len(turns) + 1,
                "speaker": speaker,
                "speaker_id": segment.get("speaker_id", 0),
                "start": segment["start"],
                "end": segment["end"],
                "start_str": segment["start_str"],
                "text": segment["text"],
                "segments": [segment["id"]],
            })
    return turns
