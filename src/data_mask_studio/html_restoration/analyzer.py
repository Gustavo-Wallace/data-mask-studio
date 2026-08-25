import time
from collections.abc import Callable, Iterator

from data_mask_studio.html_restoration.exceptions import (
    HTMLRestorationCancelled,
    HTMLRestorationError,
    HTMLRestorationSecurityError,
)
from data_mask_studio.html_restoration.models import (
    HTMLAnalysisResult,
    HTMLInspectionResult,
    HTMLRestorationProgress,
    HTMLRestorationStage,
)
from data_mask_studio.html_restoration.scanner import (
    CancellationCheck,
    is_valid_candidate,
    iter_candidates,
    iter_html_segments,
    iter_timed_html_segments,
)
from data_mask_studio.performance import BALANCED_SETTINGS, HTMLProcessingMetrics
from data_mask_studio.vault import VaultRepository

ProgressCallback = Callable[[HTMLRestorationProgress], None]


def analyze_html(
    inspection: HTMLInspectionResult,
    repository: VaultRepository,
    *,
    progress_callback: ProgressCallback | None = None,
    should_cancel: CancellationCheck | None = None,
    metrics: HTMLProcessingMetrics | None = None,
) -> HTMLAnalysisResult:
    repository = repository.as_read_only()
    unique_codes: set[str] = set()
    invalid_codes: set[str] = set()
    total_occurrences = 0
    invalid_similar_codes = 0
    prefixes: list[str] = []
    seen_prefixes: set[str] = set()

    try:
        segments = iter_html_segments(
            inspection.path,
            inspection.encoding,
            should_cancel=should_cancel,
        )
        for segment in iter_timed_html_segments(segments, metrics):
            if metrics is not None:
                started = time.perf_counter()
                candidates = list(iter_candidates(segment))
                metrics.token_scanning_seconds += time.perf_counter() - started
                metrics.candidates_scanned += len(candidates)
                started = time.perf_counter()
            else:
                candidates = iter_candidates(segment)
            valid_in_segment = 0
            for _match, normalized in candidates:
                if normalized in invalid_codes:
                    invalid_similar_codes += 1
                    continue
                if normalized in unique_codes:
                    valid_in_segment += 1
                    total_occurrences += 1
                    continue
                if not is_valid_candidate(normalized):
                    invalid_codes.add(normalized)
                    invalid_similar_codes += 1
                    continue
                valid_in_segment += 1
                total_occurrences += 1
                unique_codes.add(normalized)
                prefix = normalized.rsplit("-", 1)[0]
                if prefix not in seen_prefixes:
                    prefixes.append(prefix)
                    seen_prefixes.add(prefix)
            if metrics is not None:
                metrics.deduplication_seconds += time.perf_counter() - started
                metrics.valid_occurrences += valid_in_segment

            if progress_callback is not None:
                progress_callback(
                    HTMLRestorationProgress(
                        HTMLRestorationStage.ANALYZING,
                        segment.processed_bytes,
                        segment.total_bytes,
                        total_occurrences,
                    )
                )
                if metrics is not None:
                    metrics.progress_updates += 1
    except (HTMLRestorationCancelled, HTMLRestorationError):
        raise

    found_codes = 0
    missing_codes = 0
    codes = tuple(unique_codes)
    try:
        vault_metrics = metrics.vault if metrics is not None else None
        with repository.read_session(vault_metrics) as session:
            for batch in _chunks(
                codes, BALANCED_SETTINGS.html_lookup_window_codes
            ):
                if should_cancel is not None and should_cancel():
                    raise HTMLRestorationCancelled(
                        "A análise de HTML foi cancelada."
                    )
                if vault_metrics is not None:
                    vault_metrics.cache_misses += len(batch)
                fetched = session.get_many(batch)
                found_codes += len(fetched)
                missing_codes += len(batch) - len(fetched)
    except HTMLRestorationCancelled:
        raise
    except Exception as error:
        raise HTMLRestorationSecurityError(
            "Não foi possível recuperar um ou mais mapeamentos com segurança."
        ) from error

    return HTMLAnalysisResult(
        unique_codes=len(unique_codes),
        total_occurrences=total_occurrences,
        found_codes=found_codes,
        missing_codes=missing_codes,
        invalid_similar_codes=invalid_similar_codes,
        prefixes=tuple(prefixes),
    )


def _chunks(values: tuple[str, ...], size: int) -> Iterator[tuple[str, ...]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]
