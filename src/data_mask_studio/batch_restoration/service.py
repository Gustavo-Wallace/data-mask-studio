import tempfile
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from data_mask_studio.batch_restoration.exceptions import (
    BatchRestorationError,
    BatchRestorationStructuralError,
)
from data_mask_studio.batch_restoration.models import (
    BatchCSVColumn,
    BatchMissingCodePolicy,
    BatchRestorationFile,
    BatchRestorationFileType,
    BatchRestorationOptions,
    BatchRestorationProgress,
    BatchRestorationStatus,
    BatchRestorationSummary,
)
from data_mask_studio.batch_restoration.output_naming import available_output_path
from data_mask_studio.csv_tools import (
    CSVInspectionError,
    format_header_replacement_warning,
    inspect_csv,
)
from data_mask_studio.html_restoration import (
    HTMLMissingCodeError,
    HTMLMissingCodePolicy,
    HTMLRestorationCancelled,
    HTMLRestorationError,
    HTMLRestorationSecurityError,
    HTMLRestorationService,
    inspect_html,
)
from data_mask_studio.restoration import (
    MissingCodeError,
    MissingCodePolicy,
    RestorationCancelled,
    RestorationConfiguration,
    RestorationError,
    RestorationSecurityError,
    RestorationService,
    SelectedColumn,
)
from data_mask_studio.security import KeyProviderError
from data_mask_studio.vault import VaultError, VaultRepository

RepositoryFactory = Callable[[], VaultRepository]
FileCallback = Callable[[BatchRestorationFile], None]
ProgressCallback = Callable[[BatchRestorationProgress], None]
CancellationCheck = Callable[[], bool]


