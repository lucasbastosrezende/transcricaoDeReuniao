@echo off
:: Gera o rodar-transcricao.exe na raiz do projeto a partir do RodarTranscricao.cs.
:: Usa o compilador C# que acompanha o .NET Framework 4 do proprio Windows.
cd /d "%~dp0"
set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%CSC%" set "CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
if not exist "%CSC%" (
    echo [ERRO] csc.exe do .NET Framework 4 nao encontrado.
    exit /b 1
)
"%CSC%" /nologo /target:exe /optimize+ /out:..\rodar-transcricao.exe RodarTranscricao.cs
