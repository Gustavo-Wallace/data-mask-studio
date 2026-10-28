from collections.abc import Callable
from pathlib import Path
from data_mask_studio.environment_restore import recover_restore

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
from data_mask_studio.gui.file_restoration_widget import FileRestorationWidget
from data_mask_studio.gui.components import (
    NavigationItem,
    PageShell,
    SidebarNavigation,
    configure_path_field,
    configure_table,
    set_button_role,
)
from data_mask_studio.gui.maintenance_widget import MaintenanceWidget
from data_mask_studio.gui.settings_widget import SettingsWidget
from data_mask_studio.i18n import Translator
from data_mask_studio.preferences import PreferencesService
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
        preferences_service: PreferencesService | None = None,
        translator: Translator | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Data Mask Studio")
        # Localization activation remains explicit until all workflows migrate.
        # Neither saved preferences nor the Windows UI language selects it yet.
        self._translator = translator or Translator("pt-BR")
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
        recover_restore(self._backup_paths.directory, self._data_protector)

        # Uma migração pendente precisa terminar antes de qualquer aba obter
        # uma visão somente leitura do cofre ou iniciar outra operação.
        initialize_existing_vault(
            self._backup_paths.vault_database_path,
            self._vault_key_provider,
            self._key_provider,
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
            translator=self._translator,
        )
        self.batch_widget = BatchWidget(
            self._profile_service,
            self._key_provider,
            self._vault_repository_factory,
            translator=self._translator,
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
            translator=self._translator,
        )
        self.backup_widget.busy_changed.connect(self._backup_busy_changed)
        self.backup_widget.environment_restored.connect(self._environment_restored)
        self.consultant_widget = ConsultantWidget(
            self._vault_repository_factory, translator=self._translator,
        )
        self.file_restoration_widget = FileRestorationWidget(
            self._restoration_repository_factory, translator=self._translator,
        )
        # Keep existing internal access to each independent workflow. Neither
        # child is registered as a standalone navigation destination anymore.
        self.restoration_widget = self.file_restoration_widget.csv_widget
        self.html_restoration_widget = self.file_restoration_widget.html_widget
        self.batch_restoration_widget = BatchRestorationWidget(
            self._restoration_repository_factory,
            self._prepare_batch_restoration,
            translator=self._translator,
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
            translator=self._translator,
        )
        self.maintenance_widget.busy_changed.connect(self._maintenance_busy_changed)
        self.anonymization_widget.profiles_changed.connect(
            self.maintenance_widget.invalidate_diagnostic
        )
        self.anonymization_widget.environment_may_change.connect(
            self.maintenance_widget.invalidate_diagnostic
        )
        self.batch_widget.environment_may_change.connect(
            self.maintenance_widget.invalidate_diagnostic
        )
        self.maintenance_widget.environment_changed.connect(
            self._environment_maintained
        )

        self.settings_widget = SettingsWidget(
            preferences_service, translator=self._translator,
        )
        page_specs = (
            ("navigation.group.processing", "navigation.mask_file", "navigation.description.mask_file", self.anonymization_widget),
            ("navigation.group.processing", "navigation.batch_masking", "navigation.description.batch_masking", self.batch_widget),
            ("navigation.group.restoration", "navigation.restore_file", "navigation.description.restore_file", self.file_restoration_widget),
            ("navigation.group.restoration", "navigation.batch_restoration", "navigation.description.batch_restoration", self.batch_restoration_widget),
            ("navigation.group.vault", "navigation.browse_vault", "navigation.description.browse_vault", self.consultant_widget),
            ("navigation.group.vault", "navigation.backup", "navigation.description.backup", self.backup_widget),
            ("navigation.group.vault", "navigation.maintenance", "navigation.description.maintenance", self.maintenance_widget),
            (None, "common.settings", "settings.description", self.settings_widget),
        )
        self.page_widgets = tuple(spec[3] for spec in page_specs)
        self.page_stack = QStackedWidget()
        self.page_stack.setObjectName("mainPageStack")
        self.page_shells: list[PageShell] = []
        navigation_items = []
        for group_id, title_id, description_id, widget in page_specs:
            title = self._translator.text(title_id)
            description = self._translator.text(description_id)
            shell = PageShell(
                title, description, widget, translator=self._translator,
                title_message_id=title_id, description_message_id=description_id,
            )
            self.page_shells.append(shell)
            self.page_stack.addWidget(shell)
            navigation_items.append(NavigationItem(
                self._translator.text(group_id) if group_id else "",
                title, description, footer=widget is self.settings_widget,
                group_message_id=group_id, title_message_id=title_id,
                description_message_id=description_id,
            ))
        self.navigation = SidebarNavigation(
            tuple(navigation_items), translator=self._translator,
        )
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
            self.about_dialog = AboutDialog(self, translator=self._translator)
        self.about_dialog.show()
        self.about_dialog.raise_()
        self.about_dialog.activateWindow()

    def retranslate(self, translator: Translator) -> None:
        """Explicitly refresh migrated presentation only, preserving live state."""
        self._translator = translator
        self.navigation.retranslate(translator)
        for shell in self.page_shells:
            shell.retranslate(translator)
        self.settings_widget.retranslate(translator)
        self.anonymization_widget.retranslate(translator)
        self.batch_widget.retranslate(translator)
        self.file_restoration_widget.retranslate(translator)
        self.batch_restoration_widget.retranslate(translator)
        self.consultant_widget.retranslate(translator)
        self.backup_widget.retranslate(translator)
        self.maintenance_widget.retranslate(translator)
        if self.about_dialog is not None:
            self.about_dialog.retranslate(translator)

    def set_current_page(self, index: int) -> None:
        self.navigation.set_current_index(index)

    def current_page_index(self) -> int:
        return self.page_stack.currentIndex()

    def page_index(self, widget: QWidget) -> int:
        if widget in (self.restoration_widget, self.html_restoration_widget):
            widget = self.file_restoration_widget
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
            (self.anonymization_widget.path_field, self._translator.text("masking.path_accessible")),
            (self.batch_widget.output_field, self._translator.text("batch.output_accessible")),
            (self.restoration_widget.path_field, self._translator.text("restoration.csv.path_accessible")),
            (self.html_restoration_widget.path_field, self._translator.text("restoration.html.path_accessible")),
            (self.batch_restoration_widget.output_field, self._translator.text("batch_restoration.output_accessible")),
            (self.backup_widget.destination_field, self._translator.text("backup.destination_accessible")),
            (self.backup_widget.restore_file_field, self._translator.text("backup.file_accessible")),
            (self.maintenance_widget.backup_path_field, self._translator.text("maintenance.backup.path_accessible")),
        )
        for field, accessible_name in path_fields:
            configure_path_field(field, accessible_name)
        for table in self.findChildren(QTableWidget):
            configure_table(table)

    def _prepare_restore(self) -> bool:
        if (
            self.anonymization_widget.has_running_worker()
            or self.batch_widget.has_running_workers()
            or self.restoration_widget.has_running_worker()
            or self.html_restoration_widget.has_running_worker()
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
            or self.batch_restoration_widget.has_running_workers()
        ):
            return False
        self.consultant_widget.clear_consultation()
        return True

    def _backup_busy_changed(self, busy: bool) -> None:
        self._set_exclusive_page_busy(self.backup_widget, busy)

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
        self.anonymization_widget.clear_selection()
        self.anonymization_widget._set_status_id("shell.environment_restored")
        self.consultant_widget.clear_consultation()
        self.anonymization_widget.refresh_profiles()
        self.maintenance_widget.invalidate_diagnostic()
        self.batch_restoration_widget.invalidate_analysis()

    def _environment_maintained(self) -> None:
        self.consultant_widget.clear_consultation()
        self.maintenance_widget.invalidate_diagnostic()
        self.batch_restoration_widget.invalidate_analysis()

    def closeEvent(self, event: QCloseEvent) -> None:
        workers = (
            (
                self.anonymization_widget.stop_worker,
                "shell.close.masking",
            ),
            (
                self.batch_widget.stop_workers,
                "shell.close.batch",
            ),
            (
                self.backup_widget.stop_worker,
                "shell.close.backup",
            ),
            (
                self.restoration_widget.stop_worker,
                "shell.close.csv",
            ),
            (
                self.html_restoration_widget.stop_worker,
                "shell.close.html",
            ),
            (
                self.batch_restoration_widget.stop_workers,
                "shell.close.batch_restoration",
            ),
            (
                self.maintenance_widget.stop_worker,
                "shell.close.maintenance",
            ),
        )
        for stop_worker, message_id in workers:
            if not stop_worker():
                event.ignore()
                self.anonymization_widget._set_status_id(message_id)
                return
        self.consultant_widget.clear_consultation()
        super().closeEvent(event)
