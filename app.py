"""Servidor web do Transcritor pt-BR.

As tarefas passam por uma fila com um único trabalhador. Isso é proposital:
transcrição satura todos os núcleos da CPU, então rodar duas ao mesmo tempo
deixaria as duas mais lentas do que rodá-las em sequência.
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
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

import config
import exporters
import media
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

app = FastAPI(title="Transcritor pt-BR", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
templates = Jinja2Templates(directory=config.TEMPLATES_DIR)

engine = Transcriber()

# --- Estado das tarefas -------------------------------------------------
jobs: Dict[str, Dict[str, Any]] = {}
jobs_lock = threading.Lock()
work_queue: "queue.Queue[str]" = queue.Queue()
cancel_events: Dict[str, threading.Event] = {}


def _safe_name(name: str) -> str:
    """Reduz o nome do arquivo ao que é seguro usar em disco e em cabeçalhos."""
    stem = os.path.splitext(os.path.basename(name))[0]
    stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode("ascii")
    stem = re.sub(r"[^\w\s.-]", "", stem).strip().replace(" ", "_")
    stem = re.sub(r"_{2,}", "_", stem)
    return stem[:80] or "transcricao"


def _job_dir(job_id: str) -> str:
    return os.path.join(config.OUTPUT_DIR, job_id)


def _persist(job: Dict[str, Any]) -> None:
    """Salva a tarefa em disco para que sobreviva a um reinício do servidor.

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


def _public_view(job: Dict[str, Any], include_result: bool = True) -> Dict[str, Any]:
    view = {k: v for k, v in job.items() if k != "result"}
    view["segment_count"] = len(job.get("live_segments", []))
    view.pop("live_segments", None)
    if include_result:
        view["result"] = job.get("result")
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
        )

        base = _safe_name(job["filename"])
        paths = exporters.write_all(result, _job_dir(job_id), base)

        job["result"] = result
        job["files"] = paths
        job["formats"] = exporters.available_formats(paths)
        job["status"] = "concluido"
        job["percent"] = 100.0
        job["stage"] = "Concluído"
        job["detail"] = f"{result['word_count']} palavras em {len(result['segments'])} trechos."
        job["finished_at"] = time.time()
        job["elapsed"] = round(job["finished_at"] - job["started_at"], 1)
        job["eta_seconds"] = 0
        if result["duration"] > 0:
            job["speed_factor"] = round(result["duration"] / max(1.0, job["elapsed"]), 2)

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


def _worker_loop() -> None:
    while True:
        job_id = work_queue.get()
        try:
            _run_job(job_id)
        finally:
            work_queue.task_done()


threading.Thread(target=_worker_loop, name="transcricao", daemon=True).start()


# --- Limpeza e restauração ---------------------------------------------

def _restore_jobs() -> None:
    """Recarrega transcrições anteriores para que fiquem no histórico."""
    if not os.path.isdir(config.OUTPUT_DIR):
        return
    for entry in os.listdir(config.OUTPUT_DIR):
        path = os.path.join(config.OUTPUT_DIR, entry, "job.json")
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                job = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        # Tarefas interrompidas por um desligamento não podem ser retomadas.
        if job.get("status") in ("processando", "na_fila"):
            job["status"] = "erro"
            job["stage"] = "Interrompido"
            job["detail"] = "O servidor foi encerrado durante o processamento."
        job.setdefault("live_segments", [])

        # A transcrição em si é relida do JSON exportado, sob demanda.
        job["result"] = None
        exportado = job.get("files", {}).get("json")
        if exportado and os.path.exists(exportado):
            try:
                with open(exportado, encoding="utf-8") as handle:
                    job["result"] = json.load(handle)
            except (OSError, json.JSONDecodeError):
                pass

        jobs[job["job_id"]] = job


