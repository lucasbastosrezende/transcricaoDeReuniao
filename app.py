"""Servidor web do Transcritor pt-BR.

As tarefas passam por uma fila com um único trabalhador. Isso é proposital:
transcrição satura todos os núcleos da CPU, então rodar duas ao mesmo tempo
deixaria as duas mais lentas do que rodá-las em sequência. Vários arquivos
podem ser enviados de uma vez — eles entram na fila e a tela mostra a posição
de cada um.

O estado vive em memória (rápido para o fluxo em tempo real) e é espelhado no
SQLite a cada mudança relevante, que é o que faz o histórico sobreviver a um
reinício e a busca global funcionar sobre tudo que já foi transcrito.
"""
import asyncio
import json
import logging
import os
import queue
import re
import shutil
import threading
import time
import unicodedata
import uuid
import zipfile
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

import analysis
import config
import exporters
import media
import ptbr
import store
from media import TranscriptionCancelled
from transcriber import Transcriber, model_is_cached

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("app")

for directory in (config.UPLOAD_DIR, config.OUTPUT_DIR, config.STATIC_DIR, config.TEMPLATES_DIR):
    os.makedirs(directory, exist_ok=True)

app = FastAPI(
    title="Transcritor pt-BR",
    version=config.VERSION,
    docs_url=None,
    redoc_url=None,
)
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
templates = Jinja2Templates(directory=config.TEMPLATES_DIR)

engine = Transcriber()

# --- Estado das tarefas -------------------------------------------------
jobs: Dict[str, Dict[str, Any]] = {}
jobs_lock = threading.RLock()
work_queue: "queue.Queue[str]" = queue.Queue()
cancel_events: Dict[str, threading.Event] = {}

TERMINAL_STATES = ("concluido", "erro", "cancelado")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _safe_name(name: str) -> str:
    """Reduz o nome do arquivo ao que é seguro usar em disco e em cabeçalhos."""
    stem = os.path.splitext(os.path.basename(name))[0]
    stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode("ascii")
    stem = re.sub(r"[^\w\s.-]", "", stem).strip().replace(" ", "_")
    stem = re.sub(r"_{2,}", "_", stem)
    return stem[:80] or "transcricao"


def _valid_id(job_id: str) -> str:
    """Barra qualquer identificador que não seja um UUID gerado por nós.

    Os identificadores viram nome de pasta; aceitar texto livre da URL abriria
    caminho para `../` chegar ao sistema de arquivos.
    """
    if not _UUID.match(job_id or ""):
        raise HTTPException(400, "Identificador de tarefa inválido.")
    return job_id


def _job_dir(job_id: str) -> str:
    return os.path.join(config.OUTPUT_DIR, job_id)


def _require_job(job_id: str) -> Dict[str, Any]:
    _valid_id(job_id)
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Tarefa não encontrada.")
    return job


