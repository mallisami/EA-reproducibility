# Create a fresh Python 3.12 virtual environment for the reproduction levels L0-L2 (Windows PowerShell).
# Usage (from the package root):  powershell -ExecutionPolicy Bypass -File environment\setup_python_env.ps1 [-Venv .venv]
param([string]$Venv = "$PSScriptRoot\..\.venv", [string]$Python = "py")
$ErrorActionPreference = 'Stop'
if ($Python -eq 'py') { $cmd = @('py', '-3.12') } else { $cmd = @($Python) }
& $cmd[0] $cmd[1..($cmd.Length - 1)] --version
& $cmd[0] $cmd[1..($cmd.Length - 1)] -m venv $Venv
$vpy = Join-Path $Venv 'Scripts\python.exe'
& $vpy -m pip install --upgrade pip | Out-Null
& $vpy -m pip install -r "$PSScriptRoot\requirements\requirements-reproduce.txt"
& $vpy -m pip freeze | Out-File -Encoding utf8 (Join-Path $Venv 'pip_freeze_installed.txt')
Write-Output "venv ready: $Venv   (activate with: $Venv\Scripts\Activate.ps1)"
