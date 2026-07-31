import csv
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from data_mask_studio.csv_tools.csv_anonymizer import paths_refer_to_same_file
from data_mask_studio.restoration.analyzer import (
    _lookup,
    _python_encoding,
    _raise_if_cancelled,
    _validate_configuration,
    _validate_current_headers,
    _validate_row,
)
from data_mask_studio.restoration.code_classifier import classify_cell_format
from data_mask_studio.restoration.exceptions import (
    MissingCodeError,
    RestorationCancelled,
    RestorationError,
    RestorationSecurityError,
)
from data_mask_studio.restoration.models import (
    CellClassification,
    MissingCodePolicy,
    RepresentationPolicy,
    RestorationConfiguration,
    RestorationProgress,
    RestorationResult,
    RestorationStage,
)
from data_mask_studio.vault import VaultRepository

ProgressCallback = Callable[[RestorationProgress], None]
CancellationCheck = Callable[[], bool]


def restore_csv(
    configuration: RestorationConfiguration,
    destination_path: str | Path,
    repository: VaultRepository,
    *,
    overwrite: bool = False,
    progress_callback: ProgressCallback | None = None,
    should_cancel: CancellationCheck | None = None,
) -> RestorationResult:
    """Restaura somente colunas escolhidas e publica o CSV atomicamente."""
    _validate_configuration(configuration)
    source = configuration.source_path.expanduser().absolute()
    destination = Path(destination_path).expanduser().absolute()
    _validate_destination(source, destination, overwrite)
    repository = repository.as_read_only()
    temporary_path: Path | None = None
    started_at = time.perf_counter()
    rows_processed = restored_codes = missing_codes = 0
    preserved_common_values = empty_cells = 0
    cache: dict[str, object | None] = {}

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8-sig",
            newline="",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            writer = csv.writer(temporary_file, delimiter=configuration.delimiter)
            with source.open(
                "r",
                encoding=_python_encoding(configuration.encoding),
                newline="",
            ) as source_file:
                reader = csv.reader(
                    source_file, delimiter=configuration.delimiter, strict=True
                )
                headers = next(reader)
                _validate_current_headers(configuration, headers)
                writer.writerow(headers)
                for row in reader:
                    _raise_if_cancelled(should_cancel)
                    _validate_row(row, configuration, reader.line_num)
                    restored_row = list(row)
                    for column in configuration.selected_columns:
                        classified = classify_cell_format(row[column.index])
                        if classified.classification is CellClassification.EMPTY:
                            empty_cells += 1
                            continue
                        if classified.classification in (
                            CellClassification.COMMON,
                            CellClassification.INVALID_CODE_LIKE,
                        ):
                            preserved_common_values += 1
                            continue
                        assert classified.lookup_code is not None
                        mapping = _lookup(
                            repository, classified.lookup_code, cache
                        )
                        if mapping is None:
                            missing_codes += 1
                            if (
                                configuration.missing_code_policy
                                is MissingCodePolicy.EMPTY
                            ):
                                restored_row[column.index] = ""
                            elif (
                                configuration.missing_code_policy
                                is MissingCodePolicy.ABORT
                            ):
                                raise MissingCodeError(
                                    "A restauracao foi interrompida porque um codigo "
                                    f"nao foi encontrado (coluna '{column.header}', "
                                    f"linha {reader.line_num})."
                                )
                            continue
                        if (
                            configuration.representation_policy
                            is RepresentationPolicy.CANONICAL
                        ):
                            restored_row[column.index] = mapping.canonical_value
                        else:
                            restored_row[column.index] = mapping.original_value
                        restored_codes += 1
                    writer.writerow(restored_row)
                    rows_processed += 1
                    if progress_callback is not None:
                        progress_callback(
                            RestorationProgress(
                                RestorationStage.RESTORING,
                                rows_processed,
                                restored_codes,
                                missing_codes,
                                preserved_common_values,
                            )
                        )
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
            _raise_if_cancelled(should_cancel)

        if destination.exists() and not overwrite:
            raise RestorationError("O arquivo de destino ja existe.")
        os.replace(temporary_path, destination)
        temporary_path = None
    except (RestorationCancelled, MissingCodeError, RestorationSecurityError):
        raise
    except RestorationError:
        raise
    except (OSError, UnicodeError, csv.Error, StopIteration) as error:
        raise RestorationError(
            "Nao foi possivel gerar o arquivo CSV restaurado."
        ) from error
    except Exception as error:
        raise RestorationSecurityError(
            "Não foi possível recuperar um ou mais mapeamentos com segurança."
        ) from error
    finally:
        if temporary_path is not None and temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError:
                pass

    return RestorationResult(
        output_path=destination,
        rows_processed=rows_processed,
        restored_codes=restored_codes,
        missing_codes=missing_codes,
        preserved_common_values=preserved_common_values,
        empty_cells=empty_cells,
        duration_seconds=time.perf_counter() - started_at,
        missing_code_policy=configuration.missing_code_policy,
        representation_policy=configuration.representation_policy,
    )


def _validate_destination(source: Path, destination: Path, overwrite: bool) -> None:
    if paths_refer_to_same_file(source, destination):
        raise RestorationError(
            "O arquivo restaurado nao pode substituir o CSV de entrada."
        )
    if not destination.parent.is_dir():
        raise RestorationError("A pasta escolhida para o arquivo nao existe.")
    if destination.exists() and not overwrite:
        raise RestorationError("O arquivo de destino ja existe.")


def suggested_output_path(source_path: str | Path) -> Path:
    source = Path(source_path)
    stem = source.stem
    suffix = "_anonimizado"
    if stem.lower().endswith(suffix):
        stem = stem[: -len(suffix)]
    return source.with_name(f"{stem}_restaurado.csv")
