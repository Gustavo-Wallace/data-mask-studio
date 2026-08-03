[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$PortableOnly,
    [switch]$Clean,
    [switch]$VerboseOutput
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($env:OS -ne 'Windows_NT') {
    throw "Este build só pode ser executado no Windows."
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'windows_build_helpers.ps1')

function Write-BuildStep([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Get-BuildPython {
    if ($env:VIRTUAL_ENV) {
        $virtualPython = Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe'
        if (Test-Path -LiteralPath $virtualPython -PathType Leaf) {
            return (Resolve-Path -LiteralPath $virtualPython).Path
        }
    }
    $projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $projectPython -PathType Leaf) {
        return (Resolve-Path -LiteralPath $projectPython).Path
    }
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        $command = Get-Command python -ErrorAction SilentlyContinue
    }
    if ($null -eq $command) {
        throw "Python não foi encontrado no ambiente atual."
    }
    return $command.Source
}

function New-VersionInformationFile {
    param(
        [Parameter(Mandatory = $true)][string]$Version,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    if ($Version -notmatch '^(?<major>\d+)\.(?<minor>\d+)\.(?<patch>\d+)$') {
        throw "A versão deve usar o formato numérico MAJOR.MINOR.PATCH."
    }
    $major = [int]$Matches.major
    $minor = [int]$Matches.minor
    $patch = [int]$Matches.patch
    $content = @"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($major, $minor, $patch, 0),
    prodvers=($major, $minor, $patch, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'Data Mask Studio'),
        StringStruct('FileDescription', 'Data Mask Studio'),
        StringStruct('FileVersion', '$Version'),
        StringStruct('InternalName', 'DataMaskStudio'),
        StringStruct('OriginalFilename', 'DataMaskStudio.exe'),
        StringStruct('ProductName', 'Data Mask Studio'),
        StringStruct('ProductVersion', '$Version')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@
    $parent = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    Set-Content -LiteralPath $Destination -Value $content -Encoding UTF8
}

function Test-PortableStartup {
    param([Parameter(Mandatory = $true)][string]$Executable)

    $testRoot = Join-Path ([IO.Path]::GetTempPath()) ("DataMaskStudio-build-test-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $testRoot | Out-Null
    $oldLocalAppData = $env:LOCALAPPDATA
    $oldQtPlatform = $env:QT_QPA_PLATFORM
    try {
        $env:LOCALAPPDATA = $testRoot
        $env:QT_QPA_PLATFORM = 'offscreen'
        $process = Start-Process -FilePath $Executable -WorkingDirectory (Split-Path -Parent $Executable) -WindowStyle Hidden -PassThru
        Start-Sleep -Seconds 4
        $running = Get-Process -Id $process.Id -ErrorAction SilentlyContinue
        if ($null -eq $running) {
            throw "DataMaskStudio.exe terminou imediatamente durante a validação."
        }
        Stop-Process -Id $process.Id
        $process.WaitForExit(5000) | Out-Null
    }
    finally {
        $env:LOCALAPPDATA = $oldLocalAppData
        if ($null -eq $oldQtPlatform) {
            Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
        }
        else {
            $env:QT_QPA_PLATFORM = $oldQtPlatform
        }
        $resolvedTestRoot = [IO.Path]::GetFullPath($testRoot)
        $resolvedTempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
        if ($resolvedTestRoot.StartsWith($resolvedTempRoot, [StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $resolvedTestRoot)) {
            Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
        }
    }
}

$python = Get-BuildPython
$version = Get-DataMaskStudioProjectVersion -ProjectRoot $projectRoot
if ($version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Versão inválida no pyproject.toml: $version"
}
$artifactNames = Get-DataMaskStudioArtifactNames -Version $version
Write-Host "Versão confirmada pelo pyproject.toml: $version"
Write-Host "Python: $python"

& $python -m PyInstaller --version *> $null
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller não está instalado. Execute: python -m pip install -e ".[dev]"'
}

if (-not $SkipTests) {
    Write-BuildStep 'Executando a suíte de testes'
    $oldQtPlatform = $env:QT_QPA_PLATFORM
    $oldBytecode = $env:PYTHONDONTWRITEBYTECODE
    try {
        $env:QT_QPA_PLATFORM = 'offscreen'
        $env:PYTHONDONTWRITEBYTECODE = '1'
        & $python -m pytest -q -p no:cacheprovider
        if ($LASTEXITCODE -ne 0) {
            throw "A suíte de testes falhou."
        }
    }
    finally {
        $env:QT_QPA_PLATFORM = $oldQtPlatform
        $env:PYTHONDONTWRITEBYTECODE = $oldBytecode
    }
}

Write-BuildStep 'Limpando artefatos anteriores'
& (Join-Path $PSScriptRoot 'clean_build.ps1') -IncludeRelease:$Clean

$buildRoot = Join-Path $projectRoot 'build'
$distRoot = Join-Path $projectRoot 'dist'
$releaseRoot = Join-Path $projectRoot 'release'
$portableDirectory = Join-Path $distRoot 'DataMaskStudio'
$executable = Join-Path $portableDirectory 'DataMaskStudio.exe'
$zipPath = Join-Path $releaseRoot $artifactNames.PortableZip
$installerPath = Join-Path $releaseRoot $artifactNames.Installer
$versionFile = Join-Path $buildRoot 'windows\DataMaskStudio-version.txt'
$iconFile = Join-Path $projectRoot 'packaging\windows\assets\data-mask-studio.ico'
$specFile = Join-Path $projectRoot 'packaging\windows\DataMaskStudio.spec'
$innoScript = Join-Path $projectRoot 'packaging\windows\DataMaskStudio.iss'
New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
foreach ($oldArtifact in @($zipPath, $installerPath)) {
    if (Test-Path -LiteralPath $oldArtifact) {
        Remove-Item -LiteralPath $oldArtifact -Force
    }
}

New-VersionInformationFile -Version $version -Destination $versionFile
if (Test-Path -LiteralPath $iconFile -PathType Leaf) {
    Write-Host "Ícone encontrado: $iconFile"
}
else {
    Write-Warning "Ícone não encontrado em packaging/windows/assets/data-mask-studio.ico. O ícone padrão será usado."
}

Write-BuildStep 'Gerando a aplicação portátil com PyInstaller'
$oldVersionFile = $env:DMS_VERSION_FILE
$oldIconFile = $env:DMS_ICON_FILE
try {
    $env:DMS_VERSION_FILE = $versionFile
    if (Test-Path -LiteralPath $iconFile -PathType Leaf) {
        $env:DMS_ICON_FILE = $iconFile
    }
    else {
        Remove-Item Env:DMS_ICON_FILE -ErrorAction SilentlyContinue
    }
    $logLevel = if ($VerboseOutput) { 'INFO' } else { 'WARN' }
    & $python -m PyInstaller --noconfirm --clean --log-level $logLevel --distpath $distRoot --workpath (Join-Path $buildRoot 'pyinstaller') $specFile
    if ($LASTEXITCODE -ne 0) {
        throw "O PyInstaller não concluiu o build."
    }
}
finally {
    $env:DMS_VERSION_FILE = $oldVersionFile
    $env:DMS_ICON_FILE = $oldIconFile
}

if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "O executável esperado não foi criado: $executable"
}
Assert-DataMaskStudioBuildIsSafe -Root $portableDirectory

Write-BuildStep 'Validando a inicialização do executável'
Test-PortableStartup -Executable $executable

Write-BuildStep 'Criando o ZIP portátil'
New-DataMaskStudioPortableArchive -PortableDirectory $portableDirectory -DestinationPath $zipPath | Out-Null

$zipAuditRoot = Join-Path ([IO.Path]::GetTempPath()) ("DataMaskStudio-zip-audit-" + [guid]::NewGuid())
try {
    Expand-Archive -LiteralPath $zipPath -DestinationPath $zipAuditRoot
    Assert-DataMaskStudioBuildIsSafe -Root $zipAuditRoot
}
finally {
    $resolvedAuditRoot = [IO.Path]::GetFullPath($zipAuditRoot)
    $resolvedTempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if ($resolvedAuditRoot.StartsWith($resolvedTempRoot, [StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $resolvedAuditRoot)) {
        Remove-Item -LiteralPath $resolvedAuditRoot -Recurse -Force
    }
}

$installerCreated = $false
$innoCompiler = $null
if (-not $PortableOnly) {
    $innoCompiler = Find-InnoSetupCompiler
    if ($null -eq $innoCompiler) {
        Write-Warning "Inno Setup não foi encontrado. O portátil e o ZIP foram gerados normalmente."
        Write-Host "Instale o Inno Setup 6 manualmente e execute este script novamente para gerar o instalador."
    }
    else {
        Write-BuildStep 'Gerando o instalador com Inno Setup'
        $innoArguments = @(
            "/DMyAppVersion=$version",
            "/DBuildRoot=$portableDirectory",
            "/DReleaseRoot=$releaseRoot"
        )
        if (Test-Path -LiteralPath $iconFile -PathType Leaf) {
            $innoArguments += "/DDmsIconFile=$iconFile"
        }
        $innoArguments += $innoScript
        & $innoCompiler @innoArguments
        if ($LASTEXITCODE -ne 0) {
            throw "O Inno Setup não concluiu a geração do instalador."
        }
        if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
            throw "O instalador esperado não foi criado: $installerPath"
        }
        $installerCreated = $true
    }
}

Write-BuildStep 'Resumo do build'
$zipSizeMb = [math]::Round((Get-Item -LiteralPath $zipPath).Length / 1MB, 1)
Write-Host "Aplicação portátil: $portableDirectory"
Write-Host "ZIP: $zipPath ($zipSizeMb MB)"
if ($installerCreated) {
    $installerSizeMb = [math]::Round((Get-Item -LiteralPath $installerPath).Length / 1MB, 1)
    Write-Host "Instalador: $installerPath ($installerSizeMb MB)"
}
elseif ($PortableOnly) {
    Write-Host "Instalador: ignorado por -PortableOnly"
}
else {
    Write-Host "Instalador: não gerado (Inno Setup ausente)"
}
Write-Host "Nenhum dado de %LOCALAPPDATA%\DataMaskStudio foi incluído."
