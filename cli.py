"""Transcrição pela linha de comando, sem abrir o servidor.

Serve para três coisas que a interface não faz bem:

* processar uma pasta inteira de uma vez, de madrugada, sem ninguém olhando;
* entrar num script maior (`transcrever && enviar_por_email`);
* rodar em máquina sem navegador — um servidor, por exemplo.

Uso típico:

    python cli.py reuniao.mp4
    python cli.py *.mp3 --perfil precisao --saida ./transcricoes
    python cli.py entrevista.wav --vocabulario "SISCOMEX, Dra. Marcela"
    python cli.py --buscar "contrato da prefeitura"
    python cli.py --ambiente
"""
import argparse
import glob
import os
import sys
import time
from typing import List

import config

if sys.platform == "win32":  # acentos no console do Windows
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def _barra(percent: float, largura: int = 34) -> str:
    cheio = int(largura * percent / 100)
    return "█" * cheio + "·" * (largura - cheio)


def _duracao(segundos: float) -> str:
    segundos = int(segundos or 0)
    horas, resto = divmod(segundos, 3600)
    minutos, segs = divmod(resto, 60)
    return f"{horas}h{minutos:02d}min" if horas else f"{minutos}min{segs:02d}s"


def _expandir(padroes: List[str]) -> List[str]:
    """Resolve curingas e pastas em uma lista de arquivos suportados."""
    arquivos: List[str] = []
    for padrao in padroes:
        if os.path.isdir(padrao):
            for nome in sorted(os.listdir(padrao)):
                caminho = os.path.join(padrao, nome)
                if os.path.isfile(caminho) and os.path.splitext(nome)[1].lower() in config.ALLOWED_EXTENSIONS:
                    arquivos.append(caminho)
            continue
        encontrados = glob.glob(padrao)
        arquivos.extend(encontrados if encontrados else [padrao])
    return [a for a in arquivos if os.path.isfile(a)]


def _mostrar_ambiente() -> int:
    import media
    import store
    from transcriber import Transcriber, model_is_cached

    print(f"Transcritor pt-BR {config.VERSION}")
    print(f"  Python .............. {sys.version.split()[0]}")
    print(f"  FFmpeg .............. {'sim' if media.ffmpeg_available() else 'NÃO ENCONTRADO'} ({media.find_ffmpeg()})")
    dispositivo, precisao = Transcriber._detect_device()
    print(f"  Aceleração .......... {dispositivo} ({precisao}), {os.cpu_count()} núcleos")
    print(f"  Banco de histórico .. {'ok' if store.available() else 'indisponível'} "
          f"({'FTS5' if store.fts_enabled() else 'LIKE'})")
    try:
        import numpy
        print(f"  numpy ............... {numpy.__version__} (diarização e forma de onda disponíveis)")
    except ImportError:
        print("  numpy ............... ausente (sem diarização nem forma de onda)")
    print("  Modelos:")
    for modelo in config.MODEL_CATALOG:
        marca = "em cache" if model_is_cached(modelo["id"]) else "será baixado no primeiro uso"
        padrao = "  <- padrão" if modelo["id"] == config.DEFAULT_MODEL else ""
        print(f"    - {modelo['id']:<16} {marca}{padrao}")
    totais = store.summary()
    if totais.get("tarefas"):
        print(f"  Histórico ........... {totais['tarefas']} transcrições, "
              f"{totais['horas_audio']} h de áudio, {totais['palavras']} palavras")
    return 0


