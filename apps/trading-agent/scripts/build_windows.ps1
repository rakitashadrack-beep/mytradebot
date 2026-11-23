$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $PSScriptRoot
$repository = Split-Path -Parent (Split-Path -Parent $project)
Set-Location $project

& py -3.11 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements-build-windows.txt
$env:PYTHONPATH = $project
& .\.venv\Scripts\python.exe -m pytest -q "$repository\tests"
& .\.venv\Scripts\pyinstaller.exe --noconfirm --clean --windowed --name MyTradeBot --collect-all PySide6 --collect-all MetaTrader5 --add-data "web\templates;web\templates" --add-data "web\static;web\static" main.py

Write-Host "Application bundle: $project\dist\MyTradeBot"
Write-Host "Install Inno Setup, then compile installer\mytradebot.iss to create the installer."
