import csv
import hmac
import os
import tempfile
import time
from data_mask_studio.environment import guarded
from collections.abc import Callable, Sequence
from contextlib import nullcontext
from pathlib import Path

from data_mask_studio.anonymization.anonymizer import anonymize_row_with_metadata
from data_mask_studio.anonymization.column_config import validate_configuration
from data_mask_studio.anonymization.models import (
    AnonymizationResult,
    ColumnAction,
    ColumnConfig,
    NormalizationFallback,
    RuleFallbackCount,
)
from data_mask_studio.csv_tools.encoding import python_codec
from data_mask_studio.csv_tools.header_resolver import resolve_empty_headers
from data_mask_studio.publication import Publication, PublicationError, publish
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.performance import BALANCED_SETTINGS, PerformanceSettings
from data_mask_studio.processing.models import BoundCompositeColumn, ProcessingPlan
from data_mask_studio.processing.composite_executor import CompositeExecutionError, execute_composite_row
from data_mask_studio.vault import MappingCandidate, VaultCollisionError, VaultError
from data_mask_studio.vault.models import VaultUpdateSummary
from data_mask_studio.vault.repository import VaultRepository, VaultTransaction

ProgressCallback = Callable[[int], None]
CancellationCheck = Callable[[], bool]


class CSVAnonymizationError(RuntimeError):
    """Falha esperada durante a geração do CSV anonimizado."""


class ProcessingCancelled(CSVAnonymizationError):
    """Processamento interrompido a pedido do usuário."""


def resolve_anonymization_environment(
    *args,
    processing_plan: ProcessingPlan | None = None,
    vault_repository: VaultRepository | None = None,
    **kwargs,
) -> Path | None:
    """Decide environment dependency before inspecting a supplied repository."""
    if processing_plan is not None and not processing_plan.requires_masking:
        return None
    # Legacy processing can mask without a ProcessingPlan: retain its guard.
    return vault_repository.database_path.parent if vault_repository is not None else None


