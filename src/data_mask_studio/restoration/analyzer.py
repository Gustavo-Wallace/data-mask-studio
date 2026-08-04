import csv
from itertools import islice
from collections.abc import Callable, MutableMapping

from data_mask_studio.restoration.code_classifier import classify_cell_format
from data_mask_studio.restoration.exceptions import (
    RestorationCancelled,
    RestorationError,
    RestorationSecurityError,
)
from data_mask_studio.restoration.models import (
    AnalysisResult,
    CellClassification,
    RestorationConfiguration,
    RestorationProgress,
    RestorationStage,
)
from data_mask_studio.performance import (
    BALANCED_SETTINGS,
    BoundedCache,
    RestorationMetrics,
)
from data_mask_studio.vault import VaultRepository

ProgressCallback = Callable[[RestorationProgress], None]
CancellationCheck = Callable[[], bool]


def analyze_csv(
    configuration: RestorationConfiguration,
    repository: VaultRepository,
    *,
    progress_callback: ProgressCallback | None = None,
    should_cancel: CancellationCheck | None = None,
    metrics: RestorationMetrics | None = None,
) -> AnalysisResult:
    _validate_configuration(configuration)
    repository = repository.as_read_only()
    input_encoding = _python_encoding(configuration.encoding)
    cache: MutableMapping[str, object | None] = BoundedCache(
        BALANCED_SETTINGS.restoration_cache_limit
    )
    prefixes: list[str] = []
    seen_prefixes: set[str] = set()
    incompatibilities: list[str] = []
    seen_incompatibilities: set[str] = set()
    rows_processed = cells_analyzed = valid_codes = found_codes = 0
    missing_codes = invalid_formats = empty_cells = common_values = 0

    try:
        with configuration.source_path.open(
            "r", encoding=input_encoding, newline=""
        ) as source_file:
            reader = csv.reader(
                source_file, delimiter=configuration.delimiter, strict=True
            )
            headers = next(reader)
            _validate_current_headers(configuration, headers)
            with repository.read_session(metrics) as session:
                while window := list(
                    islice(reader, BALANCED_SETTINGS.restoration_window_rows)
                ):
                    prepared = []
                    codes: list[str] = []
                    first_line = reader.line_num - len(window) + 1
                    for offset, row in enumerate(window):
                        _raise_if_cancelled(should_cancel)
                        _validate_row(row, configuration, first_line + offset)
                        cells = []
                        for column in configuration.selected_columns:
                            classified = classify_cell_format(row[column.index])
                            cells.append((column, classified))
                            if classified.lookup_code is not None:
                                codes.append(classified.lookup_code)
                        prepared.append(cells)
                    resolved = _bulk_lookup(session, codes, cache, metrics)
                    for cells in prepared:
                        _raise_if_cancelled(should_cancel)
                        for column, classified in cells:
                            cells_analyzed += 1
                            if classified.classification is CellClassification.EMPTY:
                                empty_cells += 1
                            elif classified.classification is CellClassification.COMMON:
                                common_values += 1
                            elif classified.classification is CellClassification.INVALID_CODE_LIKE:
                                invalid_formats += 1
                            else:
                                valid_codes += 1
                                assert classified.lookup_code is not None
                                assert classified.prefix is not None
                                if classified.prefix not in seen_prefixes:
                                    prefixes.append(classified.prefix)
                                    seen_prefixes.add(classified.prefix)
                                mapping = resolved[classified.lookup_code]
                                if mapping is None:
                                    missing_codes += 1
                                else:
                                    found_codes += 1
                                    if mapping.source_header != column.header:
                                        issue = (
                                            f"Coluna '{column.header}': codigo associado "
                                            f"originalmente ao cabecalho '{mapping.source_header}'."
                                        )
                                        if issue not in seen_incompatibilities:
                                            incompatibilities.append(issue)
                                            seen_incompatibilities.add(issue)
                        rows_processed += 1
                        if progress_callback is not None:
                            progress_callback(RestorationProgress(RestorationStage.ANALYZING, rows_processed))
    except RestorationError:
        raise
    except (OSError, UnicodeError, csv.Error, StopIteration) as error:
        raise RestorationError(
            "Nao foi possivel analisar o arquivo CSV selecionado."
        ) from error

    return AnalysisResult(
        rows_processed=rows_processed,
        cells_analyzed=cells_analyzed,
        valid_codes=valid_codes,
        found_codes=found_codes,
        missing_codes=missing_codes,
        invalid_formats=invalid_formats,
        empty_cells=empty_cells,
        common_values=common_values,
        prefixes=tuple(prefixes),
        possible_incompatibilities=tuple(incompatibilities),
    )


def _bulk_lookup(session, codes, cache, metrics):
    resolved = {}
    missing = []
    missing_seen = set()
    for code in codes:
        if code in resolved:
            if metrics is not None:
                metrics.cache_hits += 1
            continue
        if code in cache:
            resolved[code] = cache[code]
            if metrics is not None:
                metrics.cache_hits += 1
        elif code not in missing_seen:
            missing.append(code)
            missing_seen.add(code)
            if metrics is not None:
                metrics.cache_misses += 1
    try:
        fetched = session.get_many(missing)
    except Exception as error:
        raise RestorationSecurityError(
            "Não foi possível recuperar um ou mais mapeamentos com segurança."
        ) from error
    for code in missing:
        mapping = fetched.get(code)
        resolved[code] = mapping
        cache[code] = mapping
    return resolved


def _lookup(
    repository: VaultRepository,
    code: str,
    cache: MutableMapping[str, object | None],
):
    if code in cache:
        return cache[code]
    try:
        mapping = repository.get_decrypted_mapping(code)
    except Exception as error:
        raise RestorationSecurityError(
            "Não foi possível recuperar um ou mais mapeamentos com segurança."
        ) from error
    cache[code] = mapping
    return mapping


def _validate_configuration(configuration: RestorationConfiguration) -> None:
    if not configuration.selected_columns:
        raise RestorationError("Selecione ao menos uma coluna para restaurar.")
    if not configuration.source_path.is_file():
        raise RestorationError("O arquivo CSV selecionado nao existe.")
    indexes = [column.index for column in configuration.selected_columns]
    if len(indexes) != len(set(indexes)) or any(
        index < 0 or index >= len(configuration.headers) for index in indexes
    ):
        raise RestorationError("A selecao de colunas e invalida.")


def _validate_current_headers(
    configuration: RestorationConfiguration, headers: list[str]
) -> None:
    if tuple(headers) != configuration.headers:
        raise RestorationError(
            "Os cabecalhos do arquivo foram alterados desde a selecao."
        )


def _validate_row(
    row: list[str], configuration: RestorationConfiguration, row_number: int
) -> None:
    if len(row) != len(configuration.headers):
        raise RestorationError(
            f"Linha {row_number}: quantidade de colunas inconsistente."
        )


def _python_encoding(encoding: str) -> str:
    return "cp1252" if encoding == "windows-1252" else encoding


def _raise_if_cancelled(should_cancel: CancellationCheck | None) -> None:
    if should_cancel is not None and should_cancel():
        raise RestorationCancelled("A operacao de restauracao foi cancelada.")
