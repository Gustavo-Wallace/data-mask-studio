import re
import shutil
import subprocess
import tomllib
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HELPERS = PROJECT_ROOT / "scripts" / "windows_build_helpers.ps1"
BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build_windows.ps1"
SPEC_FILE = PROJECT_ROOT / "packaging" / "windows" / "DataMaskStudio.spec"
INNO_FILE = PROJECT_ROOT / "packaging" / "windows" / "DataMaskStudio.iss"


def run_powershell(command: str) -> subprocess.CompletedProcess[str]:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell não está disponível neste ambiente Windows.")
    return subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def ps_quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def test_pyinstaller_is_a_development_dependency_only() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text("utf-8"))
    runtime_dependencies = project["project"]["dependencies"]
    development_dependencies = project["project"]["optional-dependencies"]["dev"]

    assert not any(item.lower().startswith("pyinstaller") for item in runtime_dependencies)
    assert any(item.lower().startswith("pyinstaller") for item in development_dependencies)


def test_build_helper_reads_version_and_generates_versioned_names() -> None:
    command = (
        f". {ps_quote(HELPERS)}; "
        f"$v=Get-DataMaskStudioProjectVersion -ProjectRoot {ps_quote(PROJECT_ROOT)}; "
        "$n=Get-DataMaskStudioArtifactNames -Version $v; "
        '"$v|$($n.PortableZip)|$($n.Installer)"'
    )

    result = run_powershell(command)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == (
        "0.9.0|DataMaskStudio-Portable-0.9.0.zip|"
        "DataMaskStudio-Setup-0.9.0.exe"
    )


def test_spec_uses_onedir_windowed_application_and_generated_metadata() -> None:
    spec = SPEC_FILE.read_text(encoding="utf-8")

    assert 'name="DataMaskStudio"' in spec
    assert "console=False" in spec
    assert "COLLECT(" in spec
    assert "exclude_binaries=True" in spec
    assert 'os.environ.get("DMS_VERSION_FILE")' in spec
    assert 'os.environ.get("DMS_ICON_FILE")' in spec
    assert '"pytest"' in spec and '"tests"' in spec
    assert 'Path(item[0]).name != "direct_url.json"' in spec


def test_inno_setup_is_per_user_upgradeable_and_preserves_local_data() -> None:
    script = INNO_FILE.read_text(encoding="utf-8")

    assert "AppId={{D5C24B6C-16B7-4CF5-9D92-5405A6974D43}" in script
    assert "DefaultDirName={localappdata}\\Programs\\Data Mask Studio" in script
    assert "PrivilegesRequired=lowest" in script
    assert "SetupMutex=DataMaskStudio-Setup-Mutex-D5C24B6C" in script
    assert "UsePreviousAppDir=yes" in script
    assert "OutputBaseFilename=DataMaskStudio-Setup-{#MyAppVersion}" in script
    assert "VersionInfoVersion={#MyAppVersion}" in script
    assert "VersionInfoProductVersion={#MyAppVersion}" in script
    assert "recursesubdirs createallsubdirs" in script
    assert "Flags: unchecked" in script
    assert "postinstall" in script
    assert re.search(r"(?m)^\[UninstallDelete\]\s*$", script) is None
    assert "%LOCALAPPDATA%\\DataMaskStudio" in script


def test_build_audit_rejects_sensitive_file_without_exposing_content(
    tmp_path: Path,
) -> None:
    build_root = tmp_path / "DataMaskStudio"
    build_root.mkdir()
    (build_root / "DataMaskStudio.exe").write_bytes(b"exe")
    secret = "do-not-display-this-secret"
    (build_root / "secret.key").write_text(secret, encoding="utf-8")
    destination = tmp_path / "portable.zip"
    command = (
        f". {ps_quote(HELPERS)}; "
        f"New-DataMaskStudioPortableArchive -PortableDirectory {ps_quote(build_root)} "
        f"-DestinationPath {ps_quote(destination)}"
    )

    result = run_powershell(command)

    visible_output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "secret.key" in visible_output
    assert secret not in visible_output
    assert not destination.exists()


def test_build_audit_rejects_editable_install_provenance(tmp_path: Path) -> None:
    build_root = tmp_path / "DataMaskStudio"
    metadata = build_root / "_internal" / "package.dist-info"
    metadata.mkdir(parents=True)
    (build_root / "DataMaskStudio.exe").write_bytes(b"exe")
    (metadata / "direct_url.json").write_text(
        '{"url": "file:///private/developer/path"}', encoding="utf-8"
    )
    command = (
        f". {ps_quote(HELPERS)}; "
        f"Assert-DataMaskStudioBuildIsSafe -Root {ps_quote(build_root)}"
    )

    result = run_powershell(command)

    assert result.returncode != 0
    assert "direct_url.json" in result.stderr
    assert "/private/developer/path" not in result.stderr


def test_portable_zip_has_root_folder_without_tests_or_virtualenv(
    tmp_path: Path,
) -> None:
    portable = tmp_path / "DataMaskStudio"
    internal = portable / "_internal"
    internal.mkdir(parents=True)
    (portable / "DataMaskStudio.exe").write_bytes(b"exe")
    (internal / "library.dll").write_bytes(b"dll")
    destination = tmp_path / "DataMaskStudio-Portable-0.9.0.zip"
    command = (
        f". {ps_quote(HELPERS)}; "
        f"New-DataMaskStudioPortableArchive -PortableDirectory {ps_quote(portable)} "
        f"-DestinationPath {ps_quote(destination)}"
    )

    result = run_powershell(command)

    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(destination) as archive:
        names = [name.replace("\\", "/") for name in archive.namelist()]
    assert "DataMaskStudio/DataMaskStudio.exe" in names
    assert "DataMaskStudio/_internal/library.dll" in names
    assert not any("/.venv/" in f"/{name}" for name in names)
    assert not any("/tests/" in f"/{name}" for name in names)
    assert not any(name.endswith((".csv", ".html", ".dmsbackup")) for name in names)


def test_clean_script_removes_build_and_dist_but_preserves_release_by_default(
    tmp_path: Path,
) -> None:
    copied_scripts = tmp_path / "scripts"
    copied_scripts.mkdir()
    shutil.copy2(PROJECT_ROOT / "scripts" / "clean_build.ps1", copied_scripts)
    for name in ("build", "dist", "release"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "marker.txt").write_text(name, encoding="utf-8")

    result = run_powershell(f"& {ps_quote(copied_scripts / 'clean_build.ps1')}")

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "build").exists()
    assert not (tmp_path / "dist").exists()
    assert (tmp_path / "release" / "marker.txt").exists()


def test_missing_tool_messages_are_explicit_and_inno_is_optional() -> None:
    script = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "PyInstaller não está instalado" in script
    assert 'pip install -e ".[dev]"' in script
    assert "Inno Setup não foi encontrado" in script
    assert "portátil e o ZIP foram gerados normalmente" in script
    assert "-PortableOnly" in script


def test_gitignore_excludes_generated_packaging_artifacts() -> None:
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "/build/" in gitignore
    assert "/dist/" in gitignore
    assert "/release/" in gitignore
    assert "/packaging/windows/Output/" in gitignore
    assert "*.ps1" not in gitignore
    assert "*.spec" not in gitignore
    assert "*.iss" not in gitignore
