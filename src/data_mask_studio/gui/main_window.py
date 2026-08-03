from collections.abc import Callable

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMainWindow, QTabWidget

from data_mask_studio.backup import EnvironmentPaths, default_environment_paths
from data_mask_studio.gui.anonymization_widget import AnonymizationWidget
from data_mask_studio.gui.backup_widget import BackupWidget
from data_mask_studio.gui.batch_widget import BatchWidget
from data_mask_studio.gui.consultant_widget import ConsultantWidget
from data_mask_studio.gui.html_restoration_widget import HTMLRestorationWidget
from data_mask_studio.gui.integrity_widget import IntegrityWidget
from data_mask_studio.gui.restoration_widget import RestorationWidget
from data_mask_studio.profiles import ProfileError, ProfileRepository, ProfileService
from data_mask_studio.security import (
    DataProtector,
    KeyProvider,
    LocalKeyProvider,
    WindowsDPAPIProtector,
)
from data_mask_studio.vault import (
    VaultKeyProvider,
    VaultRepository,
    create_default_read_only_vault_repository,
    create_default_vault_repository,
)


class MainWindow(QMainWindow):
    """Compõe as áreas da aplicação e coordena seus eventos gerais."""

    def __init__(
        self,
        key_provider: KeyProvider | None = None,
        vault_repository_factory: Callable[[], VaultRepository] | None = None,
        profile_service: ProfileService | None = None,
        backup_paths: EnvironmentPaths | None = None,
        vault_key_provider: KeyProvider | None = None,
        data_protector: DataProtector | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Data Mask Studio")
        self.resize(900, 600)

        self._key_provider = key_provider or LocalKeyProvider()
        self._vault_repository_factory = (
            vault_repository_factory or create_default_vault_repository
        )
        self._restoration_repository_factory = (
            create_default_read_only_vault_repository
            if vault_repository_factory is None
            else lambda: vault_repository_factory().as_read_only()
        )
        self._backup_paths = backup_paths or default_environment_paths()
        self._vault_key_provider = vault_key_provider or VaultKeyProvider(
            self._backup_paths.directory
        )
        self._data_protector = data_protector or WindowsDPAPIProtector()

        resolved_profile_service = profile_service
        profile_initialization_error: str | None = None
        if resolved_profile_service is None:
            try:
                resolved_profile_service = ProfileService(ProfileRepository())
            except ProfileError as error:
                profile_initialization_error = str(error)
        self._profile_service = resolved_profile_service

        self.anonymization_widget = AnonymizationWidget(
            self._key_provider,
            self._vault_repository_factory,
            self._profile_service,
            profile_initialization_error,
        )
        self.batch_widget = BatchWidget(
            self._profile_service,
            self._key_provider,
            self._vault_repository_factory,
        )
        self.anonymization_widget.profiles_changed.connect(
            self.batch_widget.refresh_profiles
        )
        self.backup_widget = BackupWidget(
            self._backup_paths,
            self._key_provider,
            self._vault_key_provider,
            self._data_protector,
            self._prepare_restore,
        )
        self.backup_widget.busy_changed.connect(self._backup_busy_changed)
        self.backup_widget.environment_restored.connect(self._environment_restored)
        self.consultant_widget = ConsultantWidget(self._vault_repository_factory)
        self.restoration_widget = RestorationWidget(
            self._restoration_repository_factory
        )
        self.html_restoration_widget = HTMLRestorationWidget(
            self._restoration_repository_factory
        )
        self.integrity_widget = IntegrityWidget(
            self._backup_paths,
            self._key_provider,
            self._vault_key_provider,
            self._prepare_integrity_audit,
        )
        self.integrity_widget.busy_changed.connect(self._integrity_busy_changed)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.anonymization_widget, "Anonimizar CSV")
        self.tabs.addTab(self.batch_widget, "Anonimização em lote")
        self.tabs.addTab(self.restoration_widget, "Restaurar CSV")
        self.tabs.addTab(self.html_restoration_widget, "Restaurar HTML")
        self.tabs.addTab(self.consultant_widget, "Consultar cofre")
        self.tabs.addTab(self.backup_widget, "Backup e recuperação")
        self.tabs.addTab(self.integrity_widget, "Integridade")
        self.tabs.currentChanged.connect(self._tab_changed)
        self.setCentralWidget(self.tabs)

    def __getattr__(self, name: str) -> object:
        """Mantém a API visual legada delegando-a à aba individual."""
        try:
            widget = object.__getattribute__(self, "anonymization_widget")
        except AttributeError:
            raise AttributeError(name) from None
        try:
            return getattr(widget, name)
        except AttributeError:
            raise AttributeError(name) from None

    def _tab_changed(self, index: int) -> None:
        if index == 1:
            self.batch_widget.refresh_profiles()

    def _prepare_restore(self) -> bool:
        if (
            self.anonymization_widget.has_running_worker()
            or self.batch_widget.has_running_workers()
            or self.restoration_widget.has_running_worker()
            or self.html_restoration_widget.has_running_worker()
            or self.integrity_widget.has_running_worker()
        ):
            return False
        self.consultant_widget.clear_consultation()
        return True

    def _prepare_integrity_audit(self) -> bool:
        if (
            self.anonymization_widget.has_running_worker()
            or self.batch_widget.has_running_workers()
            or self.restoration_widget.has_running_worker()
            or self.html_restoration_widget.has_running_worker()
            or self.backup_widget.has_running_worker()
        ):
            return False
        self.consultant_widget.clear_consultation()
        return True

    def _backup_busy_changed(self, busy: bool) -> None:
        backup_index = self.tabs.indexOf(self.backup_widget)
        for index in range(self.tabs.count()):
            if index != backup_index:
                self.tabs.setTabEnabled(index, not busy)

    def _integrity_busy_changed(self, busy: bool) -> None:
        integrity_index = self.tabs.indexOf(self.integrity_widget)
        for index in range(self.tabs.count()):
            if index != integrity_index:
                self.tabs.setTabEnabled(index, not busy)

    def _environment_restored(self) -> None:
        self.anonymization_widget.clear_selection(
            status="Ambiente local restaurado com sucesso."
        )
        self.consultant_widget.clear_consultation()
        self.anonymization_widget.refresh_profiles()
        self.integrity_widget.clear_report()

    def closeEvent(self, event: QCloseEvent) -> None:
        workers = (
            (
                self.anonymization_widget.stop_worker,
                "Aguarde o cancelamento do processamento antes de fechar.",
            ),
            (
                self.batch_widget.stop_workers,
                "Aguarde o cancelamento do lote antes de fechar.",
            ),
            (
                self.backup_widget.stop_worker,
                "Aguarde a conclusão segura do backup ou restauração.",
            ),
            (
                self.restoration_widget.stop_worker,
                "Aguarde o cancelamento da restauração de CSV.",
            ),
            (
                self.html_restoration_widget.stop_worker,
                "Aguarde o cancelamento da restauração de HTML.",
            ),
            (
                self.integrity_widget.stop_worker,
                "Aguarde o cancelamento da auditoria de integridade.",
            ),
        )
        for stop_worker, message in workers:
            if not stop_worker():
                event.ignore()
                self.anonymization_widget._set_status(message, is_error=False)
                return
        self.consultant_widget.clear_consultation()
        super().closeEvent(event)