def _cleanup_old_files() -> None:
    """Apaga resultados vencidos e uploads órfãos de execuções anteriores."""
    if config.RETENTION_HOURS > 0:
        deadline = time.time() - config.RETENTION_HOURS * 3600
        for entry in os.listdir(config.OUTPUT_DIR):
            folder = os.path.join(config.OUTPUT_DIR, entry)
            if os.path.isdir(folder) and os.path.getmtime(folder) < deadline:
                shutil.rmtree(folder, ignore_errors=True)
                jobs.pop(entry, None)

    for entry in os.listdir(config.UPLOAD_DIR):
        path = os.path.join(config.UPLOAD_DIR, entry)
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _cleanup_old_files()
    _restore_jobs()
    if not media.ffmpeg_available():
        logger.warning("FFmpeg não encontrado. Instale com: winget install Gyan.FFmpeg")
    logger.info("Pronto em http://%s:%d", config.HOST, config.PORT)
    yield
    engine.unload()


app.router.lifespan_context = lifespan


# --- Rotas --------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/system")
async def system_info():
    """Informa à interface o hardware, os modelos e o que já está baixado."""
    catalog = [
        {**m, "baixado": model_is_cached(m["id"]), "padrao": m["id"] == config.DEFAULT_MODEL}
        for m in config.MODEL_CATALOG
    ]
    return {
        "hardware": engine.describe_hardware(),
        "ffmpeg": media.ffmpeg_available(),
        "modelos": catalog,
        "perfis": [
            {"id": key, "nome": value["nome"], "resumo": value["resumo"],
             "padrao": key == config.DEFAULT_PROFILE}
            for key, value in config.PROFILES.items()
        ],
        "extensoes": sorted(config.ALLOWED_EXTENSIONS),
    }


@app.get("/api/jobs")
async def list_jobs():
    with jobs_lock:
        items = [_public_view(job, include_result=False) for job in jobs.values()]
    items.sort(key=lambda j: j.get("created_at", 0), reverse=True)
    return {"jobs": items[:50]}


