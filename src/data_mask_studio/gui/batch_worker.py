from collections.abc import Callable

from PySide6.QtCore import QThread, Signal

from data_mask_studio.batch import (
    BatchFile,
    BatchService,
    CancellationRequest,
)
from data_mask_studio.profiles import ConfigurationProfile
from data_mask_studio.performance import BALANCED_SETTINGS, ProgressLimiter
from data_mask_studio.security import KeyProvider
from data_mask_studio.vault import VaultRepository

VaultRepositoryFactory = Callable[[], VaultRepository]


class BatchValidationWorker(QThread):
    file_validated = Signal(object)
    completed = Signal()
    failed = Signal(object)

    def __init__(
        self,
        service: BatchService,
        files: list[BatchFile],
        profile: ConfigurationProfile,
    ) -> None:
        super().__init__()
        self._service = service
        self._files = files
        self._profile = profile
        self._cancellation = CancellationRequest()

    def request_cancel(self) -> None:
        self._cancellation.request()

    def run(self) -> None:
        try:
            self._service.validate(
                self._files,
                self._profile,
                cancellation=self._cancellation,
                file_callback=self.file_validated.emit,
            )
        except Exception as error:
            self.failed.emit(error)
        else:
            self.completed.emit()


class BatchProcessingWorker(QThread):
    file_changed = Signal(object)
    progress = Signal(object)
    completed = Signal(object)
    failed = Signal(object)

    def __init__(
        self,
        service: BatchService,
        files: list[BatchFile],
        profile: ConfigurationProfile,
        output_directory: str,
        key_provider: KeyProvider,
        vault_repository_factory: VaultRepositoryFactory,
    ) -> None:
        super().__init__()
        self._service = service
        self._files = files
        self._profile = profile
        self._output_directory = output_directory
        self._key_provider = key_provider
        self._vault_repository_factory = vault_repository_factory
        self._cancellation = CancellationRequest()

    def request_cancel(self) -> None:
        self._cancellation.request()

    def run(self) -> None:
        limiter = ProgressLimiter(BALANCED_SETTINGS)

        def report(progress) -> None:
            if limiter.should_emit(progress.records_processed):
                self.progress.emit(progress)

        try:
            summary = self._service.process(
                self._files,
                self._profile,
                self._output_directory,
                self._key_provider,
                self._vault_repository_factory,
                cancellation=self._cancellation,
                file_callback=self.file_changed.emit,
                progress_callback=report,
            )
        except Exception as error:
            self.failed.emit(error)
        else:
            self.completed.emit(summary)
