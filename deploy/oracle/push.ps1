# Upload PaperLab to the Oracle VM and (re)start it. Run from your PC, in the project folder:
#   powershell -ExecutionPolicy Bypass -File deploy\oracle\push.ps1 -Ip 140.238.12.34 -Key $env:USERPROFILE\.ssh\oracle_paperlab
# First run: also uploads %USERPROFILE%\paperlab-oracle\paperlab.env (the settings from export-env.ps1).
# Later runs: code only (add -Settings to replace the settings file too). Data on the server (/opt/paperlab/data) is kept.
param(
    [Parameter(Mandatory = $true)][string]$Ip,
    [Parameter(Mandatory = $true)][string]$Key,
    [string]$User = 'ubuntu',
    [switch]$Settings
)
$ErrorActionPreference = 'Stop'
$root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$here = $PSScriptRoot
$target = "$User@$Ip"
$ssh = @('-i', $Key, '-o', 'StrictHostKeyChecking=accept-new')
$tar = Join-Path $env:TEMP 'paperlab-app.tgz'

Write-Host '== packing the project (no data, no databases, no secrets)'
Push-Location $root
try {
    tar -czf $tar --exclude=.git --exclude=.venv --exclude=data --exclude=snapshots --exclude=research `
        --exclude=node_modules --exclude=__pycache__ --exclude=.pytest_cache --exclude=deploy `
        --exclude='*.db' --exclude='*.db-shm' --exclude='*.db-wal' --exclude='*.log' --exclude=.env .
    if ($LASTEXITCODE -ne 0) { throw 'tar failed' }
} finally { Pop-Location }

Write-Host '== uploading'
scp @ssh $tar "${target}:/opt/paperlab/app.tgz"
scp @ssh (Join-Path $here 'docker-compose.yml') (Join-Path $here 'Caddyfile') "${target}:/opt/paperlab/"
$envFile = Join-Path $env:USERPROFILE 'paperlab-oracle\paperlab.env'
$first = (ssh @ssh $target 'test -f /opt/paperlab/paperlab.env && echo yes || echo no').Trim() -eq 'no'
if ($Settings -or $first) {
    if (-not (Test-Path $envFile)) { throw "missing ${envFile}: run export-env.ps1 first" }
    scp @ssh $envFile "${target}:/opt/paperlab/paperlab.env"
    ssh @ssh $target 'chmod 600 /opt/paperlab/paperlab.env'
    Write-Host '   settings file uploaded (chmod 600)'
}

Write-Host '== unpacking and (re)starting'
ssh @ssh $target 'set -e; cd /opt/paperlab; rm -rf app.new; mkdir app.new; tar -xzf app.tgz -C app.new; rm -rf app.old; [ -d app ] && mv app app.old; mv app.new app; rm -f app.tgz; docker compose up -d --build; docker image prune -f >/dev/null; docker compose ps'
Remove-Item $tar
$site = (ssh @ssh $target 'cut -d= -f2 /opt/paperlab/.env').Trim()
Write-Host "Done. Open https://$site (first start takes a minute or two; the certificate is fetched on the first visit)."
