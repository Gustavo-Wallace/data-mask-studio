from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QRect
from PySide6.QtGui import QCloseEvent, QGuiApplication, QScreen, QShowEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QStackedWidget,
    QTableWidget,
    QWidget,
)

from data_mask_studio.backup import EnvironmentPaths, default_environment_paths
from data_mask_studio.gui.anonymization_widget import AnonymizationWidget
from data_mask_studio.gui.about_dialog import AboutDialog
from data_mask_studio.gui.backup_widget import BackupWidget
from data_mask_studio.gui.batch_widget import BatchWidget
from data_mask_studio.gui.batch_restoration_widget import BatchRestorationWidget
from data_mask_studio.gui.consultant_widget import ConsultantWidget
from data_mask_studio.gui.html_restoration_widget import HTMLRestorationWidget
from data_mask_studio.gui.integrity_widget import IntegrityWidget
from data_mask_studio.gui.components import (
    NavigationItem,
    PageShell,
    SidebarNavigation,
    configure_path_field,
    configure_result_area,
    configure_table,
    set_button_role,
)
from data_mask_studio.gui.maintenance_widget import MaintenanceWidget
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
    initialize_existing_vault,
)


class MainWindow(QMainWindow):
    """Compõe as áreas da aplicação e coordena seus eventos gerais."""

    _PREFERRED_WIDTH = 1280
    _PREFERRED_HEIGHT = 820
    _MINIMUM_WIDTH = 960
    _MINIMUM_HEIGHT = 640

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
        self._initial_screen_fit_pending = True
        self._screen_change_connected = False

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

        # Uma migração pendente precisa terminar antes de qualquer aba obter
        # uma visão somente leitura do cofre ou iniciar outra operação.
        initialize_existing_vault(
            self._backup_paths.vault_database_path,
            self._vault_key_provider,
        )

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
        self.batch_restoration_widget = BatchRestorationWidget(
            self._restoration_repository_factory,
            self._prepare_batch_restoration,
        )
        self.batch_restoration_widget.busy_changed.connect(
            self._batch_restoration_busy_changed
        )
        self.maintenance_widget = MaintenanceWidget(
            self._backup_paths,
            self._key_provider,
            self._vault_key_provider,
            self._prepare_maintenance,
            self._maintenance_directories,
        )
        self.maintenance_widget.busy_changed.connect(self._maintenance_busy_changed)
        self.maintenance_widget.environment_changed.connect(
            self._environment_maintained
        )
        self.integrity_widget.audit_completed.connect(
            self.maintenance_widget.set_last_audit
        )

        page_specs = (
            ("PROCESSAMENTO", "Anonimizar CSV", "Defina como cada coluna será tratada no CSV de saída: preservar, mascarar ou excluir.", self.anonymization_widget),
            ("PROCESSAMENTO", "Anonimização em lote", "Processe vários arquivos CSV com um perfil de configuração salvo.", self.batch_widget),
            ("RESTAURAÇÃO", "Restaurar CSV", "Recupere valores de colunas selecionadas usando o cofre local.", self.restoration_widget),
            ("RESTAURAÇÃO", "Restaurar HTML", "Recupere códigos presentes em HTML e dashboards locais.", self.html_restoration_widget),
            ("RESTAURAÇÃO", "Restauração em lote", "Restaure vários arquivos CSV ou HTML usando o cofre local.", self.batch_restoration_widget),
            ("COFRE", "Consultar cofre", "Consulte códigos específicos sem expor todo o conteúdo do cofre.", self.consultant_widget),
            ("COFRE", "Backup e recuperação", "Crie ou restaure um backup criptografado do ambiente local.", self.backup_widget),
            ("COFRE", "Integridade", "Verifique as chaves, o cofre e os perfis sem modificar os dados.", self.integrity_widget),
            ("COFRE", "Cofre e manutenção", "Diagnostique, limpe temporários e compacte o cofre com segurança.", self.maintenance_widget),
        )
        self.page_widgets = tuple(spec[3] for spec in page_specs)
        self.page_stack = QStackedWidget()
        self.page_stack.setObjectName("mainPageStack")
        self.page_shells: list[PageShell] = []
        navigation_items = []
        for group, title, description, widget in page_specs:
            shell = PageShell(title, description, widget)
            self.page_shells.append(shell)
            self.page_stack.addWidget(shell)
            navigation_items.append(NavigationItem(group, title, description))
        self.navigation = SidebarNavigation(tuple(navigation_items))
        self.navigation.current_changed.connect(self._page_changed)
        self.navigation.about_requested.connect(self.show_about_dialog)
        self.about_dialog: AboutDialog | None = None

        central = QWidget()
        central.setObjectName("mainWorkspace")
        central_layout = QHBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self.navigation)
        central_layout.addWidget(self.page_stack, stretch=1)
        self.setCentralWidget(central)
        self._configure_presentation()
        self._fit_to_available_geometry()
        self.navigation.buttons[0].setFocus()

    def _available_screen_geometry(self, screen: QScreen | None = None) -> QRect:
        resolved_screen = screen or self.screen() or QGuiApplication.primaryScreen()
        return (
            resolved_screen.availableGeometry()
            if resolved_screen is not None
            else QRect()
        )

    def _fit_to_available_geometry(self, screen: QScreen | None = None) -> None:
        """Limita e centraliza a janela normal na área útil do monitor."""
        available = self._available_screen_geometry(screen)
        if not available.isValid() or available.isEmpty():
            self.setMinimumSize(self._MINIMUM_WIDTH, self._MINIMUM_HEIGHT)
            self.resize(self._PREFERRED_WIDTH, self._PREFERRED_HEIGHT)
            return

        frame_width = max(0, self.frameGeometry().width() - self.geometry().width())
        frame_height = max(0, self.frameGeometry().height() - self.geometry().height())
        usable_width = max(1, available.width() - frame_width)
        usable_height = max(1, available.height() - frame_height)

        self.setMinimumSize(
            min(self._MINIMUM_WIDTH, usable_width),
            min(self._MINIMUM_HEIGHT, usable_height),
        )
        self.resize(
            min(self._PREFERRED_WIDTH, usable_width),
            min(self._PREFERRED_HEIGHT, usable_height),
        )

        outer_width = self.width() + frame_width
        outer_height = self.height() + frame_height
        self.move(
            available.x() + max(0, (available.width() - outer_width) // 2),
            available.y() + max(0, (available.height() - outer_height) // 2),
        )

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        window_handle = self.windowHandle()
        if window_handle is not None and not self._screen_change_connected:
            window_handle.screenChanged.connect(self._screen_changed)
            self._screen_change_connected = True
        if self._initial_screen_fit_pending:
            self._initial_screen_fit_pending = False
            if self.isMaximized():
                self._limit_minimum_to_available_geometry(self.screen())
            else:
                self._fit_to_available_geometry(self.screen())

    def _limit_minimum_to_available_geometry(self, screen: QScreen) -> None:
        available = self._available_screen_geometry(screen)
        if available.isValid() and not available.isEmpty():
            self.setMinimumSize(
                min(self._MINIMUM_WIDTH, available.width()),
                min(self._MINIMUM_HEIGHT, available.height()),
            )

    def _screen_changed(self, screen: QScreen) -> None:
        if self.isMaximized():
            self._limit_minimum_to_available_geometry(screen)
        else:
            self._fit_to_available_geometry(screen)

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

    def _page_changed(self, index: int) -> None:
        self.page_stack.setCurrentIndex(index)
        if index == 1:
            self.batch_widget.refresh_profiles()

    def show_about_dialog(self) -> None:
        if self.about_dialog is None:
            self.about_dialog = AboutDialog(self)
        self.about_dialog.show()
        self.about_dialog.raise_()
        self.about_dialog.activateWindow()

    def set_current_page(self, index: int) -> None:
        self.navigation.set_current_index(index)

    def current_page_index(self) -> int:
        return self.page_stack.currentIndex()

    def page_index(self, widget: QWidget) -> int:
        return self.page_widgets.index(widget)

    def _configure_presentation(self) -> None:
        for button in (
            self.anonymization_widget.generate_button,
            self.batch_widget.start_button,
            self.restoration_widget.generate_button,
            self.html_restoration_widget.generate_button,
            self.batch_restoration_widget.start_button,
            self.consultant_widget.consult_button,
            self.backup_widget.create_button,
            self.integrity_widget.run_button,
            self.maintenance_widget.refresh_button,
        ):
            set_button_role(button, "primary")
        for button in (
            self.anonymization_widget.delete_profile_button,
            self.maintenance_widget.cleanup_button,
        ):
            set_button_role(button, "destructive")
        set_button_role(self.backup_widget.restore_button, "attention")
        path_fields: tuple[tuple[QLineEdit, str], ...] = (
            (self.anonymization_widget.path_field, "Caminho do CSV selecionado"),
            (self.batch_widget.output_field, "Pasta de saída da anonimização em lote"),
            (self.restoration_widget.path_field, "Caminho do CSV anonimizado"),
            (self.html_restoration_widget.path_field, "Caminho do HTML anonimizado"),
            (self.batch_restoration_widget.output_field, "Pasta de saída da restauração em lote"),
            (self.backup_widget.destination_field, "Destino do novo backup"),
            (self.backup_widget.restore_file_field, "Arquivo de backup selecionado"),
            (self.maintenance_widget.backup_path_field, "Arquivo de backup para validação"),
        )
        for field, accessible_name in path_fields:
            configure_path_field(field, accessible_name)
        for table in self.findChildren(QTableWidget):
            configure_table(table)
        for result_area, empty_height in (
            (self.batch_widget.summary_output, 125),
            (self.restoration_widget.summary, 125),
            (self.html_restoration_widget.summary, 220),
            (self.batch_restoration_widget.summary_output, 110),
            (self.consultant_widget.results_output, 240),
            (self.backup_widget.restore_summary, 115),
            (self.integrity_widget.report_view, 240),
            (self.maintenance_widget.overview_output, 280),
            (self.maintenance_widget.backup_result, 220),
            (self.maintenance_widget.compaction_result, 220),
        ):
            configure_result_area(result_area, empty_height)

    def _prepare_restore(self) -> bool:
        if (
            self.anonymization_widget.has_running_worker()
            or self.batch_widget.has_running_workers()
            or self.restoration_widget.has_running_worker()
            or self.html_restoration_widget.has_running_worker()
            or self.integrity_widget.has_running_worker()
            or self.batch_restoration_widget.has_running_workers()
            or self.maintenance_widget.has_running_worker()
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
            or self.batch_restoration_widget.has_running_workers()
            or self.maintenance_widget.has_running_worker()
        ):
            return False
        self.consultant_widget.clear_consultation()
        return True

    def _prepare_batch_restoration(self) -> bool:
        if (
            self.anonymization_widget.has_running_worker()
            or self.batch_widget.has_running_workers()
            or self.restoration_widget.has_running_worker()
            or self.html_restoration_widget.has_running_worker()
            or self.backup_widget.has_running_worker()
            or self.integrity_widget.has_running_worker()
            or self.maintenance_widget.has_running_worker()
        ):
            return False
        self.consultant_widget.clear_consultation()
        return True

    def _prepare_maintenance(self) -> bool:
        if (
            self.anonymization_widget.has_running_worker()
            or self.batch_widget.has_running_workers()
            or self.restoration_widget.has_running_worker()
            or self.html_restoration_widget.has_running_worker()
            or self.backup_widget.has_running_worker()
            or self.integrity_widget.has_running_worker()
            or self.batch_restoration_widget.has_running_workers()
        ):
            return False
        self.consultant_widget.clear_consultation()
        return True

    def _backup_busy_changed(self, busy: bool) -> None:
        self._set_exclusive_page_busy(self.backup_widget, busy)

    def _integrity_busy_changed(self, busy: bool) -> None:
        self._set_exclusive_page_busy(self.integrity_widget, busy)

    def _batch_restoration_busy_changed(self, busy: bool) -> None:
        self._set_exclusive_page_busy(self.batch_restoration_widget, busy)

    def _maintenance_busy_changed(self, busy: bool) -> None:
        self._set_exclusive_page_busy(self.maintenance_widget, busy)

    def _set_exclusive_page_busy(self, widget: QWidget, busy: bool) -> None:
        active_index = self.page_index(widget)
        for index in range(len(self.page_widgets)):
            if index != active_index:
                self.navigation.set_page_enabled(index, not busy)

    def _maintenance_directories(self) -> tuple[Path, ...]:
        paths: list[Path] = []
        for widget, attribute in (
            (self.anonymization_widget, "_last_output_path"),
            (self.restoration_widget, "_last_output_path"),
            (self.html_restoration_widget, "_last_output_path"),
            (self.batch_widget, "_output_directory"),
            (self.batch_restoration_widget, "_output_directory"),
            (self.backup_widget, "_last_backup_path"),
        ):
            value = getattr(widget, attribute, None)
            if value is not None:
                path = value if getattr(value, "is_dir", lambda: False)() else value.parent
                if path not in paths:
                    paths.append(path)
        return tuple(paths)

    def _environment_restored(self) -> None:
        self.anonymization_widget.clear_selection(
            status="Ambiente local restaurado com sucesso."
        )
        self.consultant_widget.clear_consultation()
        self.anonymization_widget.refresh_profiles()
        self.integrity_widget.clear_report()
        self.batch_restoration_widget.invalidate_analysis()

    def _environment_maintained(self) -> None:
        self.consultant_widget.clear_consultation()
        self.integrity_widget.clear_report()
        self.batch_restoration_widget.invalidate_analysis()

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
            (
                self.batch_restoration_widget.stop_workers,
                "Aguarde o cancelamento da restauração em lote.",
            ),
            (
                self.maintenance_widget.stop_worker,
                "Aguarde a conclusão segura da manutenção.",
            ),
        )
        for stop_worker, message in workers:
            if not stop_worker():
                event.ignore()
                self.anonymization_widget._set_status(message, is_error=False)
                return
        self.consultant_widget.clear_consultation()
        super().closeEvent(event)
