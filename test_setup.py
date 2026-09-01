"""Verificação do ambiente: roda antes de abrir o programa pela primeira vez.

Uso:  .venv\\Scripts\\python.exe test_setup.py

Cada item marcado com [!!] impede o programa de funcionar; os marcados com
[--] só desligam um recurso opcional e não travam a inicialização.
"""
import os
import shutil
import sqlite3
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

OK = "[ok]"
FALHA = "[!!]"
AVISO = "[--]"
falhas = 0


def verificar(titulo: str, condicao: bool, detalhe: str = "", dica: str = "",
              obrigatorio: bool = True) -> None:
    global falhas
    marca = OK if condicao else (FALHA if obrigatorio else AVISO)
    print(f"{marca} {titulo}" + (f": {detalhe}" if detalhe else ""))
    if not condicao:
        if obrigatorio:
            falhas += 1
        if dica:
            print(f"     -> {dica}")


def _fts5_disponivel() -> bool:
    try:
        conexao = sqlite3.connect(":memory:")
        conexao.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        conexao.close()
        return True
    except sqlite3.Error:
        return False


def main() -> int:
    print("=" * 66)
    print(" VERIFICACAO DO AMBIENTE - TRANSCRITOR pt-BR")
    print("=" * 66)

    verificar("Python", sys.version_info >= (3, 9), sys.version.split()[0],
              "Use Python 3.9 ou mais novo.")

    try:
        import faster_whisper
        verificar("faster-whisper", True, faster_whisper.__version__)
        from faster_whisper import BatchedInferencePipeline  # noqa: F401
        verificar("Processamento em lote", True, "BatchedInferencePipeline disponivel")
    except ImportError as exc:
        verificar("faster-whisper", False, str(exc), "pip install -r requirements.txt")

    for modulo in ("fastapi", "uvicorn", "jinja2", "multipart", "ctranslate2", "numpy"):
        try:
            mod = __import__(modulo)
            verificar(modulo, True, getattr(mod, "__version__", "instalado"))
        except ImportError:
            obrigatorio = modulo != "numpy"
            verificar(modulo, False, "ausente",
                      "pip install -r requirements.txt"
                      if obrigatorio else "sem numpy nao ha diarizacao nem forma de onda",
                      obrigatorio=obrigatorio)

    import media
    verificar("FFmpeg", media.ffmpeg_available(), media.find_ffmpeg(),
              "Instale com: winget install Gyan.FFmpeg")
    verificar("FFprobe", shutil.which("ffprobe") is not None or os.path.exists(media.find_ffprobe()),
              media.find_ffprobe(), "Vem junto com o FFmpeg.")

    import config
    import store
    import transcriber

    verificar("Busca no historico (SQLite FTS5)", _fts5_disponivel(),
              "fts5" if _fts5_disponivel() else "ausente",
              "A busca global cai para LIKE, mais lenta porem funcional.",
              obrigatorio=False)
    verificar("Banco de historico", store.available(), config.DB_PATH,
              "Verifique a permissao de escrita na pasta outputs.", obrigatorio=False)

    motor_cpu = transcriber.Transcriber._detect_device()
    print(f"{OK} Aceleracao: {motor_cpu[0]} ({motor_cpu[1]}), {os.cpu_count()} nucleos")

    print("\nRecursos ligados:")
    for nome, ligado in (
        ("separacao de falantes", config.DIARIZE),
        ("resumo e capitulos", config.ANALYZE),
        ("tratamento do audio", config.AUDIO_PREPROCESS),
        ("token de autenticacao", bool(config.AUTH_TOKEN)),
    ):
        print(f"  - {nome:<26} {'sim' if ligado else 'nao'}")

    print("\nModelos:")
    for modelo in config.MODEL_CATALOG:
        baixado = transcriber.model_is_cached(modelo["id"])
        marca = "em cache" if baixado else "sera baixado no primeiro uso"
        padrao = " <- padrao" if modelo["id"] == config.DEFAULT_MODEL else ""
        print(f"  - {modelo['id']:<16} {marca}{padrao}")

    print("\n" + "=" * 66)
    if falhas:
        print(f" {falhas} problema(s) encontrado(s). Resolva antes de iniciar.")
    else:
        print(" Tudo pronto. Execute iniciar.bat")
    print("=" * 66)
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
