from collections.abc import Callable

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
)
from data_mask_studio.vault import VaultRepository
from data_mask_studio.vault.models import DecryptedVaultMapping

ProgressCallback = Callable[[HTMLRestorationProgress], None]


def analyze_html(
    inspection: HTMLInspectionResult,
    repository: VaultRepository,
    *,
    progress_callback: ProgressCallback | None = None,
    should_cancel: CancellationCheck | None = None,
) -> HTMLAnalysisResult:
    repository = repository.as_read_only()
    occurrences: dict[str, int] = {}
    invalid_similar_codes = 0
    prefixes: list[str] = []
    seen_prefixes: set[str] = set()

    try:
        for segment in iter_html_segments(
            inspection.path,
            inspection.encoding,
            should_cancel=should_cancel,
        ):
            for _match, normalized in iter_candidates(segment):
                if not is_valid_candidate(normalized):
                    invalid_similar_codes += 1
                    continue
                occurrences[normalized] = occurrences.get(normalized, 0) + 1
                prefix = normalized.rsplit("-", 1)[0]
                if prefix not in seen_prefixes:
                    prefixes.append(prefix)
                    seen_prefixes.add(prefix)
            if progress_callback is not None:
                progress_callback(
                    HTMLRestorationProgress(
                        HTMLRestorationStage.ANALYZING,
                        segment.processed_bytes,
                        segment.total_bytes,
                        sum(occurrences.values()),
                    )
                )
    except (HTMLRestorationCancelled, HTMLRestorationError):
        raise

    found_codes = 0
    missing_codes = 0
    for code in occurrences:
        if should_cancel is not None and should_cancel():
            raise HTMLRestorationCancelled("A análise de HTML foi cancelada.")
        mapping = _safe_lookup(repository, code)
        if mapping is None:
            missing_codes += 1
        else:
            found_codes += 1

    return HTMLAnalysisResult(
        unique_codes=len(occurrences),
        total_occurrences=sum(occurrences.values()),
        found_codes=found_codes,
        missing_codes=missing_codes,
        invalid_similar_codes=invalid_similar_codes,
        prefixes=tuple(prefixes),
    )


def _safe_lookup(
    repository: VaultRepository, code: str
) -> DecryptedVaultMapping | None:
    try:
        return repository.get_decrypted_mapping(code)
    except Exception as error:
        raise HTMLRestorationSecurityError(
            "Não foi possível recuperar um ou mais mapeamentos com segurança."
        ) from error
