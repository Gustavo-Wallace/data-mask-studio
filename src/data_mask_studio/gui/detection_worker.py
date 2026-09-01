from threading import Event

from PySide6.QtCore import QThread, Signal

from data_mask_studio.csv_tools import CSVInspectionResult
from data_mask_studio.detection import DetectionCancelled, analyze_csv_columns


class DetectionWorker(QThread):
    """Executa a amostragem limitada fora da thread da interface."""

    completed = Signal(object)
    cancelled = Signal()
    failed = Signal(object)

    def __init__(
        self,
        inspection: CSVInspectionResult,
        *,
        row_limit: int = 100,
    ) -> None:
        super().__init__()
        self._inspection = CSVInspectionResult(
            path=inspection.path,
            encoding=inspection.encoding,
            delimiter=inspection.delimiter,
            headers=list(inspection.headers),
            header_replacements=inspection.header_replacements,
        )
        self._row_limit = row_limit
        self._cancel_requested = Event()

    def request_cancel(self) -> None:
        self._cancel_requested.set()

    def run(self) -> None:
        try:
            result = analyze_csv_columns(
                self._inspection,
                row_limit=self._row_limit,
                should_cancel=self._cancel_requested.is_set,
            )
        except DetectionCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(error)
        else:
            self.completed.emit(result)
