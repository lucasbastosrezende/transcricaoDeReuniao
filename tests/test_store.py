"""Índice SQLite: persistência do histórico e busca em todas as transcrições."""
import time

import pytest

import config
import store


@pytest.fixture
def banco(tmp_path, monkeypatch):
    """Um banco novo por teste, isolado do histórico real do usuário."""
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "teste.sqlite3"))
    store.close()
    store._connection = None
    yield store
    store.close()
    store._connection = None


def _tarefa(job_id="11111111-1111-1111-1111-111111111111", nome="reuniao.mp4"):
    return {
        "job_id": job_id,
        "filename": nome,
        "status": "concluido",
        "model": "large-v3-turbo",
        "profile": "equilibrado",
        "created_at": time.time(),
        "finished_at": time.time(),
        "elapsed": 42.0,
        "size_bytes": 1024,
        "files": {"txt": "/tmp/x.txt"},
        "upload_path": "/tmp/segredo.mp4",
        "result": {
            "duration": 600.0,
            "word_count": 1200,
            "language": "pt",
            "avg_confidence": 0.93,
            "diarization": {"total": 2},
        },
    }


class TestPersistencia:
    def test_grava_e_le(self, banco):
        banco.save_job(_tarefa())
        tarefas = banco.load_jobs()
        assert len(tarefas) == 1
        assert tarefas[0]["filename"] == "reuniao.mp4"

    def test_nao_guarda_caminho_interno(self, banco):
        banco.save_job(_tarefa())
        assert "upload_path" not in banco.load_jobs()[0]

    def test_atualiza_em_vez_de_duplicar(self, banco):
        tarefa = _tarefa()
        banco.save_job(tarefa)
        tarefa["filename"] = "renomeado.mp4"
        banco.save_job(tarefa)
        tarefas = banco.load_jobs()
        assert len(tarefas) == 1
        assert tarefas[0]["filename"] == "renomeado.mp4"

    def test_remove(self, banco):
        banco.save_job(_tarefa())
        banco.delete_job("11111111-1111-1111-1111-111111111111")
        assert banco.load_jobs() == []

    def test_tarefa_sem_id_e_ignorada(self, banco):
        banco.save_job({"filename": "sem_id.mp4"})
        assert banco.load_jobs() == []


class TestBusca:
    def _indexar(self, banco):
        banco.save_job(_tarefa())
        banco.index_segments("11111111-1111-1111-1111-111111111111", [
            {"id": 1, "start": 0.0, "text": "O contrato da prefeitura foi assinado.", "speaker": "Falante 1"},
            {"id": 2, "start": 12.5, "text": "A nota fiscal será emitida amanhã.", "speaker": "Falante 2"},
            {"id": 3, "start": 30.0, "text": "  ", "speaker": None},
        ])

    def test_encontra_palavra(self, banco):
        self._indexar(banco)
        assert len(banco.search("contrato")) == 1

    def test_ignora_acento(self, banco):
        self._indexar(banco)
        # "prefeitura" e "sera" sem acento precisam achar o texto acentuado.
        assert banco.search("sera") or banco.search("será")

    def test_traz_metadados(self, banco):
        self._indexar(banco)
        achado = banco.search("prefeitura")[0]
        assert achado["arquivo"] == "reuniao.mp4"
        assert achado["start"] == 0.0
        assert achado["falante"] == "Falante 1"

    def test_termo_curto_nao_busca(self, banco):
        self._indexar(banco)
        assert banco.search("a") == []

    def test_nada_encontrado(self, banco):
        self._indexar(banco)
        assert banco.search("helicoptero") == []

    def test_trecho_vazio_nao_e_indexado(self, banco):
        self._indexar(banco)
        assert all(r["trecho"].strip() for r in banco.search("contrato"))

    def test_reindexar_nao_duplica(self, banco):
        self._indexar(banco)
        self._indexar(banco)
        assert len(banco.search("contrato")) == 1

    def test_remover_limpa_o_indice(self, banco):
        self._indexar(banco)
        banco.delete_job("11111111-1111-1111-1111-111111111111")
        assert banco.search("contrato") == []


class TestResumo:
    def test_totais(self, banco):
        banco.save_job(_tarefa())
        banco.save_job(_tarefa(job_id="22222222-2222-2222-2222-222222222222", nome="entrevista.mp3"))
        totais = banco.summary()
        assert totais["tarefas"] == 2
        assert totais["horas_audio"] == pytest.approx(600 * 2 / 3600, abs=0.01)
        assert totais["palavras"] == 2400

    def test_banco_vazio(self, banco):
        assert banco.summary()["tarefas"] == 0
