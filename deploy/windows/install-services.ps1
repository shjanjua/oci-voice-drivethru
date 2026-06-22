<#
.SYNOPSIS
  Install the three voice-drive-thru processes as auto-starting Windows services using NSSM.
  Windows counterpart of deploy/voicedt-*.service (systemd) + setup_vm.sh.

.DESCRIPTION
  Creates three NSSM-managed services (mirroring the Linux systemd units):
    voicedt-livekit  -> livekit-server.exe --config <LiveKitConfig>
    voicedt-agent    -> <repo>\.venv\Scripts\python.exe -m agent.main start
    voicedt-web      -> <repo>\.venv\Scripts\uvicorn.exe web.server:app --host <WebHost> --port 7871

  Services point DIRECTLY at the venv executables (not "uv run") so the Service Control
  Manager monitors the real worker and can restart it on crash. Run `uv sync` first so
  .venv exists. Run THIS script from an elevated (Administrator) PowerShell.

.PARAMETER RepoRoot
  Path to the voice-order repo root (contains pyproject.toml + .env). Defaults to two
  levels up from this script (deploy\windows\ -> repo root).

.PARAMETER LiveKitExe
  Full path to livekit-server.exe.

.PARAMETER LiveKitConfig
  Full path to your hand-edited livekit.yaml (keys MUST match LIVEKIT_API_KEY/SECRET in .env).

.PARAMETER WebHost
  Bind address for the web server. 127.0.0.1 = same-machine kiosk only (default).
  Use 0.0.0.0 to expose the kiosk/QR pages on the LAN (then open the firewall).

.PARAMETER LogDir
  Directory for service stdout/stderr logs. Default <RepoRoot>\logs.

.EXAMPLE
  # From an elevated PowerShell, in the repo root:
  .\deploy\windows\install-services.ps1 -LiveKitExe C:\livekit\livekit-server.exe -LiveKitConfig C:\Code\voice-order\deploy\livekit.yaml
#>
[CmdletBinding()]
param(
  [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
  [Parameter(Mandatory = $true)][string]$LiveKitExe,
  [Parameter(Mandatory = $true)][string]$LiveKitConfig,
  [ValidateSet('127.0.0.1', '0.0.0.0')][string]$WebHost = '127.0.0.1',
  [string]$LogDir = (Join-Path (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path 'logs')
)

$ErrorActionPreference = 'Stop'

# --- preflight ---------------------------------------------------------------
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) { throw "Run this script from an elevated (Administrator) PowerShell." }

$nssm = (Get-Command nssm.exe -ErrorAction SilentlyContinue).Source
if (-not $nssm) { throw "nssm.exe not found on PATH. Install it: 'winget install -e --id NSSM.NSSM' or 'choco install nssm'." }

$venvPython  = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$venvUvicorn = Join-Path $RepoRoot '.venv\Scripts\uvicorn.exe'
foreach ($p in @($venvPython, $venvUvicorn, $LiveKitExe, $LiveKitConfig)) {
  if (-not (Test-Path $p)) { throw "Not found: $p  (did you run 'uv sync', and are the LiveKit paths correct?)" }
}
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

Write-Host "RepoRoot     : $RepoRoot"
Write-Host "LiveKit exe  : $LiveKitExe"
Write-Host "LiveKit cfg  : $LiveKitConfig"
Write-Host "Web bind     : $WebHost`:7871"
Write-Host "Logs         : $LogDir`n"

function Set-Svc($name, $exe, $arguments, $deps) {
  & $nssm stop $name 2>$null | Out-Null
  & $nssm remove $name confirm 2>$null | Out-Null
  # SCM deletes services asynchronously; wait until it's gone so a re-run doesn't hit
  # error 1072 "marked for deletion". (Close services.msc if this still stalls.)
  for ($i = 0; $i -lt 20 -and (Get-Service $name -ErrorAction SilentlyContinue); $i++) {
    Start-Sleep -Milliseconds 250
  }
  & $nssm install $name $exe
  & $nssm set $name AppParameters $arguments
  & $nssm set $name AppDirectory $RepoRoot
  & $nssm set $name AppStdout (Join-Path $LogDir "$name.log")
  & $nssm set $name AppStderr (Join-Path $LogDir "$name.log")
  & $nssm set $name AppRotateFiles 1
  & $nssm set $name AppExit Default Restart
  & $nssm set $name AppThrottle 5000
  & $nssm set $name Start SERVICE_AUTO_START
  if ($deps) { & $nssm set $name DependOnService $deps }
  Write-Host "installed $name"
}

# 1) LiveKit SFU. AppDirectory is the repo root (harmless); config path is absolute.
Set-Svc 'voicedt-livekit' $LiveKitExe "--config `"$LiveKitConfig`"" $null

# 2) Agent worker — venv python, `-m agent.main start`. PYTHONUNBUFFERED for live logs.
Set-Svc 'voicedt-agent' $venvPython '-m agent.main start' 'voicedt-livekit'
& $nssm set 'voicedt-agent' AppEnvironmentExtra 'PYTHONUNBUFFERED=1' | Out-Null

# 3) Web server — venv uvicorn.
Set-Svc 'voicedt-web' $venvUvicorn "web.server:app --host $WebHost --port 7871" 'voicedt-livekit'

Write-Host "`nStarting services..."
& $nssm start 'voicedt-livekit'
Start-Sleep -Seconds 2
& $nssm start 'voicedt-agent'
& $nssm start 'voicedt-web'

Write-Host "`nDone. Check status with:  nssm status voicedt-web"
Write-Host "Health:  Invoke-RestMethod http://127.0.0.1:7871/api/healthz"
Write-Host "Logs in: $LogDir"
