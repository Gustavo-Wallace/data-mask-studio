from collections.abc import Callable
from pathlib import Path

from data_mask_studio.html_restoration.analyzer import analyze_html
from data_mask_studio.html_restoration.models import (
    HTMLAnalysisResult,
    HTMLInspectionResult,
    HTMLMissingCodePolicy,
    HTMLRestorationProgress,
    HTMLRestorationResult,
)
from data_mask_studio.html_restoration.restorer import restore_html
from data_mask_studio.html_restoration.scanner import CancellationCheck
from data_mask_studio.performance import HTMLProcessingMetrics
from data_mask_studio.restoration import RepresentationPolicy
from data_mask_studio.vault import VaultRepository

RepositoryFactory = Callable[[], VaultRepository]
ProgressCallback = Callable[[HTMLRestorationProgress], None]


class HTMLRestorationService:
    def __init__(self, repository_factory: RepositoryFactory) -> None:
        self._repository_factory = repository_factory

    def analyze(
        self,
        inspection: HTMLInspectionResult,
        *,
        progress_callback: ProgressCallback | None = None,
        should_cancel: CancellationCheck | None = None,
        metrics: HTMLProcessingMetrics | None = None,
    ) -> HTMLAnalysisResult:
        return analyze_html(
            inspection,
            self._repository_factory().as_read_only(),
            progress_callback=progress_callback,
            should_cancel=should_cancel,
            metrics=metrics,
        )

    def restore(
        self,
        inspection: HTMLInspectionResult,
        destination_path: str | Path,
        *,
        missing_code_policy: HTMLMissingCodePolicy,
        representation_policy: RepresentationPolicy,
        overwrite: bool,
        progress_callback: ProgressCallback | None = None,
        should_cancel: CancellationCheck | None = None,
        metrics: HTMLProcessingMetrics | None = None,
    ) -> HTMLRestorationResult:
        return restore_html(
            inspection,
            destination_path,
            self._repository_factory().as_read_only(),
            missing_code_policy=missing_code_policy,
            representation_policy=representation_policy,
            overwrite=overwrite,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
            metrics=metrics,
        )
