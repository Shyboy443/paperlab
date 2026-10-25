# Copy PaperLab's settings from Railway into a local file WITHOUT showing any value on screen.
#   powershell -ExecutionPolicy Bypass -File deploy\oracle\export-env.ps1
# Writes %USERPROFILE%\paperlab-oracle\paperlab.env (outside the project, so it can never reach git or the image).
# Railway-only variables (RAILWAY_*, PORT, DATA_DIR) are left out; docker-compose.yml sets PORT and DATA_DIR itself.
$ErrorActionPreference = 'Stop'
$dir = Join-Path $env:USERPROFILE 'paperlab-oracle'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$out = Join-Path $dir 'paperlab.env'

Push-Location (Join-Path $PSScriptRoot '..\..')
try {
    $lines = railway variables --service paperlab --kv
    if ($LASTEXITCODE -ne 0) { throw 'railway variables failed (log in with "railway login" and link the project first)' }
} finally { Pop-Location }

$keep = $lines | Where-Object { $_ -match '^[A-Z0-9_]+=' -and $_ -notmatch '^(RAILWAY_[A-Z0-9_]*|PORT|DATA_DIR)=' }
[System.IO.File]::WriteAllLines($out, [string[]]$keep, (New-Object System.Text.UTF8Encoding $false))

$names = $keep | ForEach-Object { ($_ -split '=', 2)[0] }
Write-Host ("Saved {0} settings to {1}" -f $names.Count, $out)
Write-Host ("Names only: " + ($names -join ', '))
if ($names -notcontains 'DASHBOARD_PASSWORD') { Write-Warning 'DASHBOARD_PASSWORD is missing: add it to the file before pushing.' }
