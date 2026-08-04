import time
from collections.abc import Callable
from pathlib import Path

from data_mask_studio.anonymization import ColumnConfig, NormalizationFallback
from data_mask_studio.batch.exceptions import BatchError, BatchStructuralError
from data_mask_studio.batch.models import (
    BatchErrorType,
    BatchFile,
    BatchFileResult,
    BatchFileStatus,
    BatchProgress,
    BatchSummary,
    CancellationRequest,
)
from data_mask_studio.batch.output_naming import reserve_output_path
from data_mask_studio.batch.validation import validate_file, validate_output_directory
from data_mask_studio.csv_tools import ProcessingCancelled, anonymize_csv
from data_mask_studio.csv_tools.csv_anonymizer import CSVAnonymizationError
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.profiles import ConfigurationProfile, ProfileService
from data_mask_studio.security import KeyProvider, KeyProviderError
from data_mask_studio.vault import (
    VaultCollisionError,
    VaultEncryptionError,
    VaultError,
    VaultRepository,
)

FileCallback = Callable[[BatchFile], None]
ProgressCallback = Callable[[BatchProgress], None]


class BatchService:
    def __init__(self, profile_service: ProfileService) -> None:
        self.profile_service = profile_service

    def validate(
        self,
        files: list[BatchFile],
        profile: ConfigurationProfile,
        *,
        cancellation: CancellationRequest | None = None,
        file_callback: FileCallback | None = None,
    ) -> None:
        for item in files:
            if cancellation is not None and cancellation.is_requested():
                return
            validate_file(item, profile, self.profile_service)
            if file_callback is not None:
                file_callback(item)

    def process(
        self,
        files: list[BatchFile],
        profile: ConfigurationProfile,
        output_directory: str | Path,
        key_provider: KeyProvider,
        vault_repository_factory: Callable[[], VaultRepository],
        *,
        cancellation: CancellationRequest | None = None,
        file_callback: FileCallback | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> BatchSummary:
        compatible = [item for item in files if item.status is BatchFileStatus.COMPATIBLE]
        if not files:
            raise BatchError("Adicione ao menos um arquivo ao lote.")
        if any(item.status is BatchFileStatus.PENDING for item in files):
            raise BatchError("Valide todos os arquivos antes de iniciar o lote.")
        if not compatible:
            raise BatchError("O lote não possui arquivos compatíveis.")
        try:
            output = validate_output_directory(output_directory)
        except BatchError as error:
            _mark_remaining_skipped(compatible, file_callback)
            raise BatchStructuralError(str(error)) from error

        started_at = time.perf_counter()
        cancellation = cancellation or CancellationRequest()
        try:
            secret_key = key_provider.get_key()
            vault_repository = vault_repository_factory()
        except (KeyProviderError, VaultError) as error:
            _mark_remaining_skipped(compatible, file_callback)
            raise BatchStructuralError(str(error)) from error
        except Exception as error:
            _mark_remaining_skipped(compatible, file_callback)
            raise BatchStructuralError(
                "Não foi possível preparar os recursos seguros do lote."
            ) from error
        if not secret_key:
            _mark_remaining_skipped(compatible, file_callback)
            raise BatchStructuralError("A chave HMAC local não está disponível.")

        for current_index, item in enumerate(compatible, start=1):
            if cancellation.is_requested():
                _mark_remaining_cancelled(compatible[current_index - 1 :], file_callback)
                break
            item.status = BatchFileStatus.PROCESSING
            item.result_message = "Processando arquivo."
            _notify_file(item, file_callback)
            reservation: Path | None = None
            try:
                reservation = reserve_output_path(output, item.path)
                configurations = _profile_configurations(profile, item.headers)

                def report_records(records: int) -> None:
                    item.records_processed = records
                    if progress_callback is not None:
                        progress_callback(
                            _progress(files, item, current_index, len(compatible))
                        )

                result = anonymize_csv(
                    item.path,
                    reservation,
                    encoding=item.encoding or "utf-8",
                    delimiter=item.delimiter or ",",
                    configurations=configurations,
                    secret_key=secret_key,
                    overwrite=True,
                    progress_callback=report_records,
                    should_cancel=cancellation.is_requested,
                    vault_repository=vault_repository,
                )
            except ProcessingCancelled:
                _remove_reservation(reservation)
                item.status = BatchFileStatus.CANCELLED
                item.error_type = BatchErrorType.CANCELLATION
                item.result_message = "Processamento cancelado."
                _notify_file(item, file_callback)
                _mark_remaining_cancelled(compatible[current_index:], file_callback)
                break
            except Exception as error:
                _remove_reservation(reservation)
                structural = _is_structural_error(error)
                item.status = BatchFileStatus.ERROR
                item.error_type = (
                    BatchErrorType.STRUCTURAL if structural else BatchErrorType.FILE
                )
                item.result_message = _safe_error_message(error)
                _notify_file(item, file_callback)
                if structural:
                    _mark_remaining_skipped(compatible[current_index:], file_callback)
                    break
                continue
            item.status = BatchFileStatus.COMPLETED
            item.output_path = result.output_path
            item.records_processed = result.records_processed
            item.new_mappings = result.new_mappings
            item.updated_mappings = result.updated_mappings
            item.normalization_fallbacks = result.normalization_fallbacks
            item.result_message = _completed_message(result.normalization_fallbacks)
            _notify_file(item, file_callback)
            if progress_callback is not None:
                progress_callback(_progress(files, item, current_index, len(compatible)))

        return _summary(files, len(compatible), output, started_at)


def _profile_configurations(
    profile: ConfigurationProfile, headers: tuple[str, ...]
) -> list[ColumnConfig]:
    columns = {column.header: column for column in profile.columns}
    return [
        ColumnConfig(
            header=header,
            anonymize=header in columns,
            prefix=columns[header].prefix if header in columns else "",
            normalization_rule=(
                columns[header].normalization_rule
                if header in columns
                else NormalizationRule.EXACT
            ),
        )
        for header in headers
    ]


def _is_structural_error(error: Exception) -> bool:
    current: BaseException | None = error
    while current is not None:
        if isinstance(
            current, (BatchStructuralError, KeyProviderError, VaultEncryptionError)
        ):
            return True
        if isinstance(current, VaultError) and not isinstance(
            current, VaultCollisionError
        ):
            return True
        current = current.__cause__
    return False


def _safe_error_message(error: Exception) -> str:
    if isinstance(error, (BatchError, CSVAnonymizationError)):
        message = str(error).strip()
        if message:
            return message
    return "Não foi possível processar este arquivo."


def _remove_reservation(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _notify_file(item: BatchFile, callback: FileCallback | None) -> None:
    if callback is not None:
        callback(item)


def _mark_remaining_cancelled(
    files: list[BatchFile], callback: FileCallback | None
) -> None:
    for item in files:
        item.status = BatchFileStatus.CANCELLED
        item.error_type = BatchErrorType.CANCELLATION
        item.result_message = "Não processado devido ao cancelamento."
        _notify_file(item, callback)


def _mark_remaining_skipped(
    files: list[BatchFile], callback: FileCallback | None
) -> None:
    for item in files:
        item.status = BatchFileStatus.SKIPPED
        item.result_message = "Ignorado devido a uma falha estrutural."
        _notify_file(item, callback)


def _progress(
    all_files: list[BatchFile],
    current: BatchFile,
    current_index: int,
    compatible_count: int,
) -> BatchProgress:
    return BatchProgress(
        current_file=current_index,
        compatible_files=compatible_count,
        file_name=current.path.name,
        records_processed=current.records_processed,
        completed_files=sum(
            item.status is BatchFileStatus.COMPLETED for item in all_files
        ),
        error_files=sum(item.status is BatchFileStatus.ERROR for item in all_files),
    )


def _summary(
    files: list[BatchFile],
    compatible_count: int,
    output: Path,
    started_at: float,
) -> BatchSummary:
    return BatchSummary(
        selected_files=len(files),
        compatible_files=compatible_count,
        completed_files=sum(item.status is BatchFileStatus.COMPLETED for item in files),
        incompatible_files=sum(
            item.status is BatchFileStatus.INCOMPATIBLE for item in files
        ),
        error_files=sum(item.status is BatchFileStatus.ERROR for item in files),
        cancelled_or_skipped_files=sum(
            item.status in {BatchFileStatus.CANCELLED, BatchFileStatus.SKIPPED}
            for item in files
        ),
        records_processed=sum(item.records_processed for item in files),
        new_mappings=sum(item.new_mappings for item in files),
        updated_mappings=sum(item.updated_mappings for item in files),
        duration_seconds=time.perf_counter() - started_at,
        output_directory=output,
        normalization_fallbacks=_aggregate_fallbacks(files),
        results=tuple(
            BatchFileResult(
                path=item.path,
                status=item.status,
                output_path=item.output_path,
                message=item.result_message,
                records_processed=item.records_processed,
            )
            for item in files
        ),
    )


def _aggregate_fallbacks(
    files: list[BatchFile],
) -> tuple[NormalizationFallback, ...]:
    counts: dict[str, int] = {}
    for item in files:
        for fallback in item.normalization_fallbacks:
            counts[fallback.header] = counts.get(fallback.header, 0) + fallback.count
    return tuple(
        NormalizationFallback(header, count)
        for header, count in sorted(counts.items(), key=lambda item: item[0].casefold())
    )


def _completed_message(
    fallbacks: tuple[NormalizationFallback, ...],
) -> str:
    if not fallbacks:
        return "Arquivo anonimizado com sucesso."
    details = "; ".join(f"{item.header}: {item.count}" for item in fallbacks)
    return (
        "Arquivo anonimizado com sucesso. Alguns valores incompatíveis com a "
        f"normalização foram processados por valor exato. {details}."
    )
