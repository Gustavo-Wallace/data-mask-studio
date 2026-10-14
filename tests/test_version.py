import tomllib
from pathlib import Path

from data_mask_studio import metadata
from data_mask_studio.metadata import application_version


def test_project_version_is_1_3_0() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["version"] == "1.3.0"
    assert application_version() == "1.3.0"


def test_checkout_version_takes_precedence_over_installed_metadata(monkeypatch) -> None:
    monkeypatch.setattr(metadata, "version", lambda _name: "1.2.0")

    assert application_version() == "1.3.0"


def test_installed_application_reads_package_metadata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        metadata, "__file__", str(tmp_path / "bundle" / "data_mask_studio" / "metadata.py")
    )
    requested_distributions: list[str] = []

    def installed_version(name: str) -> str:
        requested_distributions.append(name)
        return "1.3.0"

    monkeypatch.setattr(metadata, "version", installed_version)

    assert application_version() == "1.3.0"
    assert requested_distributions == ["data-mask-studio"]


def test_project_public_metadata_is_complete_and_gpl_licensed() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["description"] == (
        "Aplicação desktop local-first para preparação de dados, anonimização e "
        "mascaramento determinístico com restauração controlada."
    )
    assert project["urls"] == {
        "Repository": "https://github.com/Gustavo-Wallace/data-mask-studio",
        "Releases": "https://github.com/Gustavo-Wallace/data-mask-studio/releases",
        "Issues": "https://github.com/Gustavo-Wallace/data-mask-studio/issues",
        "Changelog": "https://github.com/Gustavo-Wallace/data-mask-studio/blob/main/CHANGELOG.md",
        "Security": "https://github.com/Gustavo-Wallace/data-mask-studio/blob/main/SECURITY.md",
    }
    assert project["license"] == "GPL-3.0-only"
    assert project["license-files"] == ["LICENSE"]


def test_build_backend_supports_spdx_license_metadata() -> None:
    configuration = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert configuration["build-system"]["requires"] == ["setuptools>=77"]
