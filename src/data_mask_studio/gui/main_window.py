from pathlib import Path
from collections.abc import Callable

from PySide6.QtCore import QSignalBlocker, Qt, QUrl
from PySide6.QtGui import QColor, QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from data_mask_studio.anonymization import (
    AnonymizationResult,
    ColumnConfig,
    create_column_configs,
    normalize_prefix,
    validate_configuration,
)
from data_mask_studio.csv_tools import CSVInspectionError, CSVInspectionResult, inspect_csv
from data_mask_studio.csv_tools.csv_anonymizer import (
    CSVAnonymizationError,
    paths_refer_to_same_file,
)
from data_mask_studio.gui.anonymization_worker import AnonymizationWorker
from data_mask_studio.gui.consultant_widget import ConsultantWidget
from data_mask_studio.normalization import (
    NORMALIZATION_OPTIONS,
    NormalizationRule,
)
from data_mask_studio.profiles import (
    ConfigurationProfile,
    ProfileError,
    ProfileColumn,
    ProfileRepository,
    ProfileService,
)
from data_mask_studio.security import KeyProvider, KeyProviderError, LocalKeyProvider
from data_mask_studio.vault import (
    VaultError,
    VaultRepository,
    create_default_vault_repository,
)

SEPARATOR_NAMES = {
    ",": "Vírgula (,)",
    ";": "Ponto e vírgula (;)",
    "\t": "Tabulação",
    "|": "Barra vertical (|)",
}


