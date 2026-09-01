"""Motor de transcrição em português do Brasil sobre o faster-whisper.

Decisões que mais afetam a qualidade do resultado, e o porquê de cada uma:

* **large-v3-turbo** como padrão. Tem o mesmo codificador do large-v3 (a parte
  que de fato "escuta"), mas 4 camadas de decodificação em vez de 32. Na prática
  entrega a precisão da família large em pt-BR rodando várias vezes mais rápido
  em CPU — que é o cenário desta máquina.
* **Processamento em lote** (`BatchedInferencePipeline`) com recorte por VAD.
  Além de acelerar, ele impede que o modelo arraste contexto entre blocos, o
  que é a origem mais comum das alucinações em áudios longos.
* **Tempo por palavra** ligado sempre. É o que permite montar legendas com
  quebras corretas, destacar a palavra falada durante a reprodução e medir a
  confiança de cada frase separadamente.

Depois da decodificação vem uma esteira de pós-processamento que é onde mora
boa parte do valor: separação de falantes, divisão em frases com tempo exato,
limpeza tipográfica do português, montagem das legendas e leitura automática
do conteúdo (resumo, temas, capítulos).
"""
import gc
import logging
import math
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from faster_whisper import BatchedInferencePipeline, WhisperModel

import analysis
import config
import diarize
import media
import ptbr
from media import TranscriptionCancelled

logger = logging.getLogger("transcriber")

ProgressFn = Callable[[Dict[str, Any]], None]


def model_is_cached(model_id: str) -> bool:
    """Diz se o modelo já está no cache local (evita download surpresa)."""
    from faster_whisper.utils import _MODELS

    repo = _MODELS.get(model_id, model_id)
    if os.path.isdir(repo):
        return True

    cache = os.environ.get("HF_HOME") or os.path.join(os.path.expanduser("~"), ".cache", "huggingface")
    hub = os.path.join(cache, "hub") if not cache.endswith("hub") else cache
    folder = os.path.join(hub, "models--" + repo.replace("/", "--"), "snapshots")
    if not os.path.isdir(folder):
        return False
    return any(
        any(f.endswith(".bin") for f in os.listdir(os.path.join(folder, snap)))
        for snap in os.listdir(folder)
        if os.path.isdir(os.path.join(folder, snap))
    )


class _Pacer:
    """Mantém a barra de progresso avançando entre um lote e outro.

    O processamento em lote decodifica quatro minutos de áudio por vez e só
    então devolve o texto. Sem isto a barra ficaria parada por minutos e daria
    a impressão de travamento. A estimativa parte da velocidade típica do
    perfil e se corrige sozinha assim que o primeiro lote real chega.
    """

    def __init__(self, total_duration: float, profile: str, report: Callable[[float, str, str], None]):
        self.total = max(1.0, total_duration)
        self.throughput = config.THROUGHPUT_INICIAL.get(profile, 1.0)
        self.report = report
        self.started = time.monotonic()
        self.audio_done = 0.0
        self._last_percent = 15.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def observe(self, audio_position: float) -> None:
        """Registra até onde a transcrição chegou de verdade."""
        self.audio_done = max(self.audio_done, audio_position)
        elapsed = time.monotonic() - self.started
        if elapsed > 5 and self.audio_done > 0:
            self.throughput = self.audio_done / elapsed

    def _percent(self) -> float:
        elapsed = time.monotonic() - self.started
        estimado = min(self.total, elapsed * self.throughput)
        # Nunca projetar mais de um lote (4 min de áudio) além do confirmado.
        posicao = max(self.audio_done, min(estimado, self.audio_done + 240))
        atual = 15.0 + min(79.0, (posicao / self.total) * 80.0)
        # Uma barra que anda para trás parece defeito, mesmo quando a nova
        # estimativa é a correta: só deixamos avançar.
        self._last_percent = max(self._last_percent, atual)
        return self._last_percent

    def _loop(self) -> None:
        while not self._stop.wait(2.0):
            confirmado = f"{self.audio_done / 60:.0f} de {self.total / 60:.0f} min de áudio"
            self.report(self._percent(), "Transcrevendo", f"{confirmado} já reconhecidos")


