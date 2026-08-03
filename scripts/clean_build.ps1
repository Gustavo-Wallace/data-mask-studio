[CmdletBinding(SupportsShouldProcess = $true)]
param([switch]$IncludeRelease)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$targets = @(
    (Join-Path $projectRoot 'build'),
    (Join-Path $projectRoot 'dist')
)
if ($IncludeRelease) {
    $targets += Join-Path $projectRoot 'release'
}

foreach ($target in $targets) {
    $absoluteTarget = [IO.Path]::GetFullPath($target)
    $expectedPrefix = $projectRoot.TrimEnd('\') + '\'
    if (-not $absoluteTarget.StartsWith($expectedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Caminho de limpeza fora do projeto: $absoluteTarget"
    }
    if (Test-Path -LiteralPath $absoluteTarget) {
        if ($PSCmdlet.ShouldProcess($absoluteTarget, 'Remover artefatos de build')) {
            Remove-Item -LiteralPath $absoluteTarget -Recurse -Force
        }
    }
}
