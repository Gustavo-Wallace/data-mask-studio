from threading import Event

from PySide6.QtCore import QThread, Signal

from data_mask_studio.maintenance import (
    MaintenanceCancelled,
    MaintenanceDiagnostics,
    TemporaryItem,
    VaultCompactor,
    cleanup_temporaries,
    locate_temporaries,
)


class _MaintenanceWorker(QThread):
    failed = Signal(object)
    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._cancellation = Event()

    def request_cancel(self) -> None:
        self._cancellation.set()


class DiagnosticWorker(_MaintenanceWorker):
    progress = Signal(int, int)
    completed = Signal(object)

    def __init__(self, diagnostics: MaintenanceDiagnostics) -> None:
        super().__init__()
        self._diagnostics = diagnostics

    def run(self) -> None:
        try:
            result = self._diagnostics.run(
                should_cancel=self._cancellation.is_set,
                progress_callback=self.progress.emit,
            )
        except MaintenanceCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(error)
        else:
            self.completed.emit(result)


class TemporaryScanWorker(_MaintenanceWorker):
    completed = Signal(object)

    def __init__(self, application_directory, session_directories) -> None:
        super().__init__()
        self._application_directory = application_directory
        self._session_directories = session_directories

    def run(self) -> None:
        try:
            result = locate_temporaries(
                self._application_directory, self._session_directories
            )
        except Exception as error:
            self.failed.emit(error)
        else:
            self.completed.emit(result)


class TemporaryCleanupWorker(_MaintenanceWorker):
    completed = Signal(object)

    def __init__(
        self,
        items: list[TemporaryItem],
        application_directory,
        session_directories,
    ) -> None:
        super().__init__()
        self._items = items
        self._application_directory = application_directory
        self._session_directories = session_directories

    def run(self) -> None:
        try:
            result = cleanup_temporaries(
                self._items,
                self._application_directory,
                self._session_directories,
            )
        except Exception as error:
            self.failed.emit(error)
        else:
            self.completed.emit(result)


class CompactionWorker(_MaintenanceWorker):
    phase_changed = Signal(str, bool)
    completed = Signal(object)

    def __init__(self, compactor: VaultCompactor) -> None:
        super().__init__()
        self._compactor = compactor

    def run(self) -> None:
        try:
            result = self._compactor.compact(
                should_cancel=self._cancellation.is_set,
                progress_callback=self.phase_changed.emit,
            )
        except MaintenanceCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(error)
        else:
            self.completed.emit(result)