class MainWindow(QMainWindow):
    """Janela principal do Data Mask Studio."""

    def __init__(
        self,
        key_provider: KeyProvider | None = None,
        vault_repository_factory: Callable[[], VaultRepository] | None = None,
        profile_service: ProfileService | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Data Mask Studio")
        self.resize(900, 600)

        title = QLabel("Data Mask Studio")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 28px; font-weight: 600;")

        description = QLabel(
            "Selecione um arquivo CSV para visualizar os cabeçalhos encontrados."
        )
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        description.setWordWrap(True)
        description.setStyleSheet("font-size: 14px; color: #555;")

        self.select_button = QPushButton("Selecionar CSV")
        self.select_button.clicked.connect(self._select_csv)

        self.clear_button = QPushButton("Limpar seleção")
        self.clear_button.clicked.connect(self.clear_selection)
        self.clear_button.setEnabled(False)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.select_button)
        button_layout.addWidget(self.clear_button)
        button_layout.addStretch()

        self.path_field = QLineEdit()
        self.path_field.setReadOnly(True)
        self.path_field.setPlaceholderText("Nenhum arquivo selecionado")

        self.file_name_label = QLabel("—")
        self.encoding_label = QLabel("—")
        self.delimiter_label = QLabel("—")
        self.column_count_label = QLabel("—")

        details_layout = QFormLayout()
        details_layout.addRow("Arquivo:", self.file_name_label)
        details_layout.addRow("Caminho completo:", self.path_field)
        details_layout.addRow("Codificação:", self.encoding_label)
        details_layout.addRow("Separador:", self.delimiter_label)
        details_layout.addRow("Quantidade de colunas:", self.column_count_label)

        configuration_label = QLabel("Configuração das colunas")
        configuration_label.setStyleSheet("font-size: 16px; font-weight: 600;")

        profile_label = QLabel("Perfil de configuração")
        profile_label.setStyleSheet("font-size: 16px; font-weight: 600;")
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(180)
        self.apply_profile_button = QPushButton("Aplicar perfil")
        self.apply_profile_button.clicked.connect(self.apply_selected_profile)
        self.save_profile_button = QPushButton("Salvar como perfil")
        self.save_profile_button.clicked.connect(self.save_as_profile)
        self.update_profile_button = QPushButton("Atualizar perfil")
        self.update_profile_button.clicked.connect(self.update_selected_profile)
        self.rename_profile_button = QPushButton("Renomear")
        self.rename_profile_button.clicked.connect(self.rename_selected_profile)
        self.delete_profile_button = QPushButton("Excluir")
        self.delete_profile_button.clicked.connect(self.delete_selected_profile)
        self.profile_combo.currentIndexChanged.connect(self._update_profile_actions)

        profile_layout = QHBoxLayout()
        profile_layout.addWidget(self.profile_combo, stretch=1)
        profile_layout.addWidget(self.apply_profile_button)
        profile_layout.addWidget(self.save_profile_button)
        profile_layout.addWidget(self.update_profile_button)
        profile_layout.addWidget(self.rename_profile_button)
        profile_layout.addWidget(self.delete_profile_button)

        self.select_all_button = QPushButton("Selecionar todas")
        self.select_all_button.clicked.connect(self.select_all_columns)
        self.select_all_button.setEnabled(False)

        self.unselect_all_button = QPushButton("Desmarcar todas")
        self.unselect_all_button.clicked.connect(self.unselect_all_columns)
        self.unselect_all_button.setEnabled(False)

        self.selected_count_label = QLabel("0 colunas selecionadas")

        selection_layout = QHBoxLayout()
        selection_layout.addWidget(self.select_all_button)
        selection_layout.addWidget(self.unselect_all_button)
        selection_layout.addStretch()
        selection_layout.addWidget(self.selected_count_label)

        self.config_table = QTableWidget(0, 5)
        self.config_table.setHorizontalHeaderLabels(
            ["Anonimizar", "Cabeçalho", "Prefixo", "Normalização", "Status"]
        )
        self.config_table.setAlternatingRowColors(True)
        self.config_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.config_table.verticalHeader().setVisible(False)
        table_header = self.config_table.horizontalHeader()
        table_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        table_header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        table_header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)

        self.validate_button = QPushButton("Validar configuração")
        self.validate_button.clicked.connect(self.validate_current_configuration)
        self.validate_button.setEnabled(False)

        self.generate_button = QPushButton("Gerar CSV anonimizado")
        self.generate_button.clicked.connect(self._choose_output_file)
        self.generate_button.setEnabled(False)

        self.cancel_button = QPushButton("Cancelar")
        self.cancel_button.clicked.connect(self.cancel_processing)
        self.cancel_button.setVisible(False)

        action_layout = QHBoxLayout()
        action_layout.addWidget(self.validate_button)
        action_layout.addWidget(self.generate_button)
        action_layout.addWidget(self.cancel_button)
        action_layout.addStretch()

        self._column_configs: list[ColumnConfig] = []
        self._checkboxes: list[QCheckBox] = []
        self._prefix_fields: list[QLineEdit] = []
        self._normalization_fields: list[QComboBox] = []
        self._inspection_result: CSVInspectionResult | None = None
        self._configuration_validated = False
        self._configuration_dirty = False
        self._key_provider = key_provider or LocalKeyProvider()
        self._vault_repository_factory = (
            vault_repository_factory or create_default_vault_repository
        )
        self._profile_service: ProfileService | None = profile_service
        self._profile_initialization_error: str | None = None
        if self._profile_service is None:
            try:
                self._profile_service = ProfileService(ProfileRepository())
            except ProfileError as error:
                self._profile_initialization_error = str(error)
        self._profiles: list[ConfigurationProfile] = []
        self._worker: AnonymizationWorker | None = None
        self._last_output_path: Path | None = None
        self._last_processing_error: Exception | None = None

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.processed_count_label = QLabel("0 registros processados")
        self.processed_count_label.setVisible(False)

        progress_layout = QHBoxLayout()
        progress_layout.addWidget(self.progress_bar, stretch=1)
        progress_layout.addWidget(self.processed_count_label)

        self.output_path_label = QLabel()
        self.output_path_label.setWordWrap(True)
        self.output_path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.output_path_label.setVisible(False)

        self.open_folder_button = QPushButton("Abrir pasta do arquivo")
        self.open_folder_button.clicked.connect(self.open_output_folder)
        self.open_folder_button.setVisible(False)

        self.status_label = QLabel("Selecione um arquivo CSV para começar.")
        self.status_label.setWordWrap(True)

        layout = QVBoxLayout()
        layout.setContentsMargins(36, 24, 36, 24)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(description)
        layout.addSpacing(8)
        layout.addLayout(button_layout)
        layout.addLayout(details_layout)
        layout.addWidget(profile_label)
        layout.addLayout(profile_layout)
        layout.addWidget(configuration_label)
        layout.addLayout(selection_layout)
        layout.addWidget(self.config_table, stretch=1)
        layout.addLayout(action_layout)
        layout.addLayout(progress_layout)
        layout.addWidget(self.output_path_label)
        layout.addWidget(self.open_folder_button)
        layout.addWidget(self.status_label)

        anonymization_widget = QWidget()
        anonymization_widget.setLayout(layout)
        self.consultant_widget = ConsultantWidget(self._vault_repository_factory)
        self.tabs = QTabWidget()
        self.tabs.addTab(anonymization_widget, "Anonimizar CSV")
        self.tabs.addTab(self.consultant_widget, "Consultar cofre")
        self.setCentralWidget(self.tabs)
        self._refresh_profiles()

    def _select_csv(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Selecionar arquivo CSV",
            "",
            "Arquivos CSV (*.csv)",
        )
        if not file_path:
            self._set_status("Seleção de arquivo cancelada.", is_error=False)
            return
        self.load_csv(file_path)

    def load_csv(self, file_path: str) -> None:
        """Inspeciona o CSV selecionado e atualiza a interface."""
        try:
            result = inspect_csv(file_path)
        except CSVInspectionError as error:
            self._show_error(file_path, str(error))
            return

        self._show_result(result)

    def _show_result(self, result: CSVInspectionResult) -> None:
        self._inspection_result = result
        self._configuration_validated = False
        self._configuration_dirty = False
        self.generate_button.setEnabled(False)
        self._clear_output_result()
        self.file_name_label.setText(result.path.name)
        self.path_field.setText(str(result.path))
        self.encoding_label.setText(result.encoding)
        self.delimiter_label.setText(SEPARATOR_NAMES[result.delimiter])
        self.column_count_label.setText(str(len(result.headers)))
        self._build_configuration_table(result.headers)
        self.clear_button.setEnabled(True)
        self._update_profile_actions()
        self._set_status("Cabeçalhos lidos com sucesso.", is_error=False)

    def _show_error(self, file_path: str, message: str) -> None:
        selected_path = Path(file_path).expanduser().absolute()
        self._reset_details()
        self.file_name_label.setText(selected_path.name)
        self.path_field.setText(str(selected_path))
        self.clear_button.setEnabled(True)
        self._set_status(message, is_error=True)

    def clear_selection(
        self,
        *,
        status: str = "Seleção limpa.",
        is_error: bool = False,
    ) -> None:
        """Remove da interface todas as informações do arquivo selecionado."""
        self._reset_details()
        self._set_status(status, is_error=is_error)

    def _reset_details(self) -> None:
        self._inspection_result = None
        self._configuration_validated = False
        self._configuration_dirty = False
        self.generate_button.setEnabled(False)
        self._clear_output_result()
        self.file_name_label.setText("—")
        self.path_field.clear()
        self.encoding_label.setText("—")
        self.delimiter_label.setText("—")
        self.column_count_label.setText("—")
        self._clear_configuration_table()
        self.clear_button.setEnabled(False)
        self._update_profile_actions()

    def _build_configuration_table(self, headers: list[str]) -> None:
        self._clear_configuration_table()
        self._column_configs = create_column_configs(headers)
        self.config_table.setRowCount(len(headers))

        for row, configuration in enumerate(self._column_configs):
            checkbox = QCheckBox()
            checkbox.setToolTip(f"Anonimizar a coluna {configuration.header}")
            checkbox_container = QWidget()
            checkbox_layout = QHBoxLayout(checkbox_container)
            checkbox_layout.setContentsMargins(0, 0, 0, 0)
            checkbox_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            checkbox_layout.addWidget(checkbox)
            self.config_table.setCellWidget(row, 0, checkbox_container)

            header_item = QTableWidgetItem(configuration.header)
            header_item.setFlags(header_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.config_table.setItem(row, 1, header_item)

            prefix_field = QLineEdit()
            prefix_field.setEnabled(False)
            prefix_field.setMaxLength(24)
            prefix_field.setPlaceholderText("Marque a coluna para definir")
            self.config_table.setCellWidget(row, 2, prefix_field)

            normalization_field = QComboBox()
            for rule, label in NORMALIZATION_OPTIONS:
                normalization_field.addItem(label, rule.value)
            normalization_field.setCurrentIndex(0)
            normalization_field.setEnabled(False)
            self.config_table.setCellWidget(row, 3, normalization_field)

            status_item = QTableWidgetItem("Não selecionada")
            status_item.setFlags(status_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.config_table.setItem(row, 4, status_item)

            checkbox.toggled.connect(
                lambda checked, current_row=row: self._column_toggled(
                    current_row, checked
                )
            )
            prefix_field.textChanged.connect(
                lambda text, current_row=row: self._prefix_changed(current_row, text)
            )
            normalization_field.currentIndexChanged.connect(
                lambda _index, current_row=row: self._normalization_changed(
                    current_row
                )
            )
            self._checkboxes.append(checkbox)
            self._prefix_fields.append(prefix_field)
            self._normalization_fields.append(normalization_field)

        has_headers = bool(headers)
        self.select_all_button.setEnabled(has_headers)
        self.unselect_all_button.setEnabled(has_headers)
        self.validate_button.setEnabled(has_headers)
        self._update_selected_count()

    def _clear_configuration_table(self) -> None:
        self.config_table.setRowCount(0)
        self._column_configs = []
        self._checkboxes = []
        self._prefix_fields = []
        self._normalization_fields = []
        self.select_all_button.setEnabled(False)
        self.unselect_all_button.setEnabled(False)
        self.validate_button.setEnabled(False)
        self.selected_count_label.setText("0 colunas selecionadas")

    def _column_toggled(self, row: int, checked: bool) -> None:
        configuration = self._column_configs[row]
        prefix_field = self._prefix_fields[row]
        normalization_field = self._normalization_fields[row]
        configuration.anonymize = checked
        prefix_field.setEnabled(checked)
        normalization_field.setEnabled(checked)
        if checked and not prefix_field.text():
            prefix_field.setText(normalize_prefix(configuration.header))
        self._update_selected_count()
        self._refresh_row_statuses()
        self._configuration_changed()

    def _prefix_changed(self, row: int, text: str) -> None:
        self._column_configs[row].prefix = text
        self._refresh_row_statuses()
        self._configuration_changed()

    def _normalization_changed(self, row: int) -> None:
        value = self._normalization_fields[row].currentData()
        self._column_configs[row].normalization_rule = NormalizationRule(value)
        self._configuration_changed()

    def _configuration_changed(self) -> None:
        self._configuration_validated = False
        self._configuration_dirty = True
        self.generate_button.setEnabled(False)
        self._set_status(
            "Configuração alterada. Use “Validar configuração” para conferir.",
            is_error=False,
        )

    def select_all_columns(self) -> None:
        for checkbox in self._checkboxes:
            checkbox.setChecked(True)

    def unselect_all_columns(self) -> None:
        for checkbox in self._checkboxes:
            checkbox.setChecked(False)

    def _update_selected_count(self) -> None:
        selected_count = sum(
            configuration.anonymize for configuration in self._column_configs
        )
        self.selected_count_label.setText(
            f"{selected_count} de {len(self._column_configs)} colunas selecionadas"
        )

    def _refresh_row_statuses(self) -> None:
        result = validate_configuration(self._column_configs)
        for row, (configuration, row_result) in enumerate(
            zip(self._column_configs, result.column_results, strict=True)
        ):
            status_item = self.config_table.item(row, 4)
            prefix_field = self._prefix_fields[row]
            if not configuration.anonymize:
                status_item.setText("Não selecionada")
                status_item.setBackground(QColor("transparent"))
                prefix_field.setStyleSheet("")
            elif row_result.is_valid:
                status_item.setText("Válida")
                status_item.setBackground(QColor("#dcfce7"))
                prefix_field.setStyleSheet("")
            else:
                status_item.setText(row_result.error_message or "Inválida")
                status_item.setBackground(QColor("#fee2e2"))
                prefix_field.setStyleSheet("border: 1px solid #b42318;")

    def validate_current_configuration(self) -> None:
        result = validate_configuration(self._column_configs)
        self._refresh_row_statuses()
        if result.is_valid:
            self._configuration_validated = True
            self.generate_button.setEnabled(True)
            suffix = "coluna" if result.selected_count == 1 else "colunas"
            self._set_status(
                f"Configuração válida para {result.selected_count} {suffix}.",
                is_error=False,
            )
        else:
            self._configuration_validated = False
            self.generate_button.setEnabled(False)
            self._set_status(result.error_message or "Configuração inválida.", is_error=True)

    def _refresh_profiles(self, selected_identifier: str | None = None) -> None:
        if self._profile_service is None:
            self._profiles = []
            self.profile_combo.clear()
            self._update_profile_actions()
            self._set_status(
                self._profile_initialization_error
                or "Os perfis de configuração não estão disponíveis.",
                is_error=True,
            )
            return
        try:
            self._profiles = self._profile_service.list_profiles()
        except ProfileError as error:
            self._profiles = []
            self.profile_combo.clear()
            self._update_profile_actions()
            self._set_status(str(error), is_error=True)
            return

        blocker = QSignalBlocker(self.profile_combo)
        self.profile_combo.clear()
        for profile in self._profiles:
            self.profile_combo.addItem(profile.name, profile.identifier)
        if selected_identifier is not None:
            index = self.profile_combo.findData(selected_identifier)
            if index >= 0:
                self.profile_combo.setCurrentIndex(index)
        del blocker
        self._update_profile_actions()

    def _selected_profile(self) -> ConfigurationProfile | None:
        identifier = self.profile_combo.currentData()
        return next(
            (profile for profile in self._profiles if profile.identifier == identifier),
            None,
        )

    def _update_profile_actions(self, *_args: object) -> None:
        has_profile = self._selected_profile() is not None
        has_csv = self._inspection_result is not None
        has_service = self._profile_service is not None
        self.apply_profile_button.setEnabled(has_profile and has_csv)
        self.save_profile_button.setEnabled(has_service and has_csv)
        self.update_profile_button.setEnabled(has_profile and has_csv)
        self.rename_profile_button.setEnabled(has_profile)
        self.delete_profile_button.setEnabled(has_profile)

    def save_as_profile(self) -> None:
        if (
            self._profile_service is None
            or self._inspection_result is None
            or not self._configuration_validated
        ):
            self._set_status(
                "Selecione um CSV e valide a configuração antes de salvar o perfil.",
                is_error=True,
            )
            return
        name, accepted = QInputDialog.getText(
            self, "Salvar perfil", "Nome do perfil:"
        )
        if not accepted:
            return
        try:
            profile = self._profile_service.create(name, self._column_configs)
        except ProfileError as error:
            self._set_status(str(error), is_error=True)
            return
        self._refresh_profiles(profile.identifier)
        self._set_status(f"Perfil “{profile.name}” salvo com sucesso.", is_error=False)

    def update_selected_profile(self) -> None:
        profile = self._selected_profile()
        if (
            profile is None
            or self._profile_service is None
            or self._inspection_result is None
            or not self._configuration_validated
        ):
            self._set_status(
                "Selecione um perfil, um CSV e uma configuração válida.",
                is_error=True,
            )
            return
        answer = QMessageBox.question(
            self,
            "Atualizar perfil",
            f"Substituir a configuração do perfil “{profile.name}”?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            updated = self._profile_service.update(
                profile.identifier, self._column_configs
            )
        except ProfileError as error:
            self._set_status(str(error), is_error=True)
            return
        self._refresh_profiles(updated.identifier)
        self._set_status(f"Perfil “{updated.name}” atualizado.", is_error=False)

    def apply_selected_profile(self) -> None:
        profile = self._selected_profile()
        if (
            profile is None
            or self._profile_service is None
            or self._inspection_result is None
        ):
            self._set_status("Selecione um CSV e um perfil compatível.", is_error=True)
            return
        if self._configuration_dirty:
            answer = QMessageBox.question(
                self,
                "Substituir configuração",
                "A configuração atual foi alterada e será substituída. Continuar?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        application = self._profile_service.apply(
            profile, self._inspection_result.headers
        )
        if not application.has_matches:
            self._set_status(
                "O perfil não é compatível com o CSV selecionado.", is_error=True
            )
            return

        self._apply_profile_configurations(application.configurations)
        self._configuration_dirty = False
        if application.is_complete:
            self.validate_current_configuration()
            self._configuration_dirty = False
            self._set_status(f"Perfil “{profile.name}” aplicado.", is_error=False)
            return

        self._configuration_validated = False
        self.generate_button.setEnabled(False)
        missing = ", ".join(application.missing_headers)
        self._set_status(
            "O perfil foi aplicado parcialmente. "
            f"Cabeçalhos não encontrados: {missing}.",
            is_error=True,
        )

    def _apply_profile_configurations(
        self, configurations: tuple[ProfileColumn, ...]
    ) -> None:
        for row, profile_column in enumerate(configurations):
            configuration = self._column_configs[row]
            checkbox = self._checkboxes[row]
            prefix_field = self._prefix_fields[row]
            normalization_field = self._normalization_fields[row]
            blockers = (
                QSignalBlocker(checkbox),
                QSignalBlocker(prefix_field),
                QSignalBlocker(normalization_field),
            )
            configuration.anonymize = profile_column.anonymize
            configuration.prefix = profile_column.prefix
            configuration.normalization_rule = profile_column.normalization_rule
            checkbox.setChecked(profile_column.anonymize)
            prefix_field.setText(profile_column.prefix)
            prefix_field.setEnabled(profile_column.anonymize)
            normalization_field.setCurrentIndex(
                normalization_field.findData(profile_column.normalization_rule.value)
            )
            normalization_field.setEnabled(profile_column.anonymize)
            del blockers
        self._update_selected_count()
        self._refresh_row_statuses()

    def rename_selected_profile(self) -> None:
        profile = self._selected_profile()
        if profile is None or self._profile_service is None:
            return
        name, accepted = QInputDialog.getText(
            self,
            "Renomear perfil",
            "Novo nome:",
            text=profile.name,
        )
        if not accepted:
            return
        try:
            renamed = self._profile_service.rename(profile.identifier, name)
        except ProfileError as error:
            self._set_status(str(error), is_error=True)
            return
        self._refresh_profiles(renamed.identifier)
        self._set_status(f"Perfil renomeado para “{renamed.name}”.", is_error=False)

    def delete_selected_profile(self) -> None:
        profile = self._selected_profile()
        if profile is None or self._profile_service is None:
            return
        answer = QMessageBox.question(
            self,
            "Excluir perfil",
            f"Excluir somente o perfil “{profile.name}”?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._profile_service.delete(profile.identifier)
        except ProfileError as error:
            self._set_status(str(error), is_error=True)
            return
        self._refresh_profiles()
        self._set_status(f"Perfil “{profile.name}” excluído.", is_error=False)

    def _choose_output_file(self) -> None:
        if self._inspection_result is None or not self._configuration_validated:
            self._set_status("Valide a configuração antes de gerar o arquivo.", is_error=True)
            return
        if self._worker is not None and self._worker.isRunning():
            return

        source = self._inspection_result.path
        suggested_path = source.with_name(f"{source.stem}_anonimizado.csv")
        selected_path, _ = QFileDialog.getSaveFileName(
            self,
            "Salvar CSV anonimizado",
            str(suggested_path),
            "Arquivos CSV (*.csv)",
        )
        if not selected_path:
            self._set_status("Geração do arquivo cancelada.", is_error=False)
            return

        destination = Path(selected_path)
        if destination.suffix.lower() != ".csv":
            destination = destination.with_suffix(".csv")
        if paths_refer_to_same_file(source, destination):
            self._set_status(
                "O arquivo de saída não pode ser o mesmo CSV original.",
                is_error=True,
            )
            return

        overwrite = destination.exists()
        if overwrite:
            answer = QMessageBox.question(
                self,
                "Confirmar substituição",
                "O arquivo de saída já existe. Deseja substituí-lo?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._set_status("O arquivo existente não foi alterado.", is_error=False)
                return

        self._start_processing(destination, overwrite=overwrite)

    def _start_processing(self, destination: Path, *, overwrite: bool) -> None:
        if self._inspection_result is None:
            return
        self._clear_output_result()
        self._set_processing_state(True)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)
        self.processed_count_label.setText("0 registros processados")
        self.processed_count_label.setVisible(True)
        self._set_status("Gerando o CSV anonimizado...", is_error=False)

        worker = AnonymizationWorker(
            self._inspection_result,
            str(destination),
            self._column_configs,
            self._key_provider,
            self._vault_repository_factory,
            overwrite=overwrite,
        )
        self._worker = worker
        worker.progress.connect(self._processing_progress)
        worker.completed.connect(self._processing_completed)
        worker.cancelled.connect(self._processing_cancelled)
        worker.failed.connect(self._processing_failed)
        worker.finished.connect(self._worker_finished)
        worker.start()

    def _processing_progress(self, records_processed: int) -> None:
        self.processed_count_label.setText(
            f"{records_processed} registros processados"
        )

    def _processing_completed(self, result: AnonymizationResult) -> None:
        self._set_processing_state(False)
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        self.processed_count_label.setText(
            f"{result.records_processed} registros processados"
        )
        self._last_output_path = result.output_path
        self.output_path_label.setText(f"Arquivo gerado: {result.output_path}")
        self.output_path_label.setVisible(True)
        self.open_folder_button.setVisible(True)
        self.open_folder_button.setEnabled(True)
        self._set_status(
            "CSV anonimizado gerado com sucesso. "
            f"{result.records_processed} registros processados; "
            f"{result.new_mappings} novos mapeamentos; "
            f"{result.updated_mappings} mapeamentos existentes atualizados. "
            "O cofre local foi atualizado.",
            is_error=False,
        )

    def _processing_cancelled(self) -> None:
        self._set_processing_state(False)
        self.progress_bar.setVisible(False)
        self.processed_count_label.setVisible(False)
        self._set_status("A geração do CSV foi cancelada.", is_error=False)

    def _processing_failed(self, error: Exception) -> None:
        self._last_processing_error = error
        self._set_processing_state(False)
        self.progress_bar.setVisible(False)
        self.processed_count_label.setVisible(False)
        if isinstance(error, (CSVAnonymizationError, KeyProviderError, VaultError)):
            message = str(error)
        else:
            message = "Não foi possível gerar o arquivo CSV anonimizado."
        self._set_status(message, is_error=True)

    def _worker_finished(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()

    def cancel_processing(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.request_cancel()
            self.cancel_button.setEnabled(False)
            self._set_status("Cancelamento solicitado...", is_error=False)

    def _set_processing_state(self, processing: bool) -> None:
        has_file = self._inspection_result is not None
        self.select_button.setEnabled(not processing)
        self.clear_button.setEnabled(not processing and has_file)
        self.config_table.setEnabled(not processing)
        self.select_all_button.setEnabled(not processing and has_file)
        self.unselect_all_button.setEnabled(not processing and has_file)
        self.validate_button.setEnabled(not processing and has_file)
        self.generate_button.setEnabled(
            not processing and has_file and self._configuration_validated
        )
        self.profile_combo.setEnabled(not processing)
        if processing:
            for button in (
                self.apply_profile_button,
                self.save_profile_button,
                self.update_profile_button,
                self.rename_profile_button,
                self.delete_profile_button,
            ):
                button.setEnabled(False)
        else:
            self._update_profile_actions()
        self.cancel_button.setVisible(processing)
        self.cancel_button.setEnabled(processing)

    def _clear_output_result(self) -> None:
        self._last_output_path = None
        self.output_path_label.clear()
        self.output_path_label.setVisible(False)
        self.open_folder_button.setVisible(False)
        self.open_folder_button.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.processed_count_label.setVisible(False)

    def open_output_folder(self) -> None:
        if self._last_output_path is None:
            return
        opened = QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(self._last_output_path.parent))
        )
        if not opened:
            self._set_status("Não foi possível abrir a pasta do arquivo.", is_error=True)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.request_cancel()
            if not self._worker.wait(5000):
                event.ignore()
                self._set_status(
                    "Aguarde o cancelamento do processamento antes de fechar.",
                    is_error=False,
                )
                return
        self.consultant_widget.clear_consultation()
        super().closeEvent(event)

    def _set_status(self, message: str, *, is_error: bool) -> None:
        color = "#b42318" if is_error else "#276749"
        self.status_label.setText(message)
        self.status_label.setStyleSheet(f"color: {color};")
