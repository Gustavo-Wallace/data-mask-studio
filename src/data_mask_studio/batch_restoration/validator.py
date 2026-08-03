from collections.abc import Iterable
from pathlib import Path

from data_mask_studio.batch_restoration.exceptions import BatchRestorationError
from data_mask_studio.batch_restoration.models import (
    BatchRestorationFile,
    BatchRestorationFileType,
    BatchRestorationStatus,
)

_SUPPORTED_SUFFIXES = {".csv", ".html", ".htm"}


def add_files(
    files: list[BatchRestorationFile], paths: Iterable[str | Path]
) -> int:
    known = {_path_key(item.path) for item in files}
    added = 0
    for value in paths:
        path = Path(value).expanduser().absolute()
        suffix = path.suffix.casefold()
        key = _path_key(path)
        if suffix not in _SUPPORTED_SUFFIXES or key in known or not path.is_file():
            continue
        file_type = (
            BatchRestorationFileType.CSV
            if suffix == ".csv"
            else BatchRestorationFileType.HTML
        )
        files.append(BatchRestorationFile(path=path, file_type=file_type))
        known.add(key)
        added += 1
    return added


def discover_files(directory: str | Path) -> list[Path]:
    folder = Path(directory).expanduser()
    if not folder.is_dir():
        raise BatchRestorationError("A pasta selecionada não existe.")
    try:
        return sorted(
            (
                path.absolute()
                for path in folder.iterdir()
                if path.is_file() and path.suffix.casefold() in _SUPPORTED_SUFFIXES
            ),
            key=lambda path: path.name.casefold(),
        )
    except OSError as error:
        raise BatchRestorationError(
            "Não foi possível examinar a pasta selecionada."
        ) from error


def invalidate_files(files: Iterable[BatchRestorationFile]) -> None:
    for item in files:
        item.status = BatchRestorationStatus.PENDING
        item.encoding = ""
        item.delimiter = None
        item.headers = ()
        item.columns.clear()
        item.codes_found = 0
        item.codes_in_vault = 0
        item.missing_codes = 0
        item.result_message = ""
        item.output_path = None


def _path_key(path: Path) -> str:
    return str(path.resolve()).casefold()
