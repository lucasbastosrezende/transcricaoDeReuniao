@echo off
chcp 65001 > NUL
setlocal EnableDelayedExpansion
title Transcritor de Videos e Audios - Portugues do Brasil

cd /d "%~dp0"

echo.
echo ======================================================================
echo    TRANSCRITOR pt-BR  -  100%% gratuito, offline e sem limites
echo ======================================================================
echo.

set "UV=%USERPROFILE%\.local\bin\uv.exe"
set "PY=.venv\Scripts\python.exe"

:: ---------------------------------------------------------------- Python
if exist "%PY%" (
    echo [ok] Ambiente Python encontrado.
    goto FFMPEG
)

echo [..] Criando o ambiente Python pela primeira vez...

if exist "%UV%" goto USAR_UV

where python >nul 2>nul
if %errorlevel% equ 0 (
    python -m venv .venv
    "%PY%" -m pip install --upgrade pip
    "%PY%" -m pip install -r requirements.txt
    goto FFMPEG
)

echo [..] Python nao encontrado. Instalando o gerenciador uv...
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

:USAR_UV
"%UV%" venv --python 3.11 .venv
"%UV%" pip install --python "%PY%" -r requirements.txt

if not exist "%PY%" (
    echo.
    echo [ERRO] Nao foi possivel preparar o ambiente Python.
    pause
    exit /b 1
)

:: ---------------------------------------------------------------- FFmpeg
:FFMPEG
where ffmpeg >nul 2>nul
if %errorlevel% neq 0 (
    if not exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe" (
        echo [..] FFmpeg nao encontrado. Instalando via WinGet...
        winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
    )
)

:: --------------------------------------------------------------- Verifica
echo.
"%PY%" test_setup.py
if %errorlevel% neq 0 (
    echo.
    echo Resolva os itens marcados com [!!] acima e execute novamente.
    pause
    exit /b 1
)

:: --------------------------------------------------------------- Servidor
echo.
echo [ok] Abrindo http://localhost:8000 no navegador...
echo      Para encerrar, feche esta janela ou pressione Ctrl+C.
echo.

start "" http://localhost:8000
"%PY%" app.py

echo.
echo Servidor encerrado.
pause
