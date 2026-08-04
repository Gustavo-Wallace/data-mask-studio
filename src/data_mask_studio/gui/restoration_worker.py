from PySide6.QtCore import QThread, Signal

from data_mask_studio.restoration import (
    RestorationCancelled,
    RestorationConfiguration,
    RestorationService,
)
from data_mask_studio.performance import BALANCED_SETTINGS, ProgressLimiter


class _RestorationWorker(QThread):
    progress = Signal(object)
    cancelled = Signal()
    failed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        from threading import Event

        self._cancel_requested = Event()

    def request_cancel(self) -> None:
        self._cancel_requested.set()


class RestorationAnalysisWorker(_RestorationWorker):
    completed = Signal(object)

    def __init__(
        self,
        service: RestorationService,
        configuration: RestorationConfiguration,
    ) -> None:
        super().__init__()
        self._service = service
        self._configuration = configuration

    def run(self) -> None:
        limiter = ProgressLimiter(BALANCED_SETTINGS)
        last_progress = None

        def report(progress) -> None:
            nonlocal last_progress
            last_progress = progress
            if limiter.should_emit(progress.rows_processed):
                self.progress.emit(progress)

        try:
            result = self._service.analyze(
                self._configuration,
                progress_callback=report,
                should_cancel=self._cancel_requested.is_set,
            )
        except RestorationCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(error)
        else:
            if last_progress is not None and limiter.should_emit(
                last_progress.rows_processed, force=True
            ):
                self.progress.emit(last_progress)
            self.completed.emit(result)


class CSVRestorationWorker(_RestorationWorker):
    completed = Signal(object)

    def __init__(
        self,
        service: RestorationService,
        configuration: RestorationConfiguration,
        destination: str,
        *,
        overwrite: bool,
    ) -> None:
        super().__init__()
        self._service = service
        self._configuration = configuration
        self._destination = destination
        self._overwrite = overwrite

    def run(self) -> None:
        limiter = ProgressLimiter(BALANCED_SETTINGS)
        last_progress = None

        def report(progress) -> None:
            nonlocal last_progress
            last_progress = progress
            if limiter.should_emit(progress.rows_processed):
                self.progress.emit(progress)

        try:
            result = self._service.restore(
                self._configuration,
                self._destination,
                overwrite=self._overwrite,
                progress_callback=report,
                should_cancel=self._cancel_requested.is_set,
            )
        except RestorationCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(error)
        else:
            if last_progress is not None and limiter.should_emit(
                last_progress.rows_processed, force=True
            ):
                self.progress.emit(last_progress)
            self.completed.emit(result)
