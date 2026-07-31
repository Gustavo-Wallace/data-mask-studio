import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from data_mask_studio.csv_tools.csv_anonymizer import paths_refer_to_same_file
from data_mask_studio.html_restoration.analyzer import _safe_lookup
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
    is_valid_candidate,
    iter_html_segments,
    replace_candidates,
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
) -> HTMLRestorationResult:
    source = inspection.path.expanduser().absolute()
    destination = Path(destination_path).expanduser().absolute()
    _validate_destination(source, destination, overwrite)
    repository = repository.as_read_only()
    cache: dict[str, DecryptedVaultMapping | None] = {}
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
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)

            def replacement(original: str, normalized: str) -> str:
                nonlocal total_occurrences, restored_occurrences, missing_occurrences
                if not is_valid_candidate(normalized):
                    return original
                total_occurrences += 1
                if normalized not in cache:
                    cache[normalized] = _safe_lookup(repository, normalized)
                mapping = cache[normalized]
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

            for segment in iter_html_segments(
                source,
                inspection.encoding,
                should_cancel=should_cancel,
            ):
                temporary_file.write(replace_candidates(segment, replacement))
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
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
            if should_cancel is not None and should_cancel():
                raise HTMLRestorationCancelled(
                    "A restauração de HTML foi cancelada."
                )

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