class Transcriber:
    """Mantém os modelos carregados em memória entre uma tarefa e outra."""

    def __init__(self) -> None:
        self._models: Dict[str, WhisperModel] = {}
        self._lock = threading.Lock()
        self.cpu_threads = max(1, os.cpu_count() or 4)
        self.device, self.compute_type = self._detect_device()
        logger.info(
            "Dispositivo: %s (%s) com %d threads de CPU",
            self.device, self.compute_type, self.cpu_threads,
        )

    # --- Hardware -------------------------------------------------------
    @staticmethod
    def _detect_device() -> tuple:
        """Usa GPU quando existir; senão int8 na CPU (instruções AVX2/AVX512)."""
        try:
            import ctranslate2

            if ctranslate2.get_cuda_device_count() > 0:
                return "cuda", "float16"
        except Exception:  # ctranslate2 sem suporte a CUDA compilado
            pass
        return "cpu", "int8"

    def describe_hardware(self) -> Dict[str, Any]:
        return {
            "device": self.device,
            "compute_type": self.compute_type,
            "cpu_threads": self.cpu_threads,
            "gpu": self.device == "cuda",
            "modelo_carregado": next(iter(self._models), None),
        }

    # --- Modelos --------------------------------------------------------
    def get_model(self, model_name: str) -> WhisperModel:
        with self._lock:
            if model_name in self._models:
                return self._models[model_name]

            logger.info("Carregando modelo %s...", model_name)
            model = WhisperModel(
                model_name,
                device=self.device,
                compute_type=self.compute_type,
                cpu_threads=self.cpu_threads,
                num_workers=1,
            )
            # Só um modelo grande por vez cabe confortavelmente em 16 GB.
            for old in list(self._models):
                del self._models[old]
            gc.collect()
            self._models[model_name] = model
            return model

    def unload(self) -> None:
        with self._lock:
            self._models.clear()
        gc.collect()

    # --- Transcrição ----------------------------------------------------
    def transcribe(
        self,
        input_path: str,
        output_dir: str,
        model_name: str = config.DEFAULT_MODEL,
        profile: str = config.DEFAULT_PROFILE,
        language: Optional[str] = "pt",
        vocabulary: str = "",
        on_progress: Optional[ProgressFn] = None,
        on_segment: Optional[Callable[[Dict[str, Any]], None]] = None,
        cancel_event: Optional[threading.Event] = None,
        keep_audio_preview: bool = config.KEEP_AUDIO_PREVIEW,
        diarizar: bool = config.DIARIZE,
        analisar: bool = config.ANALYZE,
        remover_vicios: bool = False,
    ) -> Dict[str, Any]:
        """Transcreve um arquivo de áudio ou vídeo inteiro.

        `on_segment` é chamado assim que cada trecho fica pronto, o que permite
        à interface mostrar o texto aparecendo em tempo real em vez de esperar
        o fim do processamento.
        """
        settings = config.PROFILES.get(profile, config.PROFILES[config.DEFAULT_PROFILE])
        os.makedirs(output_dir, exist_ok=True)
        started_at = time.monotonic()

        def report(percent: float, stage: str, detail: str = "") -> None:
            if on_progress:
                on_progress({"percent": round(percent, 1), "stage": stage, "detail": detail})

        def check_cancel() -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise TranscriptionCancelled("Transcrição cancelada pelo usuário.")

        base_name = os.path.splitext(os.path.basename(input_path))[0]
        wav_path = os.path.join(output_dir, f"{base_name}.wav")
        preview_path = os.path.join(output_dir, "preview.m4a") if keep_audio_preview else None
        terms = [t.strip() for t in vocabulary.replace(";", ",").split(",") if t.strip()]

        try:
            # 1. Inspeção -------------------------------------------------
            check_cancel()
            report(1.0, "Analisando o arquivo", "Lendo duração, codecs e faixas de áudio...")
            info = media.probe(input_path)
            if not info.get("has_audio", True):
                raise RuntimeError("Este arquivo não possui faixa de áudio para transcrever.")
            total_duration = info.get("duration", 0.0)

            # 2. Extração de áudio ---------------------------------------
            filtros = config.AUDIO_FILTERS if config.AUDIO_PREPROCESS else None
            report(
                3.0,
                "Extraindo o áudio",
                "Convertendo para 16 kHz mono e nivelando o volume..."
                if filtros else "Convertendo para 16 kHz mono com o FFmpeg...",
            )
            media.extract_audio(
                input_path,
                wav_path,
                preview_path=preview_path,
                threads=self.cpu_threads,
                duration=total_duration,
                on_progress=lambda frac: report(
                    3.0 + frac * 9.0,
                    "Extraindo o áudio",
                    f"{frac * 100:.0f}% do áudio convertido",
                ),
                cancel_event=cancel_event,
                filters=filtros,
            )
            if total_duration <= 0:
                total_duration = media.get_duration(wav_path)

            # 3. Modelo ---------------------------------------------------
            check_cancel()
            if model_is_cached(model_name):
                report(13.0, "Carregando o modelo de IA", f"Preparando {model_name} na memória...")
            else:
                report(
                    13.0,
                    "Baixando o modelo de IA",
                    f"Primeira execução do {model_name}: o download acontece só uma vez.",
                )
            model = self.get_model(model_name)

            engine: Any = model
            decode_kwargs: Dict[str, Any] = {}
            if settings["batched"]:
                engine = BatchedInferencePipeline(model=model)
                decode_kwargs["batch_size"] = settings["batch_size"]

            # 4. Decodificação -------------------------------------------
            check_cancel()
            report(
                15.0,
                "Transcrevendo",
                f"Perfil {settings['nome'].lower()} · {self.cpu_threads} núcleos · {model_name}",
            )

            hotwords = ", ".join(terms) if terms else None
            segments_iter, whisper_info = engine.transcribe(
                wav_path,
                language=language or None,
                task="transcribe",
                beam_size=settings["beam_size"],
                best_of=settings["best_of"],
                patience=settings["patience"],
                temperature=settings["temperature"],
                repetition_penalty=config.REPETITION_PENALTY,
                no_repeat_ngram_size=config.NO_REPEAT_NGRAM_SIZE,
                compression_ratio_threshold=config.COMPRESSION_RATIO_THRESHOLD,
                log_prob_threshold=config.LOG_PROB_THRESHOLD,
                no_speech_threshold=config.NO_SPEECH_THRESHOLD,
                # Contexto entre blocos é o que faz o modelo entrar em laço;
                # o modo sequencial ainda o usa, mas reinicia ao aquecer.
                condition_on_previous_text=not settings["batched"],
                prompt_reset_on_temperature=0.5,
                initial_prompt=config.INITIAL_PROMPT,
                hotwords=hotwords,
                word_timestamps=True,
                hallucination_silence_threshold=config.HALLUCINATION_SILENCE_THRESHOLD,
                vad_filter=True,
                vad_parameters={
                    "threshold": config.VAD_THRESHOLD,
                    "min_speech_duration_ms": config.VAD_MIN_SPEECH_MS,
                    "min_silence_duration_ms": config.VAD_MIN_SILENCE_MS,
                    "speech_pad_ms": config.VAD_SPEECH_PAD_MS,
                },
                **decode_kwargs,
            )

            detected_language = getattr(whisper_info, "language", language) or "pt"
            language_probability = float(getattr(whisper_info, "language_probability", 1.0) or 1.0)

            segments: List[Dict[str, Any]] = []
            discarded = 0

            pacer = _Pacer(total_duration, profile, report)
            pacer.start()
            try:
                for raw in segments_iter:
                    check_cancel()
                    pacer.observe(raw.end)

                    text = ptbr.clean_text(raw.text)
                    if ptbr.is_hallucination(
                        text,
                        no_speech_prob=getattr(raw, "no_speech_prob", 0.0) or 0.0,
                        avg_logprob=getattr(raw, "avg_logprob", 0.0) or 0.0,
                    ):
                        discarded += 1
                        continue

                    words = [
                        {
                            "word": w.word,
                            "start": round(w.start, 3),
                            "end": round(w.end, 3),
                            "probability": round(getattr(w, "probability", 1.0) or 1.0, 3),
                        }
                        for w in (raw.words or [])
                    ]

                    segments.append({
                        "id": len(segments) + 1,
                        "start": round(raw.start, 3),
                        "end": round(raw.end, 3),
                        "start_str": ptbr.seconds_to_short(raw.start),
                        "end_str": ptbr.seconds_to_short(raw.end),
                        "text": text,
                        "words": words,
                        # exp(avg_logprob) aproxima a confiança do modelo no trecho.
                        "confidence": round(min(1.0, math.exp(getattr(raw, "avg_logprob", 0.0) or 0.0)), 3),
                        "no_speech_prob": round(getattr(raw, "no_speech_prob", 0.0) or 0.0, 3),
                    })

                    if on_segment:
                        on_segment(segments[-1])
            finally:
                pacer.stop()

            if not segments:
                raise RuntimeError(
                    "Nenhuma fala foi reconhecida neste arquivo. "
                    "Verifique se o áudio tem voz audível e não apenas música ou ruído."
                )

            # 5. Consolidação ---------------------------------------------
            check_cancel()
            report(94.0, "Revisando o texto", "Corrigindo pontuação, laços e quebras de frase...")
            # As frases precisam existir antes da diarização: uma assinatura
            # vocal medida sobre 30 segundos de bloco misturaria duas pessoas.
            segments = ptbr.split_into_sentences(segments)
            segments = ptbr.drop_repeated_segments(segments)
            for index, segment in enumerate(segments, start=1):
                segment["id"] = index

            # 6. Falantes --------------------------------------------------
            diarization: Optional[Dict[str, Any]] = None
            if diarizar:
                check_cancel()
                report(95.0, "Separando os falantes", "Comparando as assinaturas de voz...")
                try:
                    diarization = diarize.assign_speakers(wav_path, segments)
                except Exception as exc:  # noqa: BLE001 - extra, nunca obrigatório
                    logger.warning("Diarização indisponível: %s", exc)

            # 7. Texto final ----------------------------------------------
            check_cancel()
            report(96.0, "Montando o documento", "Parágrafos, legendas e vocabulário...")
            if remover_vicios:
                for segment in segments:
                    segment["text"] = ptbr.strip_fillers(segment["text"])
                segments = [s for s in segments if s["text"].strip()]
            if terms:
                for segment in segments:
                    segment["text"] = ptbr.apply_vocabulary(segment["text"], terms)

            cues = ptbr.build_cues(segments)
            paragraphs = ptbr.build_paragraphs(segments)
            dialogue = ptbr.build_dialogue(segments) if diarization else []
            full_text = "\n\n".join(paragraphs)
            plain_text = ptbr.capitalize_sentences(
                ptbr.clean_text(" ".join(s["text"] for s in segments))
            )

            # 8. Leitura automática ---------------------------------------
            conteudo: Dict[str, Any] = {}
            if analisar:
                check_cancel()
                report(97.0, "Lendo o conteúdo", "Resumo, temas e capítulos...")
                try:
                    conteudo = analysis.analyze(segments, total_duration)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Análise de conteúdo falhou: %s", exc)

            # 9. Forma de onda --------------------------------------------
            report(99.0, "Finalizando", "Desenhando a forma de onda...")
            waveform = media.waveform_peaks(wav_path) if preview_path else []

            confidences = [s["confidence"] for s in segments if s["confidence"] > 0]
            speech_time = sum(s["end"] - s["start"] for s in segments)
            elapsed = max(0.001, time.monotonic() - started_at)

            report(100.0, "Concluído", "Transcrição pronta.")

            return {
                "success": True,
                "versao": config.VERSION,
                "duration": round(total_duration, 2),
                "speech_duration": round(speech_time, 2),
                "language": detected_language,
                "language_probability": round(language_probability, 3),
                "model": model_name,
                "profile": profile,
                "device": self.device,
                "compute_type": self.compute_type,
                "segments": segments,
                "cues": cues,
                "paragraphs": paragraphs,
                "dialogue": dialogue,
                "full_text": full_text,
                "plain_text": plain_text,
                "word_count": len(plain_text.split()),
                "avg_confidence": round(sum(confidences) / len(confidences), 3) if confidences else 0.0,
                "discarded_segments": discarded,
                "audio_preview": os.path.basename(preview_path) if preview_path and os.path.exists(preview_path) else None,
                "waveform": waveform,
                "media_info": info,
                "diarization": diarization,
                "analysis": conteudo,
                "legendas_qa": ptbr.subtitle_report(cues),
                "vocabulary": terms,
                # Quantas vezes mais rápido que o tempo real: a métrica que
                # responde "vale a pena usar o perfil de precisão nesta máquina?"
                "processing_seconds": round(elapsed, 1),
                "speed_factor": round(total_duration / elapsed, 2) if total_duration else 0.0,
            }

        finally:
            # O WAV chega a 115 MB por hora de áudio e já cumpriu seu papel.
            if os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except OSError as exc:
                    logger.warning("Não foi possível remover %s: %s", wav_path, exc)
