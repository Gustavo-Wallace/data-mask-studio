from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import tomllib


def application_version() -> str:
    """Lê o pyproject no checkout ou os metadados do pacote instalado."""
    project_file = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if project_file.is_file():
        try:
            document = tomllib.loads(project_file.read_text(encoding="utf-8"))
            return str(document["project"]["version"])
        except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError):
            pass
    try:
        return version("data-mask-studio")
    except PackageNotFoundError:
        return "0+unknown"
