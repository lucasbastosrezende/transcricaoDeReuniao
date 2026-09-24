"""Transcrição crua: só o texto falado, num .txt, e mais nada.

Sem marcação de tempo, sem "Falante 1:", sem cabeçalho, sem resumo. É o modo
para quem só quer colar o conteúdo em outro lugar. Como pula a separação de
falantes e a análise, também é o modo mais rápido.

Uso:

    python transcricao_crua.py                 abre uma janela para escolher os arquivos
    python transcricao_crua.py reuniao.mp4     transcreve direto
    python transcricao_crua.py pasta/ --perfil rapido --saida ./textos

Cada arquivo vira `<nome>.txt` na pasta `outputs/texto_cru` (ou na indicada
em --saida). No fim, a pasta é aberta no Explorador de Arquivos.
"""
import argparse
import os
import subprocess
import sys
import tempfile
import time
from typing import List

import config
import exporters
from cli import _barra, _duracao, _expandir

if sys.platform == "win32":  # acentos no console do Windows
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

PASTA_PADRAO = os.path.join(config.OUTPUT_DIR, "texto_cru")


def _escolher_arquivos() -> List[str]:
    """Janela de seleção de arquivos; cai para o teclado se não houver interface gráfica."""
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError:
        return _perguntar_no_terminal()

    extensoes = " ".join(f"*{ext}" for ext in sorted(config.ALLOWED_EXTENSIONS))
    raiz = tkinter.Tk()
    raiz.withdraw()
    raiz.attributes("-topmost", True)  # sem isso a janela abre atrás do console
    escolhidos = filedialog.askopenfilenames(
        title="Escolha os áudios ou vídeos para transcrever",
        filetypes=[("Áudio e vídeo", extensoes), ("Todos os arquivos", "*.*")],
    )
    raiz.destroy()
    return list(escolhidos)


def _perguntar_no_terminal() -> List[str]:
    print("Arraste o arquivo para esta janela (ou digite o caminho) e tecle Enter:")
    linha = input("> ").strip().strip('"')
    return [linha] if linha else []


def _abrir_pasta(pasta: str) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(pasta)  # noqa: S606 - abre o Explorador na pasta de saída
        elif sys.platform == "darwin":
            subprocess.Popen(["open", pasta])
        else:
            subprocess.Popen(["xdg-open", pasta])
    except OSError:
        pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="transcricao_crua.py",
        description="Transcreve áudio e vídeo e grava só o texto, sem tempos nem falantes.",
    )
    parser.add_argument("arquivos", nargs="*", help="arquivos, curingas ou pastas (vazio: abre uma janela)")
    parser.add_argument("--perfil", default=config.DEFAULT_PROFILE, choices=sorted(config.VALID_PROFILES))
    parser.add_argument("--modelo", default=config.DEFAULT_MODEL, choices=sorted(config.VALID_MODELS))
    parser.add_argument("--idioma", default="pt", help="'pt', 'en', 'es' ou 'auto'")
    parser.add_argument("--vocabulario", default="", help="nomes e siglas separados por vírgula")
    parser.add_argument("--saida", default=PASTA_PADRAO, help=f"pasta de destino (padrão: {PASTA_PADRAO})")
    parser.add_argument("--limpar-vicios", action="store_true", help="remover 'né', 'tipo', hesitações")
    parser.add_argument("--nao-abrir-pasta", action="store_true", help="não abrir a pasta no fim")
    args = parser.parse_args(argv)

    print("\nTRANSCRIÇÃO CRUA — só o texto, sem tempos nem falantes\n")

    entrada = args.arquivos or _escolher_arquivos()
    arquivos = _expandir(entrada)
    if not arquivos:
        print("Nenhum arquivo escolhido.")
        return 4

    import media
    from transcriber import Transcriber

    if not media.ffmpeg_available():
        print("FFmpeg não encontrado. Instale com: winget install Gyan.FFmpeg", file=sys.stderr)
        return 3

    os.makedirs(args.saida, exist_ok=True)
    engine = Transcriber()
    gerados: List[str] = []

    for indice, caminho in enumerate(arquivos, start=1):
        nome = os.path.basename(caminho)
        base = os.path.splitext(nome)[0]
        print(f"[{indice}/{len(arquivos)}] {nome}")

        ultimo = [0.0]

        def progresso(info):
            agora = time.monotonic()
            if info["percent"] < 100 and agora - ultimo[0] < 0.4:
                return
            ultimo[0] = agora
            texto = f"\r  {_barra(info['percent'])} {info['percent']:5.1f}%  {info['stage']}"
            sys.stdout.write(texto.ljust(96)[:96])
            sys.stdout.flush()

        # O motor precisa de uma pasta para o WAV intermediário; ela some no fim.
        with tempfile.TemporaryDirectory(prefix="transcricao_crua_") as trabalho:
            try:
                resultado = engine.transcribe(
                    input_path=caminho,
                    output_dir=trabalho,
                    model_name=args.modelo,
                    profile=args.perfil,
                    language=None if args.idioma == "auto" else args.idioma,
                    vocabulary=args.vocabulario,
                    on_progress=progresso,
                    keep_audio_preview=False,
                    diarizar=False,
                    analisar=False,
                    remover_vicios=args.limpar_vicios,
                )
            except Exception as exc:  # noqa: BLE001 - reporta e segue para o próximo
                print(f"\n  ERRO: {exc}", file=sys.stderr)
                continue

        destino = os.path.join(args.saida, f"{base}.txt")
        with open(destino, "w", encoding="utf-8", newline="") as arquivo:
            arquivo.write(exporters.build_txt_raw(resultado))
        gerados.append(destino)

        print(f"\r  {_barra(100)} 100.0%  Concluído".ljust(96))
        print(f"  {resultado['word_count']} palavras · {_duracao(resultado['duration'])} de áudio")
        print(f"  -> {destino}\n")

    if gerados and not args.nao_abrir_pasta:
        _abrir_pasta(os.path.abspath(args.saida))
    return 0 if len(gerados) == len(arquivos) else 1


if __name__ == "__main__":
    raise SystemExit(main())
