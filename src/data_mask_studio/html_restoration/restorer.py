import os
import tempfile
import time
from collections.abc import Callable, MutableMapping
from pathlib import Path

from data_mask_studio.csv_tools.csv_anonymizer import paths_refer_to_same_file
from data_mask_studio.html_restoration.exceptions import (
    HTMLMissingCodeError,
    HTMLRestorationCancelled,
    HTMLRestorationError,
    HTMLRestorationSecurityError,
)
from data_mask_studio.html_restoration.inspector import python_encoding
from data_mask_studio.html_restoration.models import (
    HTMLInspectionResult,
    HTMLMissingCodePolicy,
    HTMLRestorationProgress,
    HTMLRestorationResult,
    HTMLRestorationStage,
)
from data_mask_studio.html_restoration.scanner import (
    CancellationCheck,
    HTMLSegment,
    is_valid_candidate,
    iter_candidates,
    iter_html_segments,
    iter_timed_html_segments,
    replace_candidates,
)
from data_mask_studio.performance import (
    BALANCED_SETTINGS,
    BoundedCache,
    HTMLProcessingMetrics,
    RestorationMetrics,
)
from data_mask_studio.restoration import RepresentationPolicy
from data_mask_studio.vault import VaultRepository
from data_mask_studio.vault.models import DecryptedVaultMapping

ProgressCallback = Callable[[HTMLRestorationProgress], None]


def restore_html(
    inspection: HTMLInspectionResult,
    destination_path: str | Path,
    repository: VaultRepository,
    *,
    missing_code_policy: HTMLMissingCodePolicy = HTMLMissingCodePolicy.KEEP,
    representation_policy: RepresentationPolicy = RepresentationPolicy.FIRST_ORIGINAL,
    overwrite: bool = False,
    progress_callback: ProgressCallback | None = None,
    should_cancel: CancellationCheck | None = None,
    metrics: HTMLProcessingMetrics | None = None,
) -> HTMLRestorationResult:
    source = inspection.path.expanduser().absolute()
    destination = Path(destination_path).expanduser().absolute()
    _validate_destination(source, destination, overwrite)
    repository = repository.as_read_only()
    cache: BoundedCache[str, DecryptedVaultMapping | None] = BoundedCache(
        BALANCED_SETTINGS.restoration_cache_limit
    )
    temporary_path: Path | None = None
    total_occurrences = restored_occurrences = missing_occurrences = 0
    started_at = time.perf_counter()

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=python_encoding(inspection.encoding),
            newline="",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
            buffering=BALANCED_SETTINGS.io_buffer_size,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            segments = iter_html_segments(
                source,
                inspection.encoding,
                should_cancel=should_cancel,
            )
            vault_metrics = metrics.vault if metrics is not None else None
            with repository.read_session(vault_metrics) as session:
                for segment in iter_timed_html_segments(segments, metrics):
                    _raise_if_cancelled(should_cancel)
                    started = time.perf_counter()
                    cached_replacement = (
                        _replace_from_cache(
                            segment,
                            cache.snapshot(),
                            missing_code_policy,
                            representation_policy,
                        )
                        if cache
                        and len(cache) <= BALANCED_SETTINGS.html_direct_cache_limit
                        else None
                    )
                    if cached_replacement is not None:
                        (
                            restored_segment,
                            segment_candidates,
                            segment_occurrences,
                            segment_restored,
                            segment_missing,
                        ) = cached_replacement
                        total_occurrences += segment_occurrences
                        restored_occurrences += segment_restored
                        missing_occurrences += segment_missing
                        if metrics is not None:
                            metrics.candidates_scanned += segment_candidates
                            metrics.valid_occurrences += segment_occurrences
                            metrics.substitution_seconds += (
                                time.perf_counter() - started
                            )
                            vault_metrics.cache_hits += segment_occurrences
                        started = time.perf_counter()
                        temporary_file.write(restored_segment)
                        if metrics is not None:
                            metrics.writing_seconds += (
                                time.perf_counter() - started
                            )
                        if progress_callback is not None:
                            progress_callback(
                                HTMLRestorationProgress(
                                    HTMLRestorationStage.RESTORING,
                                    segment.processed_bytes,
                                    segment.total_bytes,
                                    total_occurrences,
                                    restored_occurrences,
                                    missing_occurrences,
                                )
                            )
                            if metrics is not None:
                                metrics.progress_updates += 1
                        continue

                    started = time.perf_counter()
                    candidates = list(iter_candidates(segment))
                    if metrics is not None:
                        metrics.token_scanning_seconds += (
                            time.perf_counter() - started
                        )
                        metrics.candidates_scanned += len(candidates)
                    codes = [
                        normalized
                        for _match, normalized in candidates
                        if is_valid_candidate(normalized)
                    ]
                    if metrics is not None:
                        metrics.valid_occurrences += len(codes)
                    resolved = _bulk_lookup(session, codes, cache, vault_metrics)

                    def replacement(original: str, normalized: str) -> str:
                        nonlocal total_occurrences
                        nonlocal restored_occurrences
                        nonlocal missing_occurrences
                        if normalized not in resolved:
                            return original
                        total_occurrences += 1
                        mapping = resolved[normalized]
                        if mapping is None:
                            missing_occurrences += 1
                            if missing_code_policy is HTMLMissingCodePolicy.ABORT:
                                raise HTMLMissingCodeError(
                                    "A restauração foi interrompida porque um código não "
                                    "foi encontrado no cofre."
                                )
                            return original
                        restored_occurrences += 1
                        if representation_policy is RepresentationPolicy.CANONICAL:
                            return mapping.canonical_value
                        return mapping.original_value

                    started = time.perf_counter()
                    restored_segment = replace_candidates(
                        segment, replacement, candidates
                    )
                    if metrics is not None:
                        metrics.substitution_seconds += (
                            time.perf_counter() - started
                        )
                    started = time.perf_counter()
                    temporary_file.write(restored_segment)
                    if metrics is not None:
                        metrics.writing_seconds += time.perf_counter() - started
                    if progress_callback is not None:
                        progress_callback(
                            HTMLRestorationProgress(
                                HTMLRestorationStage.RESTORING,
                                segment.processed_bytes,
                                segment.total_bytes,
                                total_occurrences,
                                restored_occurrences,
                                missing_occurrences,
                            )
                        )
                        if metrics is not None:
                            metrics.progress_updates += 1
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
            _raise_if_cancelled(should_cancel)

        if destination.exists() and not overwrite:
            raise HTMLRestorationError("O arquivo de destino já existe.")
        os.replace(temporary_path, destination)
        temporary_path = None
    except (
        HTMLMissingCodeError,
        HTMLRestorationCancelled,
        HTMLRestorationSecurityError,
    ):
        raise
    except HTMLRestorationError:
        raise
    except UnicodeEncodeError as error:
        raise HTMLRestorationError(
            "A codificação original não representa um dos valores restaurados."
        ) from error
    except OSError as error:
        raise HTMLRestorationError(
            "Não foi possível gerar o arquivo HTML restaurado."
        ) from error
    except Exception as error:
        raise HTMLRestorationSecurityError(
            "Não foi possível recuperar um ou mais mapeamentos com segurança."
        ) from error
    finally:
        if temporary_path is not None and temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError:
                pass

    return HTMLRestorationResult(
        output_path=destination,
        encoding=inspection.encoding,
        total_occurrences=total_occurrences,
        restored_occurrences=restored_occurrences,
        missing_occurrences=missing_occurrences,
        duration_seconds=time.perf_counter() - started_at,
        missing_code_policy=missing_code_policy,
        representation_policy=representation_policy,
    )


