import tomllib
from pathlib import Path

from data_mask_studio.metadata import application_version


def test_project_version_is_1_0_3() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["version"] == "1.0.3"
    assert application_version() == "1.0.3"


def test_project_public_metadata_is_complete_and_gpl_licensed() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["description"] == (
        "Aplicação desktop para anonimização local, determinística e reversível "
        "de arquivos CSV."
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
