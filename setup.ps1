# Windows PowerShell: create .venv, install dependencies, run the offline tests.
Set-Location $PSScriptRoot
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
.\.venv\Scripts\python.exe -m pytest -q
Write-Host "Done. Put your key in .env, then: .\.venv\Scripts\Activate.ps1; python eval.py"
