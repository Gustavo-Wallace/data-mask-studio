from threading import Event
from data_mask_studio.environment import guarded, provider_directory
from collections.abc import Callable

from PySide6.QtCore import QThread, Signal

from data_mask_studio.anonymization.models import (
    AnonymizationResult,
    ColumnConfig,
)
from data_mask_studio.csv_tools.csv_anonymizer import (
    ProcessingCancelled,
    anonymize_csv,
)
from data_mask_studio.csv_tools.models import CSVInspectionResult
from data_mask_studio.performance import BALANCED_SETTINGS, ProgressLimiter
from data_mask_studio.processing.models import ProcessingPlan
from data_mask_studio.security import KeyProvider
from data_mask_studio.vault import VaultRepository, create_default_vault_repository

VaultRepositoryFactory = Callable[[], VaultRepository]


class AnonymizationWorker(QThread):
    """Executa a geração do CSV sem bloquear a thread da interface."""

    progress = Signal(int)
    completed = Signal(object)
    cancelled = Signal()
    failed = Signal(object)

    def __init__(
        self,
        inspection: CSVInspectionResult,
        output_path: str,
        configurations: list[ColumnConfig],
        key_provider: KeyProvider,
        vault_repository_factory: VaultRepositoryFactory = create_default_vault_repository,
        *,
        overwrite: bool,
        processing_plan: ProcessingPlan | None = None,
    ) -> None:
        super().__init__()
        self._inspection = inspection
        self._processing_plan = processing_plan
        self._output_path = output_path
        self._configurations = [
            ColumnConfig(
                item.header,
                item.anonymize,
                item.prefix,
                item.normalization_rule,
                action=item.action,
                output_name=item.output_name,
            )
            for item in configurations
        ]
        self._key_provider = key_provider
        self._vault_repository_factory = vault_repository_factory
        self._overwrite = overwrite
        self._cancel_requested = Event()

    def request_cancel(self) -> None:
        self._cancel_requested.set()

    def run(self) -> None:
        try:
            self._run_leased()
        except Exception as error:
            self.failed.emit(error)

    @guarded(lambda self: provider_directory(self._key_provider)
             if self._processing_plan is None or self._processing_plan.requires_masking else None)
    def _run_leased(self) -> None:
        try:
            limiter = ProgressLimiter(BALANCED_SETTINGS)

            def report(records: int) -> None:
                if limiter.should_emit(records):
                    self.progress.emit(records)

            needs_masking = self._processing_plan is None or self._processing_plan.requires_masking
            secret_key = self._key_provider.get_key() if needs_masking else None
            vault_repository = self._vault_repository_factory() if needs_masking else None
            result = anonymize_csv(
                self._inspection.path,
                self._output_path,
                encoding=self._inspection.encoding,
                delimiter=self._inspection.delimiter,
                configurations=self._configurations,
                processing_plan=self._processing_plan,
                secret_key=secret_key,
                overwrite=self._overwrite,
                progress_callback=report,
                should_cancel=self._cancel_requested.is_set,
                vault_repository=vault_repository,
            )
        except ProcessingCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(error)
        else:
            if limiter.should_emit(result.records_processed, force=True):
                self.progress.emit(result.records_processed)
            self.completed.emit(result)
