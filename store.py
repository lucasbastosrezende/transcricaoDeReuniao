"""Índice persistente das transcrições, em SQLite.

Antes disso o histórico era uma varredura de `job.json` em disco: aceitável
com dez transcrições, insuportável com quinhentas — e sem nenhuma forma de
responder "em qual gravação eu falei sobre o contrato da prefeitura?".

O banco resolve as duas coisas. Os metadados vão para uma tabela comum e o
texto de cada trecho para uma tabela **FTS5**, o motor de busca textual que já
vem embutido no SQLite: procura por prefixo, por frase exata e por proximidade,
com acento ou sem acento, em milissegundos sobre centenas de horas de áudio.

Quando o Python foi compilado sem FTS5 (raro, mas acontece), a mesma API cai
para uma busca com `LIKE`. Mais lenta, porém correta — nada quebra.
"""
import json
import logging
import os
import sqlite3
import threading
import time
import unicodedata
from typing import Any, Dict, Iterable, List, Optional

import config

logger = logging.getLogger("store")

_lock = threading.RLock()
_connection: Optional[sqlite3.Connection] = None
_fts_enabled = False

_META_COLUMNS = (
    "job_id", "filename", "status", "model", "profile", "language",
    "created_at", "finished_at", "elapsed", "duration", "word_count",
    "speakers", "avg_confidence", "size_bytes",
)


def _fold(text: str) -> str:
    """Versão sem acento, usada pelo caminho de busca sem FTS5."""
    norm = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in norm if unicodedata.category(c) != "Mn")


def _has_fts5(connection: sqlite3.Connection) -> bool:
    try:
        connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(x)")
        connection.execute("DROP TABLE IF EXISTS _fts_probe")
        return True
    except sqlite3.Error:
        return False


