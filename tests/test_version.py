import tomllib
from pathlib import Path

from data_mask_studio.metadata import application_version


def test_project_version_is_1_0_2() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["version"] == "1.0.2"
    assert application_version() == "1.0.2"


def test_project_public_metadata_is_complete_and_unlicensed() -> None:
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
    assert "license" not in project
