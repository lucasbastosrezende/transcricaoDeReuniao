"""Configurações centrais do Transcritor pt-BR.

Todos os valores podem ser sobrescritos por variáveis de ambiente ou por um
arquivo `.env` ao lado deste módulo, o que permite ajustar a aplicação sem
editar código. A ordem de precedência é:

    variável de ambiente  >  arquivo .env  >  padrão desta tabela
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

VERSION = "2.0.0"

# --- Arquivo .env -------------------------------------------------------
# Um leitor mínimo evita mais uma dependência para ler cinco linhas de texto.

def _load_dotenv(path: str) -> None:
    """Carrega pares CHAVE=valor de um .env sem sobrescrever o ambiente real."""
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                # O ambiente do processo sempre vence: o .env é só o padrão.
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass


_load_dotenv(os.path.join(BASE_DIR, ".env"))


# --- Leitores tipados ---------------------------------------------------

def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, default)).replace(",", "."))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "sim", "on")


# --- Diretórios ---------------------------------------------------------
UPLOAD_DIR = _env("TRANSCRITOR_UPLOAD_DIR", os.path.join(BASE_DIR, "uploads"))
OUTPUT_DIR = _env("TRANSCRITOR_OUTPUT_DIR", os.path.join(BASE_DIR, "outputs"))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
DB_PATH = _env("TRANSCRITOR_BANCO", os.path.join(OUTPUT_DIR, "historico.sqlite3"))

# --- Servidor -----------------------------------------------------------
HOST = _env("TRANSCRITOR_HOST", "127.0.0.1")
PORT = _env_int("TRANSCRITOR_PORT", 8000)
# Token opcional: quando definido, toda chamada à API exige
# `Authorization: Bearer <token>`. Só faz sentido ao expor fora do localhost.
AUTH_TOKEN = _env("TRANSCRITOR_TOKEN", "").strip()
# Origens liberadas para CORS (separadas por vírgula). Vazio = mesma origem.
ALLOWED_ORIGINS = [o.strip() for o in _env("TRANSCRITOR_ORIGENS", "").split(",") if o.strip()]
# 0 = sem limite. Acima disso o upload é recusado antes de gravar em disco.
MAX_UPLOAD_MB = _env_int("TRANSCRITOR_MAX_UPLOAD_MB", 0)

# --- Retenção de arquivos ----------------------------------------------
# Resultados mais antigos que isso são apagados na inicialização (0 = nunca).
RETENTION_HOURS = _env_int("TRANSCRITOR_RETENTION_HORAS", 72)
# Apaga o vídeo enviado assim que a transcrição termina.
DELETE_UPLOAD_AFTER = _env_bool("TRANSCRITOR_APAGAR_UPLOAD", True)
# Mantém o áudio comprimido extraído para permitir reprodução sincronizada.
KEEP_AUDIO_PREVIEW = _env_bool("TRANSCRITOR_MANTER_AUDIO", True)

# --- Modelos ------------------------------------------------------------
DEFAULT_MODEL = _env("TRANSCRITOR_MODELO", "large-v3-turbo")
DEFAULT_PROFILE = _env("TRANSCRITOR_PERFIL", "equilibrado")

# Catálogo exibido na interface. `ram_gb` é o pico aproximado em int8 (CPU).
MODEL_CATALOG = [
    {
        "id": "large-v3-turbo",
        "nome": "Large v3 Turbo",
        "resumo": "Melhor precisão em pt-BR para CPU. Padrão recomendado.",
        "params": "809M",
        "ram_gb": 2.0,
        "velocidade": "rápido",
        "precisao": 5,
    },
    {
        "id": "large-v3",
        "nome": "Large v3",
        "resumo": "Precisão máxima absoluta. Muito lento sem placa de vídeo.",
        "params": "1.55B",
        "ram_gb": 3.4,
        "velocidade": "muito lento",
        "precisao": 5,
    },
    {
        "id": "medium",
        "nome": "Medium",
        "resumo": "Meio-termo clássico. Inferior ao Turbo em pt-BR.",
        "params": "769M",
        "ram_gb": 1.8,
        "velocidade": "moderado",
        "precisao": 3,
    },
    {
        "id": "small",
        "nome": "Small",
        "resumo": "Rápido, aceitável para áudio limpo e sem sotaque forte.",
        "params": "244M",
        "ram_gb": 0.8,
        "velocidade": "muito rápido",
        "precisao": 2,
    },
    {
        "id": "base",
        "nome": "Base",
        "resumo": "Somente para testes rápidos da instalação.",
        "params": "74M",
        "ram_gb": 0.4,
        "velocidade": "instantâneo",
        "precisao": 1,
    },
]

VALID_MODELS = {m["id"] for m in MODEL_CATALOG}

# --- Perfis de decodificação -------------------------------------------
# Cada perfil traduz uma escolha do usuário em parâmetros reais do decoder.
# Os números vêm de medição nesta classe de máquina (8 núcleos, sem GPU,
# large-v3-turbo em int8). O lote de 8 foi o ponto ótimo: lote 4 ficou 43%
# mais lento e lote 16 não trouxe ganho nenhum.
PROFILES = {
    "rapido": {
        "nome": "Rápido",
        "resumo": "Busca gulosa. Cerca de 20% mais rápido, com mais erros de vírgula e preposição.",
        "batched": True,
        "batch_size": 8,
        "beam_size": 1,
        "best_of": 1,
        "patience": 1.0,
        "temperature": [0.0, 0.2, 0.4],
    },
    "equilibrado": {
        "nome": "Equilibrado",
        "resumo": "Avalia 5 hipóteses por trecho. Melhor relação entre tempo e precisão.",
        "batched": True,
        "batch_size": 8,
        "beam_size": 5,
        "best_of": 5,
        "patience": 1.0,
        "temperature": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
    },
    "precisao": {
        "nome": "Precisão máxima",
        "resumo": "Mantém o contexto entre os trechos. Cerca de 2,5× mais lento — use em áudio ruim.",
        "batched": False,
        "batch_size": 1,
        "beam_size": 5,
        "best_of": 5,
        "patience": 2.0,
        "temperature": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
    },
}

# Quanto de áudio o modelo processa por segundo de CPU, por perfil. Serve só
# para estimar o avanço da barra entre um lote e outro; é corrigido com a
# medição real assim que os primeiros trechos chegam.
# Propositalmente conservador: é melhor a barra avançar devagar e dar um salto
# à frente quando o lote real chega do que prometer um fim que não vem.
THROUGHPUT_INICIAL = {"rapido": 1.3, "equilibrado": 1.0, "precisao": 0.45}

VALID_PROFILES = set(PROFILES)

# --- Qualidade da transcrição ------------------------------------------
# Limiares que combatem alucinação (texto inventado em trechos de silêncio),
# um problema conhecido do Whisper em áudios longos em português.
LOG_PROB_THRESHOLD = _env_float("TRANSCRITOR_LOGPROB", -1.0)
COMPRESSION_RATIO_THRESHOLD = _env_float("TRANSCRITOR_COMPRESSAO", 2.4)
NO_SPEECH_THRESHOLD = _env_float("TRANSCRITOR_SEM_FALA", 0.6)
HALLUCINATION_SILENCE_THRESHOLD = _env_float("TRANSCRITOR_SILENCIO_ALUC", 2.0)
REPETITION_PENALTY = _env_float("TRANSCRITOR_PENALIDADE_REPETICAO", 1.05)
NO_REPEAT_NGRAM_SIZE = _env_int("TRANSCRITOR_NGRAM_SEM_REPETICAO", 0)
# Abaixo disto o trecho é marcado em amarelo na tela como "vale conferir".
LOW_CONFIDENCE = _env_float("TRANSCRITOR_CONFIANCA_BAIXA", 0.55)

# Detector de voz (Silero VAD): recorta silêncios antes de decodificar.
VAD_THRESHOLD = _env_float("TRANSCRITOR_VAD_LIMIAR", 0.5)
VAD_MIN_SILENCE_MS = _env_int("TRANSCRITOR_VAD_SILENCIO_MS", 500)
VAD_MIN_SPEECH_MS = _env_int("TRANSCRITOR_VAD_FALA_MS", 150)
VAD_SPEECH_PAD_MS = _env_int("TRANSCRITOR_VAD_MARGEM_MS", 250)

# Frase de contextualização: ensina o modelo a escrever pt-BR acentuado
# e pontuado em vez de texto corrido sem vírgulas.
INITIAL_PROMPT = _env(
    "TRANSCRITOR_PROMPT",
    "Transcrição em português do Brasil, com ortografia oficial, acentuação "
    "completa e pontuação correta. Exemplo: Olá, tudo bem? Então, hoje nós "
    "vamos falar sobre o relatório de análise técnica não é? Só um instante, "
    "por favor.",
)

# --- Pré-processamento do áudio ----------------------------------------
# Corta o ronco de rede elétrica e nivela o volume antes de decodificar.
# Gravações de reunião variam muito de volume entre quem está perto e quem
# está longe do microfone; nivelar reduz erro nas falas mais baixas.
AUDIO_PREPROCESS = _env_bool("TRANSCRITOR_TRATAR_AUDIO", True)
AUDIO_FILTERS = _env(
    "TRANSCRITOR_FILTROS_AUDIO",
    "highpass=f=60,loudnorm=I=-18:TP=-2:LRA=11",
)

# --- Legendas -----------------------------------------------------------
SUB_MAX_CHARS_PER_LINE = _env_int("TRANSCRITOR_LEGENDA_COLUNAS", 42)
SUB_MAX_LINES = _env_int("TRANSCRITOR_LEGENDA_LINHAS", 2)
SUB_MIN_DURATION = _env_float("TRANSCRITOR_LEGENDA_MIN_S", 1.0)
SUB_MAX_DURATION = _env_float("TRANSCRITOR_LEGENDA_MAX_S", 6.0)
SUB_MAX_CPS = _env_float("TRANSCRITOR_LEGENDA_CPS", 21.0)
SUB_SPLIT_GAP = _env_float("TRANSCRITOR_LEGENDA_PAUSA_S", 0.6)

# --- Diarização (separação de falantes) --------------------------------
# Implementação leve e offline, sem pyannote nem token do Hugging Face:
# MFCC por trecho + clusterização. É uma aproximação — boa para saber
# quantas vozes existem e quem fala mais, não para perícia forense.
DIARIZE = _env_bool("TRANSCRITOR_FALANTES", True)
DIARIZE_MAX_SPEAKERS = _env_int("TRANSCRITOR_FALANTES_MAX", 8)
# Trechos mais curtos que isto não têm áudio suficiente para uma assinatura
# vocal estável; herdam o falante do vizinho mais próximo.
DIARIZE_MIN_SEGMENT = _env_float("TRANSCRITOR_FALANTES_MIN_S", 1.2)
# Distância de corte do dendrograma. Maior = menos falantes distintos.
DIARIZE_THRESHOLD = _env_float("TRANSCRITOR_FALANTES_LIMIAR", 0.62)

# --- Análise do conteúdo ------------------------------------------------
ANALYZE = _env_bool("TRANSCRITOR_ANALISE", True)
SUMMARY_SENTENCES = _env_int("TRANSCRITOR_RESUMO_FRASES", 7)
KEYWORDS_TOP = _env_int("TRANSCRITOR_PALAVRAS_CHAVE", 14)
# Um capítulo nunca é menor que isto, senão o índice vira uma lista de frases.
CHAPTER_MIN_SECONDS = _env_float("TRANSCRITOR_CAPITULO_MIN_S", 90.0)

# --- Interface ----------------------------------------------------------
# Quantidade de picos calculados para desenhar a forma de onda no player.
WAVEFORM_POINTS = _env_int("TRANSCRITOR_ONDA_PONTOS", 900)

# --- Upload -------------------------------------------------------------
ALLOWED_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v", ".ts", ".mpg", ".mpeg",
    ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac", ".wma", ".amr",
}
UPLOAD_CHUNK_SIZE = 4 * 1024 * 1024


def as_dict() -> dict:
    """Instantâneo legível das configurações — usado por /api/health e pela CLI."""
    return {
        "versao": VERSION,
        "modelo_padrao": DEFAULT_MODEL,
        "perfil_padrao": DEFAULT_PROFILE,
        "diarizacao": DIARIZE,
        "analise": ANALYZE,
        "tratar_audio": AUDIO_PREPROCESS,
        "retencao_horas": RETENTION_HOURS,
        "apagar_upload": DELETE_UPLOAD_AFTER,
        "autenticado": bool(AUTH_TOKEN),
        "max_upload_mb": MAX_UPLOAD_MB,
    }