def connect() -> sqlite3.Connection:
    """Abre (uma vez) a conexão compartilhada e garante o esquema."""
    global _connection, _fts_enabled
    with _lock:
        if _connection is not None:
            return _connection

        os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
        connection = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        # WAL deixa a leitura do histórico acontecer enquanto o trabalhador
        # ainda está gravando o resultado da transcrição anterior.
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")

        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id         TEXT PRIMARY KEY,
                filename       TEXT NOT NULL DEFAULT '',
                status         TEXT NOT NULL DEFAULT '',
                model          TEXT NOT NULL DEFAULT '',
                profile        TEXT NOT NULL DEFAULT '',
                language       TEXT,
                created_at     REAL NOT NULL DEFAULT 0,
                finished_at    REAL,
                elapsed        REAL,
                duration       REAL,
                word_count     INTEGER DEFAULT 0,
                speakers       INTEGER DEFAULT 0,
                avg_confidence REAL DEFAULT 0,
                size_bytes     INTEGER DEFAULT 0,
                payload        TEXT NOT NULL DEFAULT '{}',
                updated_at     REAL NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created_at DESC);
            """
        )

        _fts_enabled = _has_fts5(connection)
        if _fts_enabled:
            connection.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(
                    text,
                    job_id UNINDEXED,
                    seg_id UNINDEXED,
                    start UNINDEXED,
                    speaker UNINDEXED,
                    tokenize = "unicode61 remove_diacritics 2"
                );
                """
            )
        else:
            logger.warning("SQLite sem FTS5: a busca no histórico usará LIKE.")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS segments_plain (
                    job_id  TEXT NOT NULL,
                    seg_id  INTEGER NOT NULL,
                    start   REAL NOT NULL DEFAULT 0,
                    speaker TEXT,
                    text    TEXT NOT NULL,
                    folded  TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS segments_plain_job ON segments_plain(job_id);
                """
            )
        connection.commit()
        _connection = connection
        return connection


def available() -> bool:
    try:
        connect()
        return True
    except sqlite3.Error as exc:  # pragma: no cover - disco cheio, permissão...
        logger.warning("Banco indisponível: %s", exc)
        return False


def fts_enabled() -> bool:
    connect()
    return _fts_enabled


# --- Escrita ------------------------------------------------------------

def save_job(job: Dict[str, Any]) -> None:
    """Insere ou atualiza os metadados de uma tarefa."""
    if not job.get("job_id"):
        return
    result = job.get("result") or {}
    payload = {
        key: value for key, value in job.items()
        if key not in ("result", "live_segments", "upload_path")
    }
    values = {
        "job_id": job["job_id"],
        "filename": job.get("filename") or "",
        "status": job.get("status") or "",
        "model": job.get("model") or "",
        "profile": job.get("profile") or "",
        "language": result.get("language") or job.get("language"),
        "created_at": float(job.get("created_at") or 0),
        "finished_at": job.get("finished_at"),
        "elapsed": job.get("elapsed"),
        "duration": result.get("duration"),
        "word_count": int(result.get("word_count") or 0),
        "speakers": int((result.get("diarization") or {}).get("total") or 0),
        "avg_confidence": float(result.get("avg_confidence") or 0),
        "size_bytes": int(job.get("size_bytes") or 0),
        "payload": json.dumps(payload, ensure_ascii=False),
        "updated_at": time.time(),
    }
    columns = ", ".join(values)
    placeholders = ", ".join(f":{name}" for name in values)
    updates = ", ".join(f"{name}=excluded.{name}" for name in values if name != "job_id")

    try:
        with _lock:
            connection = connect()
            connection.execute(
                f"INSERT INTO jobs ({columns}) VALUES ({placeholders}) "
                f"ON CONFLICT(job_id) DO UPDATE SET {updates}",
                values,
            )
            connection.commit()
    except sqlite3.Error as exc:
        logger.warning("Falha ao gravar a tarefa %s: %s", job.get("job_id"), exc)


def index_segments(job_id: str, segments: Iterable[Dict[str, Any]]) -> None:
    """Reindexa o texto de uma transcrição para a busca global."""
    rows = [
        (
            segment.get("text", ""),
            job_id,
            int(segment.get("id") or index + 1),
            float(segment.get("start") or 0.0),
            segment.get("speaker") or "",
        )
        for index, segment in enumerate(segments)
        if segment.get("text", "").strip()
    ]
    try:
        with _lock:
            connection = connect()
            if _fts_enabled:
                connection.execute("DELETE FROM segments_fts WHERE job_id = ?", (job_id,))
                connection.executemany(
                    "INSERT INTO segments_fts (text, job_id, seg_id, start, speaker) "
                    "VALUES (?, ?, ?, ?, ?)",
                    rows,
                )
            else:
                connection.execute("DELETE FROM segments_plain WHERE job_id = ?", (job_id,))
                connection.executemany(
                    "INSERT INTO segments_plain (text, job_id, seg_id, start, speaker, folded) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [row + (_fold(row[0]),) for row in rows],
                )
            connection.commit()
    except sqlite3.Error as exc:
        logger.warning("Falha ao indexar a tarefa %s: %s", job_id, exc)


def delete_job(job_id: str) -> None:
    try:
        with _lock:
            connection = connect()
            connection.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
            table = "segments_fts" if _fts_enabled else "segments_plain"
            connection.execute(f"DELETE FROM {table} WHERE job_id = ?", (job_id,))
            connection.commit()
    except sqlite3.Error as exc:
        logger.warning("Falha ao remover a tarefa %s: %s", job_id, exc)


# --- Leitura ------------------------------------------------------------

def load_jobs(limit: int = 500) -> List[Dict[str, Any]]:
    """Devolve os metadados salvos, do mais recente para o mais antigo."""
    try:
        with _lock:
            connection = connect()
            cursor = connection.execute(
                "SELECT payload FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            )
            rows = cursor.fetchall()
    except sqlite3.Error as exc:
        logger.warning("Falha ao ler o histórico: %s", exc)
        return []

    jobs = []
    for row in rows:
        try:
            jobs.append(json.loads(row["payload"]))
        except (TypeError, ValueError):
            continue
    return jobs


def summary() -> Dict[str, Any]:
    """Totais agregados de tudo que já foi transcrito nesta máquina."""
    try:
        with _lock:
            connection = connect()
            row = connection.execute(
                "SELECT COUNT(*) AS tarefas, "
                "       COALESCE(SUM(duration), 0) AS segundos, "
                "       COALESCE(SUM(word_count), 0) AS palavras, "
                "       COALESCE(SUM(elapsed), 0) AS processamento, "
                "       COALESCE(AVG(avg_confidence), 0) AS confianca "
                "FROM jobs WHERE status = 'concluido'"
            ).fetchone()
    except sqlite3.Error:
        return {}

    segundos = float(row["segundos"] or 0)
    processamento = float(row["processamento"] or 0)
    return {
        "tarefas": int(row["tarefas"] or 0),
        "horas_audio": round(segundos / 3600, 2),
        "palavras": int(row["palavras"] or 0),
        "horas_processamento": round(processamento / 3600, 2),
        "fator_velocidade": round(segundos / processamento, 2) if processamento > 1 else 0.0,
        "confianca_media": round(float(row["confianca"] or 0), 3),
        "busca_textual": "fts5" if _fts_enabled else "like",
    }


def _fts_query(text: str) -> str:
    """Converte a busca digitada em uma consulta FTS5 segura.

    Aspas viram busca por frase exata; o resto vira prefixo, para que "contrat"
    encontre "contrato" e "contratação" sem o usuário precisar saber disso.
    """
    text = text.strip()
    if text.startswith('"') and text.endswith('"') and len(text) > 2:
        inner = text[1:-1].replace('"', "")
        return f'"{inner}"'
    terms = []
    for raw in text.split():
        cleaned = "".join(c for c in raw if c.isalnum() or c in "-_")
        if cleaned:
            terms.append(f'"{cleaned}"*')
    return " ".join(terms)


def search(query: str, limit: int = 60) -> List[Dict[str, Any]]:
    """Busca em todas as transcrições e devolve o trecho onde o termo aparece."""
    query = (query or "").strip()
    if len(query) < 2:
        return []

    try:
        with _lock:
            connection = connect()
            if _fts_enabled:
                expression = _fts_query(query)
                if not expression:
                    return []
                rows = connection.execute(
                    "SELECT f.job_id, f.seg_id, f.start, f.speaker, "
                    "       snippet(segments_fts, 0, '<mark>', '</mark>', '…', 14) AS trecho, "
                    "       j.filename, j.created_at "
                    "FROM segments_fts f LEFT JOIN jobs j ON j.job_id = f.job_id "
                    "WHERE segments_fts MATCH ? "
                    "ORDER BY bm25(segments_fts), f.start LIMIT ?",
                    (expression, limit),
                ).fetchall()
            else:
                needle = f"%{_fold(query)}%"
                rows = connection.execute(
                    "SELECT p.job_id, p.seg_id, p.start, p.speaker, p.text AS trecho, "
                    "       j.filename, j.created_at "
                    "FROM segments_plain p LEFT JOIN jobs j ON j.job_id = p.job_id "
                    "WHERE p.folded LIKE ? ORDER BY j.created_at DESC, p.start LIMIT ?",
                    (needle, limit),
                ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("Busca falhou: %s", exc)
        return []

    return [
        {
            "job_id": row["job_id"],
            "seg_id": row["seg_id"],
            "start": row["start"],
            "falante": row["speaker"] or None,
            "trecho": row["trecho"],
            "arquivo": row["filename"] or "(removido)",
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def close() -> None:
    global _connection
    with _lock:
        if _connection is not None:
            try:
                _connection.commit()
                _connection.close()
            except sqlite3.Error:
                pass
            _connection = None
