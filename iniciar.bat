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
if not exist "%PY%" goto CRIAR_AMBIENTE

:: Um .venv pode existir e nao funcionar: basta a instalacao do Python que
:: ele aponta ter sido movida, renomeada ou removida. Testar de verdade e
:: mais barato que descobrir isso no meio de uma transcricao de duas horas.
"%PY%" -c "import sys" > NUL 2>&1
if errorlevel 1 (
    echo [!!] O ambiente Python existe mas nao funciona ^(instalacao base removida?^).
    echo [..] Recriando do zero...
    rmdir /s /q .venv
    goto CRIAR_AMBIENTE
)
echo [ok] Ambiente Python encontrado.
goto CONFERIR_DEPENDENCIAS

:CRIAR_AMBIENTE
echo [..] Criando o ambiente Python...

if exist "%UV%" goto USAR_UV

where python > NUL 2>&1
if %errorlevel% equ 0 (
    python -m venv .venv
    if exist "%PY%" (
        "%PY%" -m pip install --upgrade pip
        goto INSTALAR_DEPENDENCIAS
    )
)

echo [..] Python nao encontrado. Instalando o gerenciador uv...
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

:USAR_UV
"%UV%" venv --python 3.11 .venv
if not exist "%PY%" (
    echo.
    echo [ERRO] Nao foi possivel preparar o ambiente Python.
    pause
    exit /b 1
)

:INSTALAR_DEPENDENCIAS
echo [..] Instalando as dependencias ^(alguns minutos na primeira vez^)...
if exist "%UV%" (
    "%UV%" pip install --python "%PY%" -r requirements.txt
) else (
    "%PY%" -m pip install -r requirements.txt
)
if errorlevel 1 (
    echo.
    echo [ERRO] A instalacao das dependencias falhou. Verifique sua conexao.
    pause
    exit /b 1
)
copy /y requirements.txt ".venv\requirements.lock" > NUL
goto FFMPEG

:CONFERIR_DEPENDENCIAS
:: Comparacao binaria com a copia guardada na ultima instalacao: se o
:: requirements.txt mudou, as dependencias sao reinstaladas sozinhas.
if not exist ".venv\requirements.lock" goto INSTALAR_DEPENDENCIAS
fc /b requirements.txt ".venv\requirements.lock" > NUL 2>&1
if errorlevel 1 (
    echo [..] O requirements.txt mudou desde a ultima execucao.
    goto INSTALAR_DEPENDENCIAS
)

:: ---------------------------------------------------------------- FFmpeg
:FFMPEG
where ffmpeg > NUL 2>&1
if %errorlevel% neq 0 (
    if not exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe" (
        echo [..] FFmpeg nao encontrado. Instalando via WinGet...
        winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
    )
)

:: --------------------------------------------------------------- Verifica
echo.
"%PY%" test_setup.py
if errorlevel 1 (
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