def suggested_html_output_path(source_path: str | Path) -> Path:
    source = Path(source_path)
    stem = source.stem
    extension = (
        source.suffix
        if source.suffix.lower() in {".html", ".htm"}
        else ".html"
    )
    suffix = "_anonimizado"
    if stem.lower().endswith(suffix):
        stem = stem[: -len(suffix)]
    return source.with_name(f"{stem}_restaurado{extension}")


def _validate_destination(source: Path, destination: Path, overwrite: bool) -> None:
    if paths_refer_to_same_file(source, destination):
        raise HTMLRestorationError(
            "O arquivo restaurado não pode substituir o HTML de entrada."
        )
    if not source.is_file():
        raise HTMLRestorationError("O arquivo HTML selecionado não existe.")
    if not destination.parent.is_dir():
        raise HTMLRestorationError("A pasta escolhida para o arquivo não existe.")
    if destination.exists() and not overwrite:
        raise HTMLRestorationError("O arquivo de destino já existe.")


def _bulk_lookup(
    session,
    codes: list[str],
    cache: MutableMapping[str, DecryptedVaultMapping | None],
    metrics: RestorationMetrics | None,
) -> dict[str, DecryptedVaultMapping | None]:
    resolved: dict[str, DecryptedVaultMapping | None] = {}
    missing: list[str] = []
    missing_seen: set[str] = set()
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
        raise HTMLRestorationSecurityError(
            "Não foi possível recuperar um ou mais mapeamentos com segurança."
        ) from error
    for code in missing:
        mapping = fetched.get(code)
        resolved[code] = mapping
        cache[code] = mapping
    return resolved


def _replace_from_cache(
    segment: HTMLSegment,
    available: dict[str, DecryptedVaultMapping | None],
    missing_code_policy: HTMLMissingCodePolicy,
    representation_policy: RepresentationPolicy,
) -> tuple[str, int, int, int, int] | None:
    candidates = occurrences = restored = missing = 0

    def replacement(original: str, normalized: str) -> str:
        nonlocal candidates, occurrences, restored, missing
        candidates += 1
        if normalized not in available:
            if is_valid_candidate(normalized):
                raise _CacheMiss
            return original
        occurrences += 1
        mapping = available[normalized]
        if mapping is None:
            missing += 1
            if missing_code_policy is HTMLMissingCodePolicy.ABORT:
                raise HTMLMissingCodeError(
                    "A restauração foi interrompida porque um código não "
                    "foi encontrado no cofre."
                )
            return original
        restored += 1
        return (
            mapping.canonical_value
            if representation_policy is RepresentationPolicy.CANONICAL
            else mapping.original_value
        )

    try:
        output = replace_candidates(segment, replacement)
    except _CacheMiss:
        return None
    return output, candidates, occurrences, restored, missing


class _CacheMiss(Exception):
    pass


def _raise_if_cancelled(should_cancel: CancellationCheck | None) -> None:
    if should_cancel is not None and should_cancel():
        raise HTMLRestorationCancelled(
            "A restauração de HTML foi cancelada."
        )
