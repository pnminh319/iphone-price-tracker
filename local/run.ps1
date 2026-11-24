
param(
    [switch]$Write,
    [int]$Limit = 0
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Credentials = Join-Path $Root "local\secrets\google_credentials.json"
$Spreadsheet = Join-Path $Root "local\secrets\spreadsheet_id.txt"
$LogDir = Join-Path $Root "local\logs"

if (!(Test-Path $Python)) {
    throw "Run local/setup.ps1 first."
}

if ($Limit -lt 0) {
    throw "Limit must be non-negative."
}

if ($Write -and $Limit -gt 0) {
    throw "Cannot use Write with Limit."
}

if ($Write) {
    if (!(Test-Path $Credentials)) {
        throw "Missing Google credentials."
    }

    if (!(Test-Path $Spreadsheet)) {
        throw "Missing spreadsheet ID."
    }
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$LogFile = Join-Path $LogDir "crawl_$Timestamp.log"

$Mutex = [System.Threading.Mutex]::new($false, "iPhonePriceTrackerLocalV53")
$Locked = $false

try {
    try {
        $Locked = $Mutex.WaitOne(0)
    }
    catch [System.Threading.AbandonedMutexException] {
        $Locked = $true
    }

    if (!$Locked) {
        throw "Local crawler is already running."
    }

    $env:PYTHONUTF8 = "1"
    $env:PYTHONUNBUFFERED = "1"

    $PythonArgs = @(
        "-X",
        "utf8",
        "crawler/local_crawler.py"
    )

    if ($Write) {
        $env:GOOGLE_CREDENTIALS = [System.IO.File]::ReadAllText($Credentials)
        $env:SPREADSHEET_ID = [System.IO.File]::ReadAllText($Spreadsheet).Trim()

        $PythonArgs += "--write"
    }

    if ($Limit -gt 0) {
        $PythonArgs += @("--limit", "$Limit")
    }

    Write-Host "========================================"
    Write-Host "LOCAL CRAWLER V5.3.1"
    Write-Host "========================================"
    Write-Host "LOG: $LogFile"

    if ($Write) {
        Write-Host "MODE: GOOGLE SHEETS"
    }
    else {
        Write-Host "MODE: PREVIEW"
    }

    & $Python @PythonArgs | Tee-Object -FilePath $LogFile

    $ExitCode = $LASTEXITCODE

    if ($ExitCode -ne 0) {
        throw "Crawler failed with exit code $ExitCode"
    }

    Write-Host ""
    Write-Host "CRAWLER SUCCESS" -ForegroundColor Green
}
finally {
    Remove-Item Env:GOOGLE_CREDENTIALS -ErrorAction SilentlyContinue
    Remove-Item Env:SPREADSHEET_ID -ErrorAction SilentlyContinue

    if ($Locked) {
        $Mutex.ReleaseMutex()
    }

    $Mutex.Dispose()
}
