Set-StrictMode -Version Latest

function Get-DataMaskStudioProjectVersion {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $projectFile = Join-Path $ProjectRoot 'pyproject.toml'
    if (-not (Test-Path -LiteralPath $projectFile -PathType Leaf)) {
        throw "pyproject.toml não foi encontrado."
    }
    $content = Get-Content -LiteralPath $projectFile -Raw -Encoding UTF8
    $projectMatch = [regex]::Match(
        $content,
        '(?ms)^\[project\]\s*(.*?)(?=^\[|\z)'
    )
    if (-not $projectMatch.Success) {
        throw "A seção [project] não foi encontrada no pyproject.toml."
    }
    $versionMatch = [regex]::Match(
        $projectMatch.Groups[1].Value,
        '(?m)^version\s*=\s*"(?<version>[^\"]+)"\s*$'
    )
    if (-not $versionMatch.Success) {
        throw "A versão do projeto não foi encontrada no pyproject.toml."
    }
    return $versionMatch.Groups['version'].Value
}

function Get-DataMaskStudioArtifactNames {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Version)

    return [pscustomobject]@{
        PortableZip = "DataMaskStudio-Portable-$Version.zip"
        Installer   = "DataMaskStudio-Setup-$Version.exe"
    }
}

function Find-InnoSetupCompiler {
    [CmdletBinding()]
    param()

    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe')
    )
    $wingetRoot = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    if (Test-Path -LiteralPath $wingetRoot -PathType Container) {
        $wingetCompilers = Get-ChildItem -LiteralPath $wingetRoot -Directory |
            Where-Object { $_.Name -like 'JRSoftware.InnoSetup*' } |
            ForEach-Object {
                Get-ChildItem -LiteralPath $_.FullName -Filter ISCC.exe -Recurse -File -ErrorAction SilentlyContinue
            }
        $candidates += $wingetCompilers.FullName
    }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return $null
}

function Assert-DataMaskStudioBuildIsSafe {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Root)

    $resolvedRoot = (Resolve-Path -LiteralPath $Root).Path
    $forbiddenNames = @(
        'secret.key', 'vault_key.dpapi', 'vault.db', 'vault.db-journal',
        'vault.db-wal', 'vault.db-shm', 'profiles.json', '.env',
        'direct_url.json'
    )
    $findings = [System.Collections.Generic.List[string]]::new()
    foreach ($file in Get-ChildItem -LiteralPath $resolvedRoot -Recurse -File -Force) {
        $relativePath = $file.FullName.Substring($resolvedRoot.Length).TrimStart('\', '/')
        $segments = $relativePath -split '[\\/]'
        $hasForbiddenDirectory = $segments | Where-Object {
            $_ -in @('.venv', 'venv', '__pycache__', '.pytest_cache', 'tests')
        }
        $isForbidden = $file.Name -in $forbiddenNames -or
            $file.Name -like '.env.*' -or
            $file.Extension -in @('.dmsbackup', '.dpapi', '.csv', '.html', '.htm', '.pyc') -or
            $file.Name -like '*_anonimizado.csv' -or
            $file.Name -like '*_restaurado.csv' -or
            $file.Name -like '*_restaurado.html' -or
            $null -ne $hasForbiddenDirectory
        if ($isForbidden) {
            $findings.Add($relativePath)
        }
    }
    if ($findings.Count -gt 0) {
        $paths = $findings | Sort-Object -Unique
        throw "Publicação interrompida: arquivos não permitidos encontrados:`n$($paths -join "`n")"
    }
}

function New-DataMaskStudioPortableArchive {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$PortableDirectory,
        [Parameter(Mandatory = $true)][string]$DestinationPath
    )

    Assert-DataMaskStudioBuildIsSafe -Root $PortableDirectory
    $destinationParent = Split-Path -Parent $DestinationPath
    if (-not (Test-Path -LiteralPath $destinationParent -PathType Container)) {
        New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
    }
    if (Test-Path -LiteralPath $DestinationPath) {
        Remove-Item -LiteralPath $DestinationPath -Force
    }
    Compress-Archive -LiteralPath $PortableDirectory -DestinationPath $DestinationPath -CompressionLevel Optimal
    if (-not (Test-Path -LiteralPath $DestinationPath -PathType Leaf)) {
        throw "O ZIP portátil não foi criado."
    }
    return (Resolve-Path -LiteralPath $DestinationPath).Path
}