@app.post("/api/transcribe")
async def start_transcription(
    file: UploadFile = File(...),
    model_name: str = Form(config.DEFAULT_MODEL),
    profile: str = Form(config.DEFAULT_PROFILE),
    language: str = Form("pt"),
    vocabulary: str = Form(""),
):
    if not file.filename:
        raise HTTPException(400, "Nenhum arquivo foi enviado.")

    extension = os.path.splitext(file.filename)[1].lower()
    if extension not in config.ALLOWED_EXTENSIONS:
        raise HTTPException(
            400,
            f"O formato {extension or 'desconhecido'} não é suportado. "
            f"Aceitos: {', '.join(sorted(config.ALLOWED_EXTENSIONS))}",
        )
    if not media.ffmpeg_available():
        raise HTTPException(
            503,
            "O FFmpeg não foi encontrado. Instale com: winget install Gyan.FFmpeg",
        )

    model_name = model_name if model_name in config.VALID_MODELS else config.DEFAULT_MODEL
    profile = profile if profile in config.VALID_PROFILES else config.DEFAULT_PROFILE

    job_id = str(uuid.uuid4())
    upload_path = os.path.join(config.UPLOAD_DIR, f"{job_id}{extension}")

    size = 0
    try:
        with open(upload_path, "wb") as buffer:
            while chunk := await file.read(config.UPLOAD_CHUNK_SIZE):
                buffer.write(chunk)
                size += len(chunk)
    except OSError as exc:
        if os.path.exists(upload_path):
            os.remove(upload_path)
        raise HTTPException(500, f"Erro ao gravar o arquivo enviado: {exc}") from exc

    if size == 0:
        os.remove(upload_path)
        raise HTTPException(400, "O arquivo enviado está vazio.")

    job = {
        "job_id": job_id,
        "filename": file.filename,
        "size_bytes": size,
        "upload_path": upload_path,
        "model": model_name,
        "profile": profile,
        "language": None if language in ("auto", "") else language,
        "vocabulary": vocabulary[:2000],
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

    with jobs_lock:
        jobs[job_id] = job
    work_queue.put(job_id)
    cancel_events[job_id] = threading.Event()

    return {"job_id": job_id, "filename": file.filename, "queue_position": _queue_position(job_id)}


@app.get("/api/progress/{job_id}")
async def job_progress(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Tarefa não encontrada.")
    return _public_view(job)


@app.get("/api/events/{job_id}")
async def job_events(job_id: str):
    """Fluxo SSE que entrega progresso e os trechos já transcritos.

    Enviar os segmentos conforme saem faz o texto aparecer durante o
    processamento, em vez de tudo de uma vez só no final.
    """
    if job_id not in jobs:
        raise HTTPException(404, "Tarefa não encontrada.")

    async def stream():
        sent = 0
        last_signature: Optional[tuple] = None
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

            if job["status"] in ("concluido", "erro", "cancelado"):
                final = {
                    "type": "fim",
                    "status": job["status"],
                    "error": job.get("error"),
                    "result": job.get("result"),
                    "formats": job.get("formats", []),
                    "elapsed": job.get("elapsed"),
                    "speed_factor": job.get("speed_factor"),
                }
                yield f"data: {json.dumps(final, ensure_ascii=False)}\n\n"
                return

            await asyncio.sleep(0.4)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/cancel/{job_id}")
async def cancel_job(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Tarefa não encontrada.")
    if job["status"] in ("concluido", "erro", "cancelado"):
        return {"cancelado": False, "motivo": "A tarefa já terminou."}
    cancel_events.setdefault(job_id, threading.Event()).set()
    job["stage"] = "Cancelando"
    job["detail"] = "Encerrando o processamento..."
    return {"cancelado": True}


@app.get("/api/media/{job_id}")
async def job_media(job_id: str):
    """Serve o áudio leve usado pelo player sincronizado da interface."""
    job = jobs.get(job_id)
    if job is None or not job.get("result"):
        raise HTTPException(404, "Áudio não disponível.")
    name = job["result"].get("audio_preview")
    if not name:
        raise HTTPException(404, "Áudio não disponível.")
    path = os.path.join(_job_dir(job_id), name)
    if not os.path.exists(path):
        raise HTTPException(404, "Áudio não disponível.")
    return FileResponse(path, media_type="audio/mp4")


@app.get("/api/download/{job_id}/{fmt}")
async def download(job_id: str, fmt: str):
    job = jobs.get(job_id)
    if job is None or not job.get("files"):
        raise HTTPException(404, "Resultado não encontrado.")
    if fmt not in exporters.FORMATS or fmt not in job["files"]:
        raise HTTPException(400, f"Formato '{fmt}' indisponível para esta tarefa.")

    path = job["files"][fmt]
    if not os.path.exists(path):
        raise HTTPException(404, "O arquivo expirou ou foi removido.")

    _label, ext, mime = exporters.FORMATS[fmt]
    return FileResponse(path, filename=os.path.basename(path), media_type=mime)


@app.get("/api/download/{job_id}")
async def download_all(job_id: str):
    """Empacota todos os formatos em um único ZIP."""
    job = jobs.get(job_id)
    if job is None or not job.get("files"):
        raise HTTPException(404, "Resultado não encontrado.")

    import zipfile

    base = _safe_name(job["filename"])
    zip_path = os.path.join(_job_dir(job_id), f"{base}_transcricao.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in job["files"].values():
            if os.path.exists(path):
                bundle.write(path, os.path.basename(path))
    return FileResponse(zip_path, filename=os.path.basename(zip_path), media_type="application/zip")


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    job = jobs.pop(job_id, None)
    if job is None:
        raise HTTPException(404, "Tarefa não encontrada.")
    cancel_events.setdefault(job_id, threading.Event()).set()
    shutil.rmtree(_job_dir(job_id), ignore_errors=True)
    return {"removido": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")
