"""Ownership de temporários; reconhecer evidência não autoriza sua remoção."""

import re
from collections.abc import Iterator
from pathlib import Path

from data_mask_studio.environment import RESTORE_JOURNAL


# Contratos existentes dos produtores baseados em tempfile. Nomes ambíguos e
# formatos futuros desconhecidos permanecem fora da autoridade de limpeza.
_RANDOM = r"[a-z0-9_]{8}"
_DIRECTORY_NAME = re.compile(r"\.dms-(?:backup|restore)-" + _RANDOM)
_FILE_NAME = re.compile(
    r"(?:\.dms-(?:write|access)-" + _RANDOM
    + r"|\..+\.dmsbackup\." + _RANDOM + r"\.tmp)"
)
_LOCAL_FILE_NAME = re.compile(
    r"(?:\.secret-" + _RANDOM + r"\.tmp|\.profiles\.json\." + _RANDOM + r"\.tmp)"
)
_RESTORE_DIRECTORY = re.compile(r"\.dms-restore-operation-[0-9a-f]{32}")
_PUBLICATION_FILE = re.compile(
    r"(?:\.dms-operation-[0-9a-f]{32}\.json|\.dms-journal-" + _RANDOM + r")"
)
_PUBLICATION_TEMP = re.compile(
    r"(?:\.dms-operation-[0-9a-f]{32}-" + _RANDOM
    + r"|\.dms-package-" + _RANDOM + r")\.tmp"
)


def is_owned_temporary(path: Path, application_directory: Path) -> bool:
    """Mesmo contrato da limpeza; o chamador limita as raízes examinadas."""
    if path.is_symlink() or path.is_junction():
        return False
    if path.is_dir():
        return _DIRECTORY_NAME.fullmatch(path.name) is not None
    return path.is_file() and (
        _FILE_NAME.fullmatch(path.name) is not None
        or (path.parent == application_directory and _LOCAL_FILE_NAME.fullmatch(path.name) is not None)
    )


def recovery_artifacts(application_directory: Path) -> Iterator[Path]:
    """Evidências conhecidas para alerta apenas, nunca para limpeza genérica."""
    for path in application_directory.iterdir():
        if path.is_symlink() or path.is_junction():
            continue
        if path.is_dir() and _RESTORE_DIRECTORY.fullmatch(path.name):
            yield path
        elif path.is_file() and (
            path.name == RESTORE_JOURNAL or _PUBLICATION_TEMP.fullmatch(path.name)
        ):
            yield path
    publications = application_directory / "publication-operations"
    if publications.is_symlink() or publications.is_junction() or not publications.is_dir():
        return
    for path in publications.iterdir():
        if (not path.is_symlink() and not path.is_junction()
                and path.is_file() and _PUBLICATION_FILE.fullmatch(path.name)):
            yield path
