import tomllib
from pathlib import Path

from data_mask_studio.metadata import application_version


def test_project_version_is_0_6_0() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["version"] == "0.7.0"
    assert application_version() == "0.7.0"
