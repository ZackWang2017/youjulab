param([string]$Python = '', [switch]$SkipInstall)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($env:OS -ne 'Windows_NT') { throw 'Windows 安装程序及 Python sidecar 必须在 Windows x64 上构建。' }
$Desktop = Split-Path $PSScriptRoot -Parent
$Root = (Resolve-Path (Join-Path $Desktop '..\..')).Path
$Sidecar = Join-Path $Desktop 'sidecar'
$Backend = Join-Path $Root 'apps\office-backend'
$Venv = Join-Path $Sidecar '.venv-win'
function Invoke-Checked([string]$Command, [string[]]$Arguments) {
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "命令执行失败: $Command ($LASTEXITCODE)" }
}
if (-not (Test-Path (Join-Path $Venv 'Scripts\python.exe'))) {
    if ($Python) { Invoke-Checked $Python @('-m', 'venv', $Venv) }
    else { Invoke-Checked 'py' @('-3.12', '-m', 'venv', $Venv) }
}
$Runtime = Join-Path $Venv 'Scripts\python.exe'
Invoke-Checked $Runtime @('-c', "import platform; assert platform.machine().lower() in ('amd64','x86_64') and platform.architecture()[0]=='64bit', 'Windows x64 Python required'")
if (-not $SkipInstall) {
    Invoke-Checked $Runtime @('-m', 'pip', 'install', '-r', (Join-Path $Backend 'requirements.txt'), 'pyinstaller>=6.3,<7')
    Push-Location (Join-Path $Root 'apps\office-copilot-web')
    try { Invoke-Checked 'npm.cmd' @('ci') } finally { Pop-Location }
    Push-Location $Desktop
    try { Invoke-Checked 'npm.cmd' @('ci') } finally { Pop-Location }
}
Push-Location (Join-Path $Root 'apps\office-copilot-web')
try { Invoke-Checked 'npm.cmd' @('run','build') } finally { Pop-Location }
Invoke-Checked $Runtime @('-m','PyInstaller','--noconfirm','--clean','--distpath',
    (Join-Path $Sidecar 'dist'),'--workpath',(Join-Path $Sidecar 'build-win'),
    (Join-Path $Sidecar 'youju-office-backend.spec'))
$Binary = Join-Path $Sidecar 'dist\youju-office-backend\youju-office-backend.exe'
if (-not (Test-Path $Binary)) { throw '未生成 Windows Office sidecar。' }
# Exercise the frozen runtime without launching any backend or touching userData.
$Smoke = Join-Path ([IO.Path]::GetTempPath()) ('youju-sidecar-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Smoke | Out-Null
try {
    $Source = Join-Path $Smoke 'source.sqlite'
    $Target = Join-Path $Smoke 'backup.sqlite'
    Invoke-Checked $Runtime @('-c', "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('CREATE TABLE smoke(value TEXT)'); c.execute('INSERT INTO smoke VALUES (?)', ('ok',)); c.commit(); c.close()", $Source)
    Invoke-Checked $Binary @('--backup-sqlite', $Source, $Target)
    Invoke-Checked $Runtime @('-c', "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); assert c.execute('SELECT value FROM smoke').fetchone()[0]=='ok'; c.close()", $Target)
} finally { Remove-Item -Recurse -Force $Smoke }
Push-Location $Desktop
try { Invoke-Checked 'npm.cmd' @('run','dist:win') } finally { Pop-Location }
Write-Host 'Windows 安装程序已生成，目录: apps\office-desktop\release'
