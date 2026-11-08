param([switch]$Resume, [int]$MaxSteps = 20)
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    $env:TEMP = Join-Path $PSScriptRoot 'tmp'
    $env:TMP = $env:TEMP
    $python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    $arguments = @('-u', 'train.py', '--max-steps', "$MaxSteps")
    if ($Resume) { $arguments += '--resume' }
    $logName = 'training-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log'
    & $python @arguments 2>&1 | Tee-Object -FilePath (Join-Path $PSScriptRoot "logs\$logName")
    if ($LASTEXITCODE -ne 0) { throw "Training exited with code $LASTEXITCODE" }
} finally { Pop-Location }