@guarded(resolve_anonymization_environment)
def anonymize_csv(
    source_path: str | Path,
    destination_path: str | Path,
    *,
    encoding: str,
    delimiter: str,
    configurations: Sequence[ColumnConfig] = (),
    secret_key: bytes | None = None,
    overwrite: bool = False,
    progress_callback: ProgressCallback | None = None,
    should_cancel: CancellationCheck | None = None,
    vault_repository: VaultRepository | None = None,
    mapping_batch_size: int | None = None,
    performance_settings: PerformanceSettings = BALANCED_SETTINGS,
    processing_plan: ProcessingPlan | None = None,
) -> AnonymizationResult:
    """Processa um CSV linha a linha e publica o resultado de forma atômica."""
    source = Path(source_path).expanduser().absolute()
    destination = Path(destination_path).expanduser().absolute()
    # O plano validado é a autoridade; configurações legadas não o sobrescrevem.
    if processing_plan is not None:
        if not processing_plan.requires_masking:
            vault_repository = None
        configurations = tuple(ColumnConfig(
            column.reference.header, prefix=column.prefix,
            normalization_rule=column.normalization_rule, action=column.action,
            output_name=column.output_name,
        ) for column in processing_plan.physical_columns)
    effective_batch_size = mapping_batch_size or performance_settings.mapping_batch_size
    _validate_request(
        source,
        destination,
        configurations,
        secret_key,
        overwrite,
        effective_batch_size,
        processing_plan,
    )

    temporary_path: Path | None = None
    publication = None
    if vault_repository is not None and secret_key is not None:
        publication = Publication(vault_repository.database_path, source, destination, secret_key, overwrite)
    started_at = time.perf_counter()
    records_processed = 0
    vault_summary = VaultUpdateSummary()
    input_encoding = python_codec(encoding)
    pending_mappings: dict[str, MappingCandidate] = {}
    fallback_counts: dict[int, int] = {}
    composite_fallbacks: dict[NormalizationRule, int] = {}
    scalar_occurrences = composite_occurrences = composite_tokens = 0
    has_composites = processing_plan is not None and any(
        isinstance(output, BoundCompositeColumn) for output in processing_plan.outputs
    )
    has_masked_composites = processing_plan is not None and any(
        isinstance(output, BoundCompositeColumn) and output.action is ColumnAction.MASK
        for output in processing_plan.outputs
    )
    if has_masked_composites and vault_repository is None:
        raise CSVAnonymizationError("Um cofre é necessário para processar composites.")
    transaction_context = (
        vault_repository.transaction()
        if vault_repository is not None
        else nullcontext(None)
    )

    vault_transaction = None
    try:
        composite_repository = (
            vault_repository.composite_repository() if has_masked_composites else None
        )
        with transaction_context as vault_transaction:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8-sig",
                newline="",
                prefix=publication.temp_prefix if publication else f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
                delete=False,
                buffering=performance_settings.io_buffer_size,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                writer = csv.writer(temporary_file, delimiter=delimiter)
                with source.open(
                    "r",
                    encoding=input_encoding,
                    newline="",
                    buffering=performance_settings.io_buffer_size,
                ) as source_file:
                    reader = csv.reader(source_file, delimiter=delimiter, strict=True)
                    headers, _replacements = resolve_empty_headers(next(reader))
                    expected_headers = [
                        configuration.header for configuration in configurations
                    ]
                    if headers != expected_headers:
                        raise CSVAnonymizationError(
                            "Os cabeçalhos do arquivo foram alterados desde a seleção."
                        )
                    writer.writerow(
                        processing_plan.final_headers if processing_plan is not None else
                        [
                            configuration.effective_output_header
                            for header, configuration in zip(
                                headers, configurations, strict=True
                            )
                            if configuration.action is not ColumnAction.EXCLUDE
                        ]
                    )

                    for row in reader:
                        if len(configurations) == 1 and not row:
                            row = [""]
                        if len(row) != len(configurations):
                            raise CSVAnonymizationError(
                                "A linha "
                                f"{reader.line_num} possui estrutura CSV irregular. "
                                "Colunas esperadas conforme o cabeçalho: "
                                f"{len(configurations)}; colunas encontradas: {len(row)}."
                            )
                        if should_cancel is not None and should_cancel():
                            raise ProcessingCancelled("A geração do CSV foi cancelada.")
                        (
                            anonymized_row,
                            canonical_values,
                            effective_rules,
                            fallback_indexes,
                        ) = anonymize_row_with_metadata(
                            row, configurations, secret_key
                        )
                        for index in fallback_indexes:
                            fallback_counts[index] = fallback_counts.get(index, 0) + 1
                        scalar_occurrences += len(canonical_values)
                        if vault_transaction is not None:
                            _collect_mappings(
                                row,
                                anonymized_row,
                                canonical_values,
                                effective_rules,
                                configurations,
                                pending_mappings,
                            )
                            if (
                                len(pending_mappings) >= effective_batch_size
                                or (records_processed + 1) % effective_batch_size == 0
                            ):
                                _flush_mappings(vault_transaction, pending_mappings)
                        if has_composites:
                            composite_result = execute_composite_row(row, processing_plan, secret_key)
                            for fallback in composite_result.fallbacks:
                                composite_fallbacks[fallback.rule] = (
                                    composite_fallbacks.get(fallback.rule, 0) + fallback.count
                                )
                            for cell in composite_result.cells:
                                anonymized_row.append(cell.value)
                                if cell.candidate is not None:
                                    composite_repository.upsert_composite_mapping(
                                        cell.candidate, transaction=vault_transaction,
                                    )
                                    composite_occurrences += cell.candidate.occurrences
                                    composite_tokens += 1
                        writer.writerow(anonymized_row)
                        records_processed += 1
                        if progress_callback is not None:
                            progress_callback(records_processed)

                if vault_transaction is not None:
                    _flush_mappings(vault_transaction, pending_mappings)
                    vault_summary = vault_transaction.summary()
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
                if should_cancel is not None and should_cancel():
                    raise ProcessingCancelled("A geração do CSV foi cancelada.")

            # The file is closed and fsync'ed before the transaction commits.
            if publication is not None:
                publication.prepare(temporary_path)
                if should_cancel is not None and should_cancel():
                    raise ProcessingCancelled("A geração do CSV foi cancelada.")

        if publication is not None:
            publication.committed()
        publish(temporary_path, destination, overwrite)
        temporary_path = None
        if publication is not None:
            publication.complete()
    except ProcessingCancelled:
        raise
    except CSVAnonymizationError:
        raise
    except PublicationError as error:
        raise CSVAnonymizationError(str(error)) from error
    except CompositeExecutionError:
        raise CSVAnonymizationError("Não foi possível executar as composites do CSV.") from None
    except VaultError as error:
        raise CSVAnonymizationError(str(error)) from error
    except OSError as error:
        if publication is not None and publication.retained:
            raise CSVAnonymizationError(
                "A publicação não foi concluída. Operação pendente preservada para recuperação."
            ) from error
        raise CSVAnonymizationError(_safe_io_message(error)) from error
    except (UnicodeError, csv.Error, StopIteration) as error:
        raise CSVAnonymizationError(
            "Não foi possível gerar o arquivo CSV anonimizado."
        ) from error
    except Exception as error:
        raise CSVAnonymizationError(
            "O processamento foi interrompido por um erro inesperado."
        ) from error
    finally:
        if publication is not None and vault_transaction is not None and vault_transaction.publication_outcome == "rolled_back":
            # Only a proven rollback permits discarding a READY journal.
            try:
                publication.path.unlink(missing_ok=True)
                publication.retained = False
            except OSError:
                pass
        if temporary_path is not None and temporary_path.exists() and not (publication and publication.retained):
            try:
                temporary_path.unlink()
            except OSError:
                pass

    return AnonymizationResult(
        output_path=destination,
        records_processed=records_processed,
        duration_seconds=time.perf_counter() - started_at,
        new_mappings=vault_summary.new_mappings,
        updated_mappings=vault_summary.updated_mappings,
        normalization_fallbacks=tuple(
            NormalizationFallback(configurations[index].header, count)
            for index, count in sorted(fallback_counts.items())
        ),
        scalar_mapping_occurrences=scalar_occurrences,
        composite_mapping_occurrences=composite_occurrences,
        composite_tokens_generated=composite_tokens,
        composite_normalization_fallbacks=tuple(
            RuleFallbackCount(rule, count) for rule, count in composite_fallbacks.items()
        ),
    )


