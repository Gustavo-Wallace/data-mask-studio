import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

from data_mask_studio.batch.exceptions import BatchError
from data_mask_studio.batch.models import BatchFile, BatchFileStatus
from data_mask_studio.csv_tools import CSVInspectionError, inspect_csv
from data_mask_studio.profiles import ConfigurationProfile, ProfileService


def add_files(files: list[BatchFile], paths: Iterable[str | Path]) -> int:
    known = {_path_key(item.path) for item in files}
    added = 0
    for value in paths:
        path = Path(value).expanduser().absolute()
        key = _path_key(path)
        if path.suffix.lower() != ".csv" or key in known:
            continue
        files.append(BatchFile(path=path))
        known.add(key)
        added += 1
    return added


def discover_csv_files(directory: str | Path) -> list[Path]:
    folder = Path(directory).expanduser().absolute()
    if not folder.is_dir():
        raise BatchError("A pasta selecionada não existe.")
    try:
        return sorted(
            (
                item.absolute()
                for item in folder.iterdir()
                if item.is_file() and item.suffix.lower() == ".csv"
            ),
            key=lambda item: item.name.casefold(),
        )
    except OSError as error:
        raise BatchError("Não foi possível examinar a pasta selecionada.") from error


def invalidate_files(files: list[BatchFile]) -> None:
    for item in files:
        item.status = BatchFileStatus.PENDING
        item.column_count = None
        item.encoding = None
        item.delimiter = None
        item.headers = ()
        item.missing_headers = ()
        item.result_message = ""
        item.output_path = None
        item.records_processed = 0
        item.new_mappings = 0
        item.updated_mappings = 0
        item.error_type = None


def validate_file(
    item: BatchFile,
    profile: ConfigurationProfile,
    profile_service: ProfileService,
) -> None:
    item.status = BatchFileStatus.PENDING
    item.column_count = None
    item.encoding = None
    item.delimiter = None
    item.headers = ()
    item.missing_headers = ()
    item.result_message = ""
    item.output_path = None
    item.records_processed = 0
    item.new_mappings = 0
    item.updated_mappings = 0
    item.error_type = None
    try:
        inspection = inspect_csv(item.path)
    except CSVInspectionError as error:
        item.status = BatchFileStatus.INCOMPATIBLE
        item.result_message = str(error)
        return
    application = profile_service.apply(profile, inspection.headers)
    item.column_count = len(inspection.headers)
    item.encoding = inspection.encoding
    item.delimiter = inspection.delimiter
    item.headers = tuple(inspection.headers)
    item.missing_headers = application.missing_headers
    if application.is_complete:
        item.status = BatchFileStatus.COMPATIBLE
        item.result_message = "Arquivo compatível com o perfil."
    else:
        item.status = BatchFileStatus.INCOMPATIBLE
        missing = ", ".join(application.missing_headers)
        item.result_message = f"Cabeçalhos não encontrados: {missing}."


def validate_output_directory(directory: str | Path) -> Path:
    output = Path(directory).expanduser().absolute()
    if not output.is_dir():
        raise BatchError("A pasta de saída não existe.")
    temporary_path: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".dms-write-", dir=output)
        os.close(descriptor)
        temporary_path = Path(name)
    except OSError as error:
        raise BatchError("A pasta de saída não permite gravação.") from error
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
    return output


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))