def _buscar(termo: str) -> int:
    import store

    resultados = store.search(termo, limit=40)
    if not resultados:
        print("Nada encontrado.")
        return 1
    atual = None
    for item in resultados:
        if item["arquivo"] != atual:
            atual = item["arquivo"]
            print(f"\n{atual}")
        trecho = item["trecho"].replace("<mark>", "[").replace("</mark>", "]")
        minutos, segundos = divmod(int(item["start"]), 60)
        print(f"  {minutos:02d}:{segundos:02d}  {trecho}")
    print()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="Transcreve áudio e vídeo em português do Brasil, offline.",
    )
    parser.add_argument("arquivos", nargs="*", help="arquivos, curingas ou pastas")
    parser.add_argument("--modelo", default=config.DEFAULT_MODEL, choices=sorted(config.VALID_MODELS))
    parser.add_argument("--perfil", default=config.DEFAULT_PROFILE, choices=sorted(config.VALID_PROFILES))
    parser.add_argument("--idioma", default="pt", help="'pt', 'en', 'es' ou 'auto'")
    parser.add_argument("--vocabulario", default="", help="nomes e siglas separados por vírgula")
    parser.add_argument("--saida", default="", help="pasta de destino (padrão: outputs/<arquivo>)")
    parser.add_argument("--formatos", default="", help="lista separada por vírgula, ex.: txt,srt,docx")
    parser.add_argument("--sem-falantes", action="store_true", help="não separar falantes")
    parser.add_argument("--sem-analise", action="store_true", help="não gerar resumo nem capítulos")
    parser.add_argument("--limpar-vicios", action="store_true", help="remover 'né', 'tipo', hesitações")
    parser.add_argument("--silencioso", action="store_true", help="só imprime os caminhos gerados")
    parser.add_argument("--ambiente", action="store_true", help="mostra o diagnóstico e sai")
    parser.add_argument("--buscar", default="", help="procura um termo no histórico e sai")
    args = parser.parse_args(argv)

    if args.ambiente:
        return _mostrar_ambiente()
    if args.buscar:
        return _buscar(args.buscar)
    if not args.arquivos:
        parser.print_help()
        return 2

    import exporters
    import media
    import store
    from transcriber import Transcriber

    if not media.ffmpeg_available():
        print("FFmpeg não encontrado. Instale com: winget install Gyan.FFmpeg", file=sys.stderr)
        return 3

    arquivos = _expandir(args.arquivos)
    if not arquivos:
        print("Nenhum arquivo encontrado.", file=sys.stderr)
        return 4

    formatos = [f.strip() for f in args.formatos.split(",") if f.strip()] or None
    if formatos:
        invalidos = [f for f in formatos if f not in exporters.FORMATS]
        if invalidos:
            print(f"Formato desconhecido: {', '.join(invalidos)}", file=sys.stderr)
            return 5

    engine = Transcriber()
    falhas = 0

    for indice, caminho in enumerate(arquivos, start=1):
        nome = os.path.basename(caminho)
        base = os.path.splitext(nome)[0]
        destino = args.saida or os.path.join(config.OUTPUT_DIR, base)
        if args.saida and len(arquivos) > 1:
            destino = os.path.join(args.saida, base)

        if not args.silencioso:
            print(f"\n[{indice}/{len(arquivos)}] {nome}")

        ultimo = [0.0]

        def progresso(info):
            if args.silencioso:
                return
            # Redesenhar só a cada 0,4 s evita inundar terminais lentos.
            agora = time.monotonic()
            if info["percent"] < 100 and agora - ultimo[0] < 0.4:
                return
            ultimo[0] = agora
            texto = f"\r  {_barra(info['percent'])} {info['percent']:5.1f}%  {info['stage']}"
            sys.stdout.write(texto.ljust(96)[:96])
            sys.stdout.flush()

        try:
            resultado = engine.transcribe(
                input_path=caminho,
                output_dir=destino,
                model_name=args.modelo,
                profile=args.perfil,
                language=None if args.idioma == "auto" else args.idioma,
                vocabulary=args.vocabulario,
                on_progress=progresso,
                diarizar=not args.sem_falantes,
                analisar=not args.sem_analise,
                remover_vicios=args.limpar_vicios,
            )
        except Exception as exc:  # noqa: BLE001 - a CLI reporta e segue para o próximo
            falhas += 1
            print(f"\n  ERRO: {exc}", file=sys.stderr)
            continue

        caminhos = exporters.write_all(resultado, destino, base, only=formatos)

        if args.silencioso:
            for caminho_saida in caminhos.values():
                print(caminho_saida)
            continue

        falantes = (resultado.get("diarization") or {}).get("total") or 0
        print(f"\r  {_barra(100)} 100.0%  Concluído".ljust(96))
        print(f"  {resultado['word_count']} palavras · {_duracao(resultado['duration'])} de áudio "
              f"· {resultado['speed_factor']}× o tempo real"
              + (f" · {falantes} falante(s)" if falantes else ""))
        resumo = (resultado.get("analysis") or {}).get("resumo") or []
        if resumo:
            print("  Resumo:")
            for item in resumo[:3]:
                print(f"    · {item['texto'][:110]}")
        print(f"  {len(caminhos)} arquivo(s) em {destino}")

    store.close()
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
