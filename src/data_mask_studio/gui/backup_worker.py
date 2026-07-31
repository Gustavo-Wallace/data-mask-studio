from PySide6.QtCore import QThread, Signal

from data_mask_studio.backup import (
    BackupCancelled,
    CancellationRequest,
    EnvironmentPaths,
    create_backup,
    restore_backup,
    validate_backup,
)
from data_mask_studio.security import DataProtector, KeyProvider


class _CancellableWorker(QThread):
    progress = Signal(object)
    cancelled = Signal()
    failed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._cancellation = CancellationRequest()

    def request_cancel(self) -> None:
        self._cancellation.request()


class BackupCreationWorker(_CancellableWorker):
    completed = Signal(object)

    def __init__(
        self,
        destination: str,
        password: str,
        confirmation: str,
        paths: EnvironmentPaths,
        hmac_key_provider: KeyProvider,
        vault_key_provider: KeyProvider,
        *,
        overwrite: bool,
    ) -> None:
        super().__init__()
        self._destination = destination
        self._password = password
        self._confirmation = confirmation
        self._paths = paths
        self._hmac_key_provider = hmac_key_provider
        self._vault_key_provider = vault_key_provider
        self._overwrite = overwrite

    def run(self) -> None:
        try:
            result = create_backup(
                self._destination,
                self._password,
                self._confirmation,
                paths=self._paths,
                hmac_key_provider=self._hmac_key_provider,
                vault_key_provider=self._vault_key_provider,
                overwrite=self._overwrite,
                cancellation=self._cancellation,
                progress_callback=self.progress.emit,
            )
        except BackupCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(error)
        else:
            self.completed.emit(result)
        finally:
            self._password = ""
            self._confirmation = ""


class BackupValidationWorker(_CancellableWorker):
    completed = Signal(object)

    def __init__(self, backup_path: str, password: str) -> None:
        super().__init__()
        self._backup_path = backup_path
        self._password = password

    def run(self) -> None:
        try:
            result = validate_backup(
                self._backup_path,
                self._password,
                cancellation=self._cancellation,
            )
        except BackupCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(error)
        else:
            self.completed.emit(result)
        finally:
            self._password = ""


class BackupRestoreWorker(_CancellableWorker):
    completed = Signal(object)

    def __init__(
        self,
        backup_path: str,
        password: str,
        paths: EnvironmentPaths,
        protector: DataProtector,
    ) -> None:
        super().__init__()
        self._backup_path = backup_path
        self._password = password
        self._paths = paths
        self._protector = protector

    def run(self) -> None:
        try:
            result = restore_backup(
                self._backup_path,
                self._password,
                paths=self._paths,
                protector=self._protector,
                cancellation=self._cancellation,
            )
        except BackupCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(error)
        else:
            self.completed.emit(result)
        finally:
            self._password = ""
