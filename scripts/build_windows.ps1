$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $PSScriptRoot
Set-Location $project

& py -3.11 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements-build-windows.txt
& .\.venv\Scripts\python.exe -m pytest -q
& .\.venv\Scripts\pyinstaller.exe --noconfirm --clean --windowed --name MyTradeBot --collect-all PySide6 --collect-all MetaTrader5 main.py

Write-Host "Application bundle: $project\dist\MyTradeBot"
Write-Host "Install Inno Setup, then compile installer\mytradebot.iss to create the installer."
