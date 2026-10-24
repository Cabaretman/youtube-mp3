$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.venv/Scripts/python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
}
& .venv/Scripts/python.exe -m pip install -r requirements.txt --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw 'Could not install dependencies.' }
Write-Host 'Open http://127.0.0.1:8000. Press Ctrl+C to stop.'
& .venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
