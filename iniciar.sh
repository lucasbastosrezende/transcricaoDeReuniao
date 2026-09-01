#!/usr/bin/env bash
# Equivalente ao iniciar.bat para Linux e macOS.
#
#   ./iniciar.sh              sobe o servidor em http://localhost:8000
#   ./iniciar.sh --testes     roda a bateria de testes
#   ./iniciar.sh cli.py a.mp4 executa qualquer script no ambiente preparado
set -euo pipefail

cd "$(dirname "$0")"

VENV=".venv"
PY="$VENV/bin/python"

echo
echo "======================================================================"
echo "   TRANSCRITOR pt-BR  -  100% gratuito, offline e sem limites"
echo "======================================================================"
echo

precisa_instalar=0

# Um ambiente pode existir e não funcionar (Python base removido ou movido).
if [ -x "$PY" ] && "$PY" -c "import sys" >/dev/null 2>&1; then
  echo "[ok] Ambiente Python encontrado."
else
  [ -d "$VENV" ] && { echo "[!!] Ambiente quebrado. Recriando..."; rm -rf "$VENV"; }
  echo "[..] Criando o ambiente Python..."
  if command -v uv >/dev/null 2>&1; then
    uv venv --python 3.11 "$VENV"
  else
    python3 -m venv "$VENV"
  fi
  precisa_instalar=1
fi

# Reinstala sozinho quando o requirements.txt muda.
if [ "$precisa_instalar" -eq 0 ] && ! cmp -s requirements.txt "$VENV/requirements.lock"; then
  echo "[..] O requirements.txt mudou desde a última execução."
  precisa_instalar=1
fi

if [ "$precisa_instalar" -eq 1 ]; then
  echo "[..] Instalando as dependências (alguns minutos na primeira vez)..."
  if command -v uv >/dev/null 2>&1; then
    uv pip install --python "$PY" -r requirements.txt
  else
    "$PY" -m pip install --upgrade pip
    "$PY" -m pip install -r requirements.txt
  fi
  cp requirements.txt "$VENV/requirements.lock"
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "[!!] FFmpeg não encontrado. Instale com:"
  echo "     Ubuntu/Debian:  sudo apt install ffmpeg"
  echo "     macOS:          brew install ffmpeg"
  exit 1
fi

if [ "${1:-}" = "--testes" ]; then
  "$PY" -m pip install --quiet pytest
  exec "$PY" -m pytest -q
fi

if [ $# -gt 0 ]; then
  exec "$PY" "$@"
fi

"$PY" test_setup.py
echo
echo "[ok] Servidor em http://localhost:8000  (Ctrl+C para encerrar)"
echo
exec "$PY" app.py
