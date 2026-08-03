from threading import Event

from PySide6.QtCore import QThread, Signal

from data_mask_studio.batch_restoration import (
    BatchRestorationFile,
    BatchRestorationOptions,
    BatchRestorationService,
)


class _BatchRestorationWorker(QThread):
    file_changed = Signal(object)
    failed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._cancellation = Event()

    def request_cancel(self) -> None:
        self._cancellation.set()


class BatchRestorationAnalysisWorker(_BatchRestorationWorker):
    progress = Signal(int, int)
    completed = Signal()
    cancelled = Signal()

    def __init__(
        self,
        service: BatchRestorationService,
        files: list[BatchRestorationFile],
    ) -> None:
        super().__init__()
        self._service = service
        self._files = files

    def run(self) -> None:
        processed = 0

        def changed(item: BatchRestorationFile) -> None:
            nonlocal processed
            processed += 1
            self.file_changed.emit(item)
            self.progress.emit(processed, len(self._files))

        try:
            cancelled = self._service.analyze_files(
                self._files,
                file_callback=changed,
                should_cancel=self._cancellation.is_set,
            )
        except Exception as error:
            self.failed.emit(error)
        else:
            if cancelled:
                self.cancelled.emit()
                return
            self.completed.emit()


class BatchRestorationProcessingWorker(_BatchRestorationWorker):
    progress = Signal(object)
    completed = Signal(object)

    def __init__(
        self,
        service: BatchRestorationService,
        files: list[BatchRestorationFile],
        output_directory: str,
        options: BatchRestorationOptions,
    ) -> None:
        super().__init__()
        self._service = service
        self._files = files
        self._output_directory = output_directory
        self._options = options

    def run(self) -> None:
        try:
            summary = self._service.restore_files(
                self._files,
                self._output_directory,
                self._options,
                file_callback=self.file_changed.emit,
                progress_callback=self.progress.emit,
                should_cancel=self._cancellation.is_set,
            )
        except Exception as error:
            self.failed.emit(error)
        else:
            self.completed.emit(summary)
