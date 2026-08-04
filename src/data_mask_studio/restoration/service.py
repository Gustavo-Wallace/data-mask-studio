from collections.abc import Callable
from pathlib import Path

from data_mask_studio.performance import RestorationMetrics
from data_mask_studio.restoration.analyzer import analyze_csv
from data_mask_studio.restoration.csv_restorer import restore_csv
from data_mask_studio.restoration.models import (
    AnalysisResult,
    RestorationConfiguration,
    RestorationProgress,
    RestorationResult,
)
from data_mask_studio.vault import VaultRepository

RepositoryFactory = Callable[[], VaultRepository]
ProgressCallback = Callable[[RestorationProgress], None]
CancellationCheck = Callable[[], bool]


class RestorationService:
    def __init__(self, repository_factory: RepositoryFactory) -> None:
        self._repository_factory = repository_factory

    def analyze(
        self,
        configuration: RestorationConfiguration,
        *,
        progress_callback: ProgressCallback | None = None,
        should_cancel: CancellationCheck | None = None,
        metrics: RestorationMetrics | None = None,
    ) -> AnalysisResult:
        repository = self._repository_factory().as_read_only()
        return analyze_csv(
            configuration,
            repository,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
            metrics=metrics,
        )

    def restore(
        self,
        configuration: RestorationConfiguration,
        destination_path: str | Path,
        *,
        overwrite: bool = False,
        progress_callback: ProgressCallback | None = None,
        should_cancel: CancellationCheck | None = None,
        metrics: RestorationMetrics | None = None,
    ) -> RestorationResult:
        repository = self._repository_factory().as_read_only()
        return restore_csv(
            configuration,
            destination_path,
            repository,
            overwrite=overwrite,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
            metrics=metrics,
        )
