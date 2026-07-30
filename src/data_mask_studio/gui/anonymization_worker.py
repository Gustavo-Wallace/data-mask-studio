from threading import Event

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
from data_mask_studio.security import KeyProvider


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
        *,
        overwrite: bool,
    ) -> None:
        super().__init__()
        self._inspection = inspection
        self._output_path = output_path
        self._configurations = [
            ColumnConfig(item.header, item.anonymize, item.prefix)
            for item in configurations
        ]
        self._key_provider = key_provider
        self._overwrite = overwrite
        self._cancel_requested = Event()

    def request_cancel(self) -> None:
        self._cancel_requested.set()

    def run(self) -> None:
        try:
            secret_key = self._key_provider.get_key()
            result = anonymize_csv(
                self._inspection.path,
                self._output_path,
                encoding=self._inspection.encoding,
                delimiter=self._inspection.delimiter,
                configurations=self._configurations,
                secret_key=secret_key,
                overwrite=self._overwrite,
                progress_callback=self.progress.emit,
                should_cancel=self._cancel_requested.is_set,
            )
        except ProcessingCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(error)
        else:
            self.completed.emit(result)