def _persist(job: Dict[str, Any]) -> None:
    """Salva a tarefa em disco e no índice para que sobreviva a um reinício.

    Grava apenas os metadados: a transcrição completa já vive no arquivo JSON
    exportado, e duplicá-la aqui custaria dezenas de megabytes por vídeo longo.
    """
    try:
        path = os.path.join(_job_dir(job["job_id"]), "job.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        resumo = {k: v for k, v in job.items() if k not in ("result", "live_segments")}
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(resumo, handle, ensure_ascii=False)
    except OSError as exc:
        logger.warning("Falha ao salvar a tarefa %s: %s", job.get("job_id"), exc)
    store.save_job(job)


def _slim_result(result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Versão do resultado sem o tempo de cada palavra.

    Numa gravação de uma hora os tempos por palavra são cerca de 80% do JSON e
    a interface não usa nenhum deles — eles importam para as legendas, que já
    foram montadas no servidor. Continuam íntegros no arquivo `_dados.json`.
    """
    if not result:
        return result
    enxuto = dict(result)
    enxuto["segments"] = [
        {k: v for k, v in segment.items() if k != "words"}
        for segment in result.get("segments", [])
    ]
    return enxuto


def _public_view(job: Dict[str, Any], include_result: bool = True) -> Dict[str, Any]:
    """O que a interface pode ver: sem caminhos internos, sem buffer ao vivo."""
    view = {
        k: v for k, v in job.items()
        if k not in ("result", "live_segments", "upload_path")
    }
    view["segment_count"] = len(job.get("live_segments", []))
    if include_result:
        view["result"] = _slim_result(job.get("result"))
    return view


# --- Trabalhador --------------------------------------------------------

def _queue_position(job_id: str) -> int:
    with jobs_lock:
        pending = [j for j in jobs.values() if j["status"] == "na_fila"]
    pending.sort(key=lambda j: j["created_at"])
    for index, job in enumerate(pending, start=1):
        if job["job_id"] == job_id:
            return index
    return 0


def _run_job(job_id: str) -> None:
    job = jobs.get(job_id)
    if job is None:
        return

    cancel_event = cancel_events.setdefault(job_id, threading.Event())
    if cancel_event.is_set():
        job.update(status="cancelado", stage="Cancelado", detail="Tarefa cancelada antes de iniciar.")
        _persist(job)
        return

    job.update(status="processando", started_at=time.time(), stage="Iniciando", percent=0.0)

    def on_progress(info: Dict[str, Any]) -> None:
        job["percent"] = info["percent"]
        job["stage"] = info["stage"]
        job["detail"] = info["detail"]
        elapsed = time.time() - job["started_at"]
        # Estimativa simples de tempo restante, útil em vídeos de horas.
        if info["percent"] > 5:
            job["eta_seconds"] = round(elapsed * (100 - info["percent"]) / info["percent"])

    def on_segment(segment: Dict[str, Any]) -> None:
        # Cópia enxuta: o SSE não precisa carregar tempo de cada palavra.
        job["live_segments"].append(
            {k: segment[k] for k in ("id", "start", "end", "start_str", "end_str", "text", "confidence")}
        )

    try:
        result = engine.transcribe(
            input_path=job["upload_path"],
            output_dir=_job_dir(job_id),
            model_name=job["model"],
            profile=job["profile"],
            language=job["language"],
            vocabulary=job["vocabulary"],
            on_progress=on_progress,
            on_segment=on_segment,
            cancel_event=cancel_event,
            diarizar=job.get("diarizar", config.DIARIZE),
            analisar=job.get("analisar", config.ANALYZE),
            remover_vicios=job.get("remover_vicios", False),
        )

        base = _safe_name(job["filename"])
        paths = exporters.write_all(result, _job_dir(job_id), base)

        job["result"] = result
        job["files"] = paths
        job["formats"] = exporters.available_formats(paths)
        job["status"] = "concluido"
        job["percent"] = 100.0
        job["stage"] = "Concluído"
        job["detail"] = _completion_detail(result)
        job["finished_at"] = time.time()
        job["elapsed"] = round(job["finished_at"] - job["started_at"], 1)
        job["eta_seconds"] = 0
        job["speakers"] = (result.get("diarization") or {}).get("total", 0)
        if result["duration"] > 0:
            job["speed_factor"] = round(result["duration"] / max(1.0, job["elapsed"]), 2)
        store.index_segments(job_id, result.get("segments", []))

    except TranscriptionCancelled:
        job.update(status="cancelado", stage="Cancelado", detail="Tarefa interrompida pelo usuário.")
        shutil.rmtree(_job_dir(job_id), ignore_errors=True)

    except Exception as exc:  # noqa: BLE001 - o erro precisa chegar à interface
        logger.exception("Falha na tarefa %s", job_id)
        job.update(
            status="erro",
            stage="Erro",
            detail=str(exc),
            error=str(exc),
            finished_at=time.time(),
        )

    finally:
        cancel_events.pop(job_id, None)
        if config.DELETE_UPLOAD_AFTER and os.path.exists(job["upload_path"]):
            try:
                os.remove(job["upload_path"])
            except OSError:
                pass
        _persist(job)


def _completion_detail(result: Dict[str, Any]) -> str:
    partes = [f"{result['word_count']} palavras em {len(result['segments'])} trechos"]
    falantes = (result.get("diarization") or {}).get("total")
    if falantes:
        partes.append(f"{falantes} falante(s)")
    capitulos = len((result.get("analysis") or {}).get("capitulos") or [])
    if capitulos:
        partes.append(f"{capitulos} capítulos")
    return " · ".join(partes) + "."


def _worker_loop() -> None:
    while True:
        job_id = work_queue.get()
        try:
            _run_job(job_id)
        except Exception:  # noqa: BLE001 - o trabalhador nunca pode morrer
            logger.exception("Erro não tratado no trabalhador (tarefa %s)", job_id)
        finally:
            work_queue.task_done()


threading.Thread(target=_worker_loop, name="transcricao", daemon=True).start()


# --- Limpeza e restauração ---------------------------------------------

def _restore_jobs() -> None:
    """Recarrega transcrições anteriores para que fiquem no histórico.

    A fonte de verdade é o índice SQLite; a varredura de `job.json` continua
    como resgate para bases criadas antes da versão 2 e para o caso de o banco
    ser apagado sem que as saídas sejam.
    """
    for job in store.load_jobs():
        if job.get("job_id"):
            jobs[job["job_id"]] = job

    if os.path.isdir(config.OUTPUT_DIR):
        for entry in os.listdir(config.OUTPUT_DIR):
            if entry in jobs:
                continue
            path = os.path.join(config.OUTPUT_DIR, entry, "job.json")
            if not os.path.exists(path):
                continue
            try:
                with open(path, encoding="utf-8") as handle:
                    job = json.load(handle)
            except (OSError, json.JSONDecodeError):
                continue
            if job.get("job_id"):
                jobs[job["job_id"]] = job

    for job in jobs.values():
        # Tarefas interrompidas por um desligamento não podem ser retomadas.
        if job.get("status") in ("processando", "na_fila"):
            job["status"] = "erro"
            job["stage"] = "Interrompido"
            job["detail"] = "O servidor foi encerrado durante o processamento."
        job.setdefault("live_segments", [])
        job.setdefault("files", {})
        job.setdefault("formats", [])
        # A transcrição em si é relida do JSON exportado, sob demanda.
        job["result"] = _load_result(job)

    logger.info("Histórico: %d transcrição(ões) restaurada(s).", len(jobs))


def _load_result(job: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    exportado = (job.get("files") or {}).get("json")
    if not exportado or not os.path.exists(exportado):
        return None
    try:
        with open(exportado, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def _cleanup_old_files() -> None:
    """Apaga resultados vencidos e uploads órfãos de execuções anteriores."""
    if config.RETENTION_HOURS > 0 and os.path.isdir(config.OUTPUT_DIR):
        deadline = time.time() - config.RETENTION_HOURS * 3600
        for entry in os.listdir(config.OUTPUT_DIR):
            folder = os.path.join(config.OUTPUT_DIR, entry)
            if os.path.isdir(folder) and os.path.getmtime(folder) < deadline:
                shutil.rmtree(folder, ignore_errors=True)
                jobs.pop(entry, None)
                store.delete_job(entry)

    if os.path.isdir(config.UPLOAD_DIR):
        for entry in os.listdir(config.UPLOAD_DIR):
            path = os.path.join(config.UPLOAD_DIR, entry)
            if os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    store.available()
    _cleanup_old_files()
    _restore_jobs()
    if not media.ffmpeg_available():
        logger.warning("FFmpeg não encontrado. Instale com: winget install Gyan.FFmpeg")
    if config.AUTH_TOKEN:
        logger.info("Autenticação por token ativada.")
    logger.info("Pronto em http://%s:%d", config.HOST, config.PORT)
    yield
    engine.unload()
    store.close()


app.router.lifespan_context = lifespan


# --- Segurança ----------------------------------------------------------

if config.ALLOWED_ORIGINS:
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.ALLOWED_ORIGINS,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def _autenticar(request: Request, call_next):
    """Exige o token quando ele está configurado — só nas rotas da API."""
    if config.AUTH_TOKEN and request.url.path.startswith("/api"):
        header = request.headers.get("authorization", "")
        token = header[7:] if header.lower().startswith("bearer ") else request.query_params.get("token", "")
        if token != config.AUTH_TOKEN:
            return JSONResponse({"detail": "Token ausente ou inválido."}, status_code=401)
    return await call_next(request)


# --- Rotas --------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/health")
async def health():
    """Sinal de vida com o essencial para diagnosticar uma instalação."""
    return {
        "ok": media.ffmpeg_available(),
        "versao": config.VERSION,
        "ffmpeg": media.ffmpeg_available(),
        "banco": store.available(),
        "busca_textual": "fts5" if store.fts_enabled() else "like",
        "hardware": engine.describe_hardware(),
        "fila": work_queue.qsize(),
        "tarefas_em_memoria": len(jobs),
        "config": config.as_dict(),
    }


@app.get("/api/system")
async def system_info():
    """Informa à interface o hardware, os modelos e o que já está baixado."""
    catalog = [
        {**m, "baixado": model_is_cached(m["id"]), "padrao": m["id"] == config.DEFAULT_MODEL}
        for m in config.MODEL_CATALOG
    ]
    return {
        "versao": config.VERSION,
        "hardware": engine.describe_hardware(),
        "ffmpeg": media.ffmpeg_available(),
        "modelos": catalog,
        "perfis": [
            {"id": key, "nome": value["nome"], "resumo": value["resumo"],
             "padrao": key == config.DEFAULT_PROFILE}
            for key, value in config.PROFILES.items()
        ],
        "extensoes": sorted(config.ALLOWED_EXTENSIONS),
        "recursos": {
            "diarizacao": config.DIARIZE,
            "analise": config.ANALYZE,
            "busca_global": store.available(),
            "max_upload_mb": config.MAX_UPLOAD_MB,
        },
        "formatos": [
            {"id": key, "label": value[0], "ext": value[1]}
            for key, value in exporters.FORMATS.items()
        ],
    }


@app.get("/api/stats")
async def global_stats():
    """Totais de tudo que já foi transcrito nesta máquina."""
    return store.summary()


@app.get("/api/jobs")
async def list_jobs(limit: int = Query(50, ge=1, le=500)):
    with jobs_lock:
        items = [_public_view(job, include_result=False) for job in jobs.values()]
    items.sort(key=lambda j: j.get("created_at", 0), reverse=True)
    return {"jobs": items[:limit], "fila": work_queue.qsize()}


@app.get("/api/search")
async def search(q: str = Query(..., min_length=2), limit: int = Query(60, ge=1, le=200)):
    """Busca uma palavra ou frase em todas as transcrições já feitas."""
    return {"termo": q, "resultados": store.search(q, limit)}


@app.post("/api/transcribe")
async def start_transcription(
    request: Request,
    files: List[UploadFile] = File(default=[]),
    file: Optional[UploadFile] = File(default=None),
    model_name: str = Form(config.DEFAULT_MODEL),
    profile: str = Form(config.DEFAULT_PROFILE),
    language: str = Form("pt"),
    vocabulary: str = Form(""),
    diarizar: bool = Form(config.DIARIZE),
    analisar: bool = Form(config.ANALYZE),
    remover_vicios: bool = Form(False),
):
    """Recebe um ou vários arquivos e enfileira uma tarefa para cada um."""
    entradas = [f for f in ([file] if file else []) + list(files) if f and f.filename]
    if not entradas:
        raise HTTPException(400, "Nenhum arquivo foi enviado.")
    if not media.ffmpeg_available():
        raise HTTPException(
            503,
            "O FFmpeg não foi encontrado. Instale com: winget install Gyan.FFmpeg",
        )

    limite = config.MAX_UPLOAD_MB * 1024 * 1024 if config.MAX_UPLOAD_MB else 0
    if limite:
        declarado = int(request.headers.get("content-length") or 0)
        if declarado > limite * len(entradas) + 1024 * 1024:
            raise HTTPException(413, f"O envio excede o limite de {config.MAX_UPLOAD_MB} MB por arquivo.")

    model_name = model_name if model_name in config.VALID_MODELS else config.DEFAULT_MODEL
    profile = profile if profile in config.VALID_PROFILES else config.DEFAULT_PROFILE

    criadas: List[Dict[str, Any]] = []
    recusadas: List[Dict[str, str]] = []

    for entrada in entradas:
        extension = os.path.splitext(entrada.filename)[1].lower()
        if extension not in config.ALLOWED_EXTENSIONS:
            recusadas.append({
                "filename": entrada.filename,
                "motivo": f"O formato {extension or 'desconhecido'} não é suportado.",
            })
            continue

        job_id = str(uuid.uuid4())
        upload_path = os.path.join(config.UPLOAD_DIR, f"{job_id}{extension}")
        size = 0
        try:
            with open(upload_path, "wb") as buffer:
                while chunk := await entrada.read(config.UPLOAD_CHUNK_SIZE):
                    size += len(chunk)
                    if limite and size > limite:
                        raise ValueError(f"acima de {config.MAX_UPLOAD_MB} MB")
                    buffer.write(chunk)
        except (OSError, ValueError) as exc:
            if os.path.exists(upload_path):
                os.remove(upload_path)
            recusadas.append({"filename": entrada.filename, "motivo": f"Arquivo rejeitado: {exc}"})
            continue

        if size == 0:
            os.remove(upload_path)
            recusadas.append({"filename": entrada.filename, "motivo": "O arquivo está vazio."})
            continue

        job = {
            "job_id": job_id,
            "filename": entrada.filename,
            "size_bytes": size,
            "upload_path": upload_path,
            "model": model_name,
            "profile": profile,
            "language": None if language in ("auto", "") else language,
            "vocabulary": vocabulary[:2000],
            "diarizar": bool(diarizar),
            "analisar": bool(analisar),
            "remover_vicios": bool(remover_vicios),
            "status": "na_fila",
            "stage": "Na fila",
            "detail": "Aguardando o processador ficar livre...",
            "percent": 0.0,
            "eta_seconds": None,
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
            "error": None,
            "result": None,
            "files": {},
            "formats": [],
            "live_segments": [],
        }

        # O evento de cancelamento nasce antes de a tarefa entrar na fila: se
        # fosse criado depois, o trabalhador poderia começar com um evento
        # próprio e o botão "Cancelar" acionaria um objeto descartado.
        cancel_events[job_id] = threading.Event()
        with jobs_lock:
            jobs[job_id] = job
        work_queue.put(job_id)

        criadas.append({
            "job_id": job_id,
            "filename": entrada.filename,
            "size_bytes": size,
            "queue_position": _queue_position(job_id),
        })

    if not criadas:
        raise HTTPException(400, recusadas[0]["motivo"] if recusadas else "Nenhum arquivo aceito.")

    primeira = criadas[0]
    return {
        # Campos no singular mantêm compatibilidade com quem já chamava a API
        # com um arquivo só; a lista completa vem em `tarefas`.
        "job_id": primeira["job_id"],
        "filename": primeira["filename"],
        "queue_position": primeira["queue_position"],
        "tarefas": criadas,
        "recusadas": recusadas,
    }


@app.get("/api/progress/{job_id}")
async def job_progress(job_id: str):
    job = _require_job(job_id)
    if job.get("result") is None and job.get("status") == "concluido":
        job["result"] = _load_result(job)
    return _public_view(job)


@app.get("/api/events/{job_id}")
async def job_events(job_id: str):
    """Fluxo SSE que entrega progresso e os trechos já transcritos.

    Enviar os segmentos conforme saem faz o texto aparecer durante o
    processamento, em vez de tudo de uma vez só no final.
    """
    _require_job(job_id)

    async def stream():
        sent = 0
        last_signature: Optional[tuple] = None
        last_beat = time.monotonic()
        while True:
            job = jobs.get(job_id)
            if job is None:
                yield "event: erro\ndata: {\"message\": \"Tarefa removida.\"}\n\n"
                return

            segments = job["live_segments"]
            if len(segments) > sent:
                batch = segments[sent:len(segments)]
                sent += len(batch)
                yield f"data: {json.dumps({'type': 'segmentos', 'segments': batch}, ensure_ascii=False)}\n\n"

            signature = (job["status"], job["percent"], job["stage"], job["detail"])
            if signature != last_signature:
                last_signature = signature
                payload = {
                    "type": "status",
                    "status": job["status"],
                    "percent": job["percent"],
                    "stage": job["stage"],
                    "detail": job["detail"],
                    "eta_seconds": job.get("eta_seconds"),
                    "queue_position": _queue_position(job_id) if job["status"] == "na_fila" else 0,
                }
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

            if job["status"] in TERMINAL_STATES:
                final = {
                    "type": "fim",
                    "status": job["status"],
                    "error": job.get("error"),
                    "result": _slim_result(job.get("result")),
                    "formats": job.get("formats", []),
                    "elapsed": job.get("elapsed"),
                    "speed_factor": job.get("speed_factor"),
                }
                yield f"data: {json.dumps(final, ensure_ascii=False)}\n\n"
                return

            # Comentário periódico: mantém a conexão viva através de proxies
            # que derrubam fluxos silenciosos depois de alguns segundos.
            if time.monotonic() - last_beat > 15:
                last_beat = time.monotonic()
                yield ": ping\n\n"

            await asyncio.sleep(0.4)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/cancel/{job_id}")
async def cancel_job(job_id: str):
    job = _require_job(job_id)
    if job["status"] in TERMINAL_STATES:
        return {"cancelado": False, "motivo": "A tarefa já terminou."}
    cancel_events.setdefault(job_id, threading.Event()).set()
    job["stage"] = "Cancelando"
    job["detail"] = "Encerrando o processamento..."
    return {"cancelado": True}


@app.get("/api/media/{job_id}")
async def job_media(job_id: str):
    """Serve o áudio leve usado pelo player sincronizado da interface."""
    job = _require_job(job_id)
    result = job.get("result") or _load_result(job)
    name = (result or {}).get("audio_preview")
    if not name:
        raise HTTPException(404, "Áudio não disponível.")
    path = os.path.join(_job_dir(job_id), os.path.basename(name))
    if not os.path.exists(path):
        raise HTTPException(404, "Áudio não disponível.")
    return FileResponse(path, media_type="audio/mp4")


@app.get("/api/waveform/{job_id}")
async def job_waveform(job_id: str):
    """Envelope do áudio para desenhar a onda sob o player."""
    job = _require_job(job_id)
    result = job.get("result") or _load_result(job)
    return {"pontos": (result or {}).get("waveform") or []}


@app.patch("/api/jobs/{job_id}/text")
async def update_text(job_id: str, payload: Dict[str, Any] = Body(...)):
    """Salva as correções feitas na tela e regera todos os arquivos.

    Sem isto, corrigir um nome próprio na interface e depois baixar o DOCX
    devolveria o texto errado — a edição vivia só no navegador. Aqui ela vira
    a nova verdade: segmentos, parágrafos, legendas e exportações são refeitos
    a partir do texto corrigido, mantendo os tempos originais.
    """
    job = _require_job(job_id)
    if job.get("status") != "concluido":
        raise HTTPException(409, "Só é possível editar uma transcrição concluída.")

    result = job.get("result") or _load_result(job)
    if not result:
        raise HTTPException(404, "O resultado desta transcrição não está mais disponível.")

    edits = payload.get("segments")
    if not isinstance(edits, list) or not edits:
        raise HTTPException(400, "Envie a lista de trechos editados.")

    by_id = {int(item["id"]): str(item.get("text", "")) for item in edits if item.get("id") is not None}
    alterados = 0
    for segment in result.get("segments", []):
        novo = by_id.get(segment["id"])
        if novo is None:
            continue
        limpo = ptbr.clean_text(novo)
        if limpo and limpo != segment["text"]:
            segment["text"] = limpo
            segment["editado"] = True
            alterados += 1

    if not alterados:
        return {"salvo": False, "motivo": "Nenhuma diferença em relação ao texto atual."}

    # Reconstrução: as legendas dependem do tempo por palavra, que continua
    # válido; só o texto mudou. Onde o trecho foi editado, a legenda passa a
    # usar o texto novo por inteiro, sem tentar recasar palavra a palavra.
    for segment in result["segments"]:
        if segment.get("editado"):
            segment.pop("words", None)

    result["paragraphs"] = ptbr.build_paragraphs(result["segments"])
    result["dialogue"] = ptbr.build_dialogue(result["segments"]) if result.get("diarization") else []
    result["cues"] = ptbr.build_cues(result["segments"])
    result["legendas_qa"] = ptbr.subtitle_report(result["cues"])
    result["full_text"] = "\n\n".join(result["paragraphs"])
    result["plain_text"] = ptbr.capitalize_sentences(
        ptbr.clean_text(" ".join(s["text"] for s in result["segments"]))
    )
    result["word_count"] = len(result["plain_text"].split())
    result["editado_em"] = time.time()
    if job.get("analisar", config.ANALYZE):
        try:
            result["analysis"] = analysis.analyze(result["segments"], result.get("duration", 0.0))
        except Exception:  # noqa: BLE001 - a análise é um extra
            pass

    base = _safe_name(job["filename"])
    job["result"] = result
    job["files"] = exporters.write_all(result, _job_dir(job_id), base)
    job["formats"] = exporters.available_formats(job["files"])
    job["detail"] = _completion_detail(result)
    _persist(job)
    store.index_segments(job_id, result.get("segments", []))

    return {
        "salvo": True,
        "trechos_alterados": alterados,
        "word_count": result["word_count"],
        "formats": job["formats"],
    }


@app.get("/api/download/{job_id}/{fmt}")
async def download(job_id: str, fmt: str):
    job = _require_job(job_id)
    if not job.get("files"):
        raise HTTPException(404, "Resultado não encontrado.")
    if fmt not in exporters.FORMATS or fmt not in job["files"]:
        raise HTTPException(400, f"Formato '{fmt}' indisponível para esta tarefa.")

    path = job["files"][fmt]
    if not os.path.exists(path):
        raise HTTPException(404, "O arquivo expirou ou foi removido.")

    _label, _ext, mime = exporters.FORMATS[fmt]
    return FileResponse(path, filename=os.path.basename(path), media_type=mime)


@app.get("/api/download/{job_id}")
async def download_all(job_id: str):
    """Empacota todos os formatos em um único ZIP."""
    job = _require_job(job_id)
    if not job.get("files"):
        raise HTTPException(404, "Resultado não encontrado.")

    base = _safe_name(job["filename"])
    zip_path = os.path.join(_job_dir(job_id), f"{base}_transcricao.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in job["files"].values():
            if os.path.exists(path):
                bundle.write(path, os.path.basename(path))
    return FileResponse(zip_path, filename=os.path.basename(zip_path), media_type="application/zip")


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    _valid_id(job_id)
    with jobs_lock:
        job = jobs.pop(job_id, None)
    if job is None:
        raise HTTPException(404, "Tarefa não encontrada.")
    cancel_events.setdefault(job_id, threading.Event()).set()
    shutil.rmtree(_job_dir(job_id), ignore_errors=True)
    store.delete_job(job_id)
    return {"removido": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")
