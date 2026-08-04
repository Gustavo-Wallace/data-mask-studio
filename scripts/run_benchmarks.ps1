[CmdletBinding()]
param(
    [ValidateSet(10000, 100000, 250000, 500000, 1000000)][int]$Rows = 10000,
    [int]$UniqueValues = 1000,
    [ValidateRange(1, 8)][int]$Columns = 2,
    [ValidateRange(1, 8)][int]$AnonymizedColumns = 1,
    [switch]$Prepopulate,
    [switch]$Clean
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Crie e instale o ambiente .venv antes de executar os benchmarks.'
}
$data = Join-Path $root 'benchmarks\.data'
if ($Clean) {
    if (Test-Path -LiteralPath $data) { Remove-Item -LiteralPath $data -Recurse -Force }
    return
}
New-Item -ItemType Directory -Force -Path $data | Out-Null
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$identifier = [Guid]::NewGuid().ToString('N').Substring(0, 8)
$scenario = "rows-$Rows-unique-$UniqueValues-cols-$Columns-anon-$AnonymizedColumns"
$run = Join-Path $data "$scenario-$timestamp-$identifier"
New-Item -ItemType Directory -Path $run | Out-Null
$input = Join-Path $run 'fixture.csv'
Write-Host "Execução isolada: $run"
Write-Host 'Fase: geração da fixture'
& $python (Join-Path $root 'benchmarks\generate_fixtures.py') --output $input --rows $Rows --unique-values $UniqueValues --columns $Columns
$work = Join-Path $run 'work'
Write-Host 'Fase: anonimização e restauração'
$arguments = @(
    (Join-Path $root 'benchmarks\benchmark_csv.py'),
    '--input', $input,
    '--work-dir', $work,
    '--rows', $Rows,
    '--unique-values', $UniqueValues,
    '--anonymized-columns', $AnonymizedColumns
)
if ($Prepopulate) { $arguments += '--prepopulate' }
& $python @arguments
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Benchmark interrompido ou com falha. Fase: anonimização/restauração. Diretório: $run"
    Get-ChildItem -LiteralPath $run -Recurse -Filter '*.tmp' -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
    exit $LASTEXITCODE
}
Write-Host "Benchmark concluído: $run"