def _collect_mappings(
    original_row: Sequence[str],
    anonymized_row: Sequence[str],
    canonical_values: dict[int, str],
    effective_rules: dict[int, NormalizationRule],
    configurations: Sequence[ColumnConfig],
    pending: dict[str, MappingCandidate],
) -> None:
    output_index = 0
    for index, configuration in enumerate(configurations):
        if configuration.action is ColumnAction.EXCLUDE:
            continue
        if (
            configuration.action is not ColumnAction.MASK
            or index >= len(original_row)
            or index not in canonical_values
        ):
            output_index += 1
            continue
        original_value = original_row[index]
        if original_value == "" or original_value.isspace():
            continue
        code = anonymized_row[output_index]
        output_index += 1
        effective_rule = effective_rules[index]
        existing = pending.get(code)
        if existing is None:
            pending[code] = MappingCandidate(
                code=code,
                prefix=configuration.prefix,
                original_value=original_value,
                source_header=configuration.header,
                canonical_value=canonical_values[index],
                normalization_rule=effective_rule,
            )
            continue
        canonical_matches = hmac.compare_digest(
            (existing.canonical_value or "").encode("utf-8"),
            canonical_values[index].encode("utf-8"),
        )
        if existing.prefix != configuration.prefix or not canonical_matches:
            raise VaultCollisionError(
                "Foi detectado um conflito de código no cofre local."
            )
        existing.add_variation(original_value, effective_rule)


def _flush_mappings(
    transaction: VaultTransaction,
    pending: dict[str, MappingCandidate],
) -> None:
    if not pending:
        return
    transaction.upsert_batch(list(pending.values()))
    pending.clear()


def paths_refer_to_same_file(first: str | Path, second: str | Path) -> bool:
    """Compara caminhos existentes ou ainda não criados de forma segura."""
    first_path = Path(first)
    second_path = Path(second)
    if first_path.exists() and second_path.exists():
        try:
            return first_path.samefile(second_path)
        except OSError:
            pass
    first_normalized = os.path.normcase(os.path.abspath(first))
    second_normalized = os.path.normcase(os.path.abspath(second))
    return first_normalized == second_normalized


def _validate_request(
    source: Path,
    destination: Path,
    configurations: Sequence[ColumnConfig],
    secret_key: bytes | None,
    overwrite: bool,
    mapping_batch_size: int,
    processing_plan: ProcessingPlan | None = None,
) -> None:
    if paths_refer_to_same_file(source, destination):
        raise CSVAnonymizationError(
            "O arquivo de saída não pode ser o mesmo arquivo CSV original."
        )
    if not source.is_file():
        raise CSVAnonymizationError("O arquivo CSV original não existe.")
    if not destination.parent.is_dir():
        raise CSVAnonymizationError("A pasta escolhida para o arquivo não existe.")
    if destination.exists() and not overwrite:
        raise CSVAnonymizationError("O arquivo de destino já existe.")
    needs_key = (processing_plan.requires_masking if processing_plan is not None else
                 any(column.action is ColumnAction.MASK for column in configurations))
    if needs_key and not secret_key:
        raise CSVAnonymizationError("A chave secreta local é inválida.")
    if processing_plan is None:
        validation = validate_configuration(configurations)
        if not validation.is_valid:
            raise CSVAnonymizationError(
                validation.error_message or "A configuração das colunas é inválida."
            )
    if mapping_batch_size <= 0:
        raise CSVAnonymizationError("O tamanho do lote de mapeamentos é inválido.")


def _safe_io_message(error: OSError) -> str:
    if getattr(error, "errno", None) == 28 or getattr(error, "winerror", None) == 112:
        return "Não há espaço suficiente para concluir o arquivo de saída."
    if isinstance(error, FileNotFoundError):
        return "O arquivo CSV original foi removido durante o processamento."
    if isinstance(error, PermissionError):
        return "O arquivo está bloqueado ou a pasta de destino não permite escrita."
    return "Falha de leitura ou escrita durante o processamento do CSV."
