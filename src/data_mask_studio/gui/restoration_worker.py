from PySide6.QtCore import QThread, Signal
from dataclasses import dataclass, field
from pathlib import Path

from data_mask_studio.restoration import (
    RestorationCancelled,
    RestorationConfiguration,
    RestorationService,
)
from data_mask_studio.performance import BALANCED_SETTINGS, ProgressLimiter
from data_mask_studio.restoration.csv_restorer import restore_csv_from_package
from data_mask_studio.restoration import RestorationError


@dataclass(frozen=True)
class PackageRestorationRequest:
    path: Path
    password: str = field(repr=False)


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
        package_request: PackageRestorationRequest | None = None,
    ) -> None:
        super().__init__()
        self._service = service
        self._configuration = configuration
        self._destination = destination
        self._overwrite = overwrite
        self._package_request = package_request

    def run(self) -> None:
        limiter = ProgressLimiter(BALANCED_SETTINGS)
        last_progress = None

        def report(progress) -> None:
            nonlocal last_progress
            last_progress = progress
            if limiter.should_emit(progress.rows_processed):
                self.progress.emit(progress)

        try:
            if self._package_request is not None:
                result = restore_csv_from_package(
                    self._configuration, self._destination,
                    self._package_request.path, self._package_request.password,
                    overwrite=self._overwrite, progress_callback=report,
                    should_cancel=self._cancel_requested.is_set,
                )
            else:
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
            if self._package_request is not None:
                # Never retain backend tracebacks or secrets in queued Qt signals.
                self.failed.emit(RestorationError(
                    "Não foi possível restaurar pelo pacote com segurança. "
                    "Verifique o pacote, a senha, o CSV vinculado e o destino."
                ))
            else:
                self.failed.emit(error)
        else:
            if last_progress is not None and limiter.should_emit(
                last_progress.rows_processed, force=True
            ):
                self.progress.emit(last_progress)
            self.completed.emit(result)
        finally:
            self._package_request = None
