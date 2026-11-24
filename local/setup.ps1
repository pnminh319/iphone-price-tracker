
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot

Set-Location $Root

Write-Host "========================================"
Write-Host "IPHONE TRACKER LOCAL SETUP V5.3"
Write-Host "========================================"

python --version

if ($LASTEXITCODE -ne 0) {
    throw "Python is not installed."
}

$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (!(Test-Path $Python)) {

    python -m venv .venv

    if ($LASTEXITCODE -ne 0) {
        throw "Cannot create virtual environment."
    }
}

& $Python -m pip install --upgrade pip

if ($LASTEXITCODE -ne 0) {
    throw "Cannot upgrade pip."
}

& $Python -m pip install requests beautifulsoup4 gspread google-auth

if ($LASTEXITCODE -ne 0) {
    throw "Cannot install dependencies."
}

$Directories = @(
    "local\secrets",
    "local\test_data",
    "local\logs"
)

foreach ($Directory in $Directories) {

    New-Item `
        -ItemType Directory `
        -Force `
        -Path $Directory | Out-Null
}

& $Python -m py_compile `
    crawler/local_discover.py `
    crawler/retailers_v52.py `
    crawler/local_crawler.py `
    crawler/export_dashboard.py

if ($LASTEXITCODE -ne 0) {
    throw "Python syntax check failed."
}

Write-Host ""
Write-Host "SETUP SUCCESS" -ForegroundColor Green
Write-Host ""
Write-Host "Required local files:"
Write-Host "local/secrets/google_credentials.json"
Write-Host "local/secrets/spreadsheet_id.txt"
Write-Host ""
Write-Host "Never upload these files to GitHub."