class BatchRestorationService:
    def __init__(self, repository_factory: RepositoryFactory) -> None:
        self._repository_factory = repository_factory

    def analyze_files(
        self,
        files: Iterable[BatchRestorationFile],
        *,
        file_callback: FileCallback | None = None,
        should_cancel: CancellationCheck | None = None,
    ) -> bool:
        was_cancelled = False
        for item in files:
            if _cancelled(should_cancel):
                item.status = BatchRestorationStatus.CANCELLED
                if file_callback:
                    file_callback(item)
                was_cancelled = True
                break
            try:
                self._analyze_file(item, should_cancel)
            except (
                RestorationSecurityError,
                HTMLRestorationSecurityError,
                VaultError,
                KeyProviderError,
            ) as error:
                item.status = BatchRestorationStatus.ERROR
                item.result_message = "Falha estrutural ao acessar o cofre local."
                if file_callback:
                    file_callback(item)
                raise BatchRestorationStructuralError(item.result_message) from error
            except (RestorationCancelled, HTMLRestorationCancelled):
                item.status = BatchRestorationStatus.CANCELLED
                item.result_message = "Análise cancelada."
                if file_callback:
                    file_callback(item)
                was_cancelled = True
                break
            except (CSVInspectionError, RestorationError, HTMLRestorationError) as error:
                item.status = BatchRestorationStatus.INCOMPATIBLE
                item.result_message = str(error)
            if file_callback:
                file_callback(item)
        return was_cancelled

    def _analyze_file(
        self,
        item: BatchRestorationFile,
        should_cancel: CancellationCheck | None,
    ) -> None:
        item.result_message = ""
        header_warning = ""
        if item.file_type is BatchRestorationFileType.CSV:
            inspection = inspect_csv(item.path)
            header_warning = format_header_replacement_warning(
                inspection.header_replacements
            )
            item.encoding = inspection.encoding
            item.delimiter = inspection.delimiter
            item.headers = tuple(inspection.headers)
            item.columns = []
            restoration = RestorationService(self._read_only_repository)
            for index, header in enumerate(inspection.headers):
                configuration = RestorationConfiguration(
                    source_path=inspection.path,
                    encoding=inspection.encoding,
                    delimiter=inspection.delimiter,
                    headers=tuple(inspection.headers),
                    selected_columns=(SelectedColumn(index, header),),
                )
                result = restoration.analyze(
                    configuration, should_cancel=should_cancel
                )
                item.columns.append(
                    BatchCSVColumn(
                        index,
                        header,
                        result.valid_codes,
                        result.found_codes,
                        result.missing_codes,
                    )
                )
            item.codes_found = sum(column.valid_codes for column in item.columns)
            item.codes_in_vault = sum(column.found_codes for column in item.columns)
            item.missing_codes = sum(column.missing_codes for column in item.columns)
        else:
            inspection = inspect_html(item.path)
            result = HTMLRestorationService(self._read_only_repository).analyze(
                inspection, should_cancel=should_cancel
            )
            item.encoding = inspection.encoding
            item.codes_found = result.total_occurrences
            item.codes_in_vault = result.found_codes
            item.missing_codes = result.missing_codes

        if item.codes_found == 0:
            item.status = BatchRestorationStatus.INCOMPATIBLE
            item.result_message = "Nenhum código válido foi encontrado no arquivo."
        elif item.missing_codes:
            item.status = BatchRestorationStatus.REVIEW_REQUIRED
            item.result_message = "Há códigos ausentes que exigem revisão da política."
        else:
            item.status = BatchRestorationStatus.COMPATIBLE
            item.result_message = "Arquivo analisado com sucesso."
        if header_warning:
            item.result_message = f"{item.result_message} {header_warning}"

    def restore_files(
        self,
        files: list[BatchRestorationFile],
        output_directory: str | Path,
        options: BatchRestorationOptions,
        *,
        file_callback: FileCallback | None = None,
        progress_callback: ProgressCallback | None = None,
        should_cancel: CancellationCheck | None = None,
    ) -> BatchRestorationSummary:
        started_at = time.perf_counter()
        output = Path(output_directory).expanduser().absolute()
        _validate_output_directory(output)
        eligible = [
            item
            for item in files
            if item.status
            in {
                BatchRestorationStatus.COMPATIBLE,
                BatchRestorationStatus.REVIEW_REQUIRED,
            }
        ]
        completed = errors = skipped = cancelled_count = restored = missing = 0
        was_cancelled = False

        for position, item in enumerate(eligible, start=1):
            if _cancelled(should_cancel):
                item.status = BatchRestorationStatus.CANCELLED
                item.result_message = "Restauração cancelada."
                cancelled_count += 1
                was_cancelled = True
                if file_callback:
                    file_callback(item)
                _skip_remaining(eligible[position:], file_callback)
                skipped += len(eligible[position:])
                break
            if item.file_type is BatchRestorationFileType.CSV and not any(
                column.selected for column in item.columns
            ):
                item.status = BatchRestorationStatus.SKIPPED
                item.result_message = "Nenhuma coluna foi confirmada para restauração."
                skipped += 1
                if file_callback:
                    file_callback(item)
                continue

            item.status = BatchRestorationStatus.PROCESSING
            if file_callback:
                file_callback(item)
            destination = available_output_path(item.path, output)
            current_progress = lambda value: _emit_progress(
                progress_callback,
                item,
                position,
                len(eligible),
                completed,
                errors,
                value,
            )
            try:
                result = self._restore_file(
                    item,
                    destination,
                    options,
                    current_progress,
                    should_cancel,
                )
                item.output_path = destination
                item.status = BatchRestorationStatus.COMPLETED
                item.result_message = f"Saída: {destination.name}"
                completed += 1
                restored += result[0]
                missing += result[1]
            except (RestorationCancelled, HTMLRestorationCancelled):
                item.status = BatchRestorationStatus.CANCELLED
                item.result_message = "Restauração cancelada sem publicar saída parcial."
                cancelled_count += 1
                was_cancelled = True
                _skip_remaining(eligible[position:], file_callback)
                skipped += len(eligible[position:])
                if file_callback:
                    file_callback(item)
                break
            except (MissingCodeError, HTMLMissingCodeError) as error:
                item.status = BatchRestorationStatus.ERROR
                item.result_message = _safe_error(error)
                errors += 1
                if options.missing_code_policy is BatchMissingCodePolicy.ABORT_BATCH:
                    _skip_remaining(eligible[position:], file_callback)
                    skipped += len(eligible[position:])
                    if file_callback:
                        file_callback(item)
                    break
            except (
                RestorationSecurityError,
                HTMLRestorationSecurityError,
                VaultError,
                KeyProviderError,
            ) as error:
                item.status = BatchRestorationStatus.ERROR
                item.result_message = "Falha estrutural ao acessar o cofre local."
                errors += 1
                if file_callback:
                    file_callback(item)
                _skip_remaining(eligible[position:], file_callback)
                raise BatchRestorationStructuralError(item.result_message) from error
            except (RestorationError, HTMLRestorationError, OSError) as error:
                item.status = BatchRestorationStatus.ERROR
                item.result_message = _safe_error(error)
                errors += 1
            if file_callback:
                file_callback(item)
            _emit_progress(
                progress_callback,
                item,
                position,
                len(eligible),
                completed,
                errors,
                None,
            )

        skipped += sum(
            item.status is BatchRestorationStatus.INCOMPATIBLE for item in files
        )
        return BatchRestorationSummary(
            selected_files=len(files),
            completed_files=completed,
            error_files=errors,
            skipped_files=skipped,
            cancelled_files=cancelled_count,
            restored_occurrences=restored,
            missing_occurrences=missing,
            output_directory=output,
            duration_seconds=time.perf_counter() - started_at,
            cancelled=was_cancelled,
        )

    def _restore_file(
        self,
        item: BatchRestorationFile,
        destination: Path,
        options: BatchRestorationOptions,
        progress_callback: Callable[[object], None],
        should_cancel: CancellationCheck | None,
    ) -> tuple[int, int]:
        abort = options.missing_code_policy is not BatchMissingCodePolicy.KEEP
        if item.file_type is BatchRestorationFileType.CSV:
            assert item.delimiter is not None
            configuration = RestorationConfiguration(
                source_path=item.path,
                encoding=item.encoding,
                delimiter=item.delimiter,
                headers=item.headers,
                selected_columns=tuple(
                    SelectedColumn(column.index, column.header)
                    for column in item.columns
                    if column.selected
                ),
                missing_code_policy=(
                    MissingCodePolicy.ABORT if abort else MissingCodePolicy.KEEP
                ),
                representation_policy=options.representation_policy,
            )
            result = RestorationService(self._read_only_repository).restore(
                configuration,
                destination,
                overwrite=False,
                progress_callback=progress_callback,
                should_cancel=should_cancel,
            )
            return result.restored_codes, result.missing_codes
        inspection = inspect_html(item.path)
        result = HTMLRestorationService(self._read_only_repository).restore(
            inspection,
            destination,
            missing_code_policy=(
                HTMLMissingCodePolicy.ABORT if abort else HTMLMissingCodePolicy.KEEP
            ),
            representation_policy=options.representation_policy,
            overwrite=False,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
        )
        return result.restored_occurrences, result.missing_occurrences

    def _read_only_repository(self) -> VaultRepository:
        return self._repository_factory().as_read_only()


