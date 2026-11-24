<CodeBlock language="powershell" editable>
param(
    [switch]$Write,
    [int]$Limit = 0
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot

Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"

$Credentials = Join-Path `
    $Root "local\secrets\google_credentials.json"

$Spreadsheet = Join-Path `
    $Root "local\secrets\spreadsheet_id.txt"

$LogDir = Join-Path $Root "local\logs"

New-Item `
    -ItemType Directory `
    -Force `
    -Path $LogDir | Out-Null

$Timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"

$LogFile = Join-Path `
    $LogDir "crawl_$Timestamp.log"

if (!(Test-Path $Python)) {
    throw "Run local/setup.ps1 first."
}

if ($Limit -lt 0) {
    throw "Limit must be non-negative."
}

if ($Write -and $Limit -gt 0) {
    throw "Cannot use Write with Limit."
}

$Mutex = New-Object `
    System.Threading.Mutex(
        $false,
        "iPhonePriceTrackerLocalV53"
    )

$Locked = $false

try