def _validate_output_directory(output: Path) -> None:
    if not output.is_dir():
        raise BatchRestorationStructuralError("A pasta de saída não está acessível.")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=output, prefix=".dms-access-", delete=False) as file:
            temporary = Path(file.name)
    except OSError as error:
        raise BatchRestorationStructuralError(
            "A pasta de saída não permite criar arquivos."
        ) from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _cancelled(check: CancellationCheck | None) -> bool:
    return check is not None and check()


def _skip_remaining(
    files: Iterable[BatchRestorationFile], callback: FileCallback | None
) -> None:
    for item in files:
        item.status = BatchRestorationStatus.SKIPPED
        item.result_message = "Ignorado porque o lote foi interrompido."
        if callback:
            callback(item)


def _safe_error(error: Exception) -> str:
    if isinstance(error, (MissingCodeError, HTMLMissingCodeError)):
        return "Arquivo interrompido porque há código ausente no cofre."
    return str(error)


def _emit_progress(
    callback: ProgressCallback | None,
    item: BatchRestorationFile,
    position: int,
    total: int,
    completed: int,
    errors: int,
    value: object | None,
) -> None:
    if callback is None:
        return
    current_value = int(
        getattr(value, "rows_processed", getattr(value, "processed_bytes", 0))
    )
    current_total = int(getattr(value, "total_bytes", 0))
    callback(
        BatchRestorationProgress(
            position,
            total,
            completed,
            errors,
            item.path.name,
            current_value,
            current_total,
        )
    )
