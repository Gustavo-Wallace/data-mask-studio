"""Gera capturas offscreen para revisão visual; não integra os artefatos finais."""

from argparse import ArgumentParser
from pathlib import Path
import shutil
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from data_mask_studio.app import create_application
from data_mask_studio.backup import EnvironmentPaths
from data_mask_studio.gui.main_window import MainWindow
from data_mask_studio.profiles import ProfileRepository, ProfileService
from data_mask_studio.vault import VaultCipher, VaultRepository


CAPTURE_PAGES = {
    "anonimizar-csv": "anonymization_widget",
    "restauracao-lote": "batch_restoration_widget",
    "backup-recuperacao": "backup_widget",
    # Preserve the historical filename for the consolidated integrity overview.
    "integridade": "maintenance_widget",
    "cofre-manutencao": "maintenance_widget",
}
CAPTURE_SIZES = ((1100, 760), (1280, 800), (1920, 1080))


def capture_page(application: QApplication, window: MainWindow, name: str) -> QPixmap:
    try:
        target = getattr(window, CAPTURE_PAGES[name])
        if not isinstance(target, QWidget) or not target.isWidgetType():
            raise ValueError("O destino não é uma página Qt.")
        index = window.page_index(target)
    except (AttributeError, KeyError, ValueError, RuntimeError) as error:
        raise RuntimeError(f"Página de captura indisponível: {name}.") from error
    if (type(index) is not int or not 0 <= index < window.page_stack.count()
            or index >= len(window.page_widgets) or window.page_widgets[index] is not target):
        raise RuntimeError(f"Índice de captura inválido: {name}.")
    shell = window.page_stack.widget(index)
    if getattr(shell, "content", None) is not target:
        raise RuntimeError(f"Conteúdo de captura inválido: {name}.")
    overview = None
    if name == "integridade":
        overview = target.overview_output.parentWidget()
        if overview is None or target.sections.indexOf(overview) < 0:
            raise RuntimeError("Visão geral da integridade indisponível.")
        target.sections.setCurrentWidget(overview)
    window.set_current_page(index)
    application.processEvents()
    if (window.current_page_index() != index or window.navigation.current_index() != index
            or window.page_stack.currentWidget() is not shell or shell.content is not target
            or not target.isVisible()
            or (overview is not None and target.sections.currentWidget() is not overview)):
        raise RuntimeError(f"A página solicitada não ficou ativa: {name}.")
    return window.grab()


class FixedKeyProvider:
    def __init__(self, key: bytes) -> None:
        self.key = key

    def get_key(self) -> bytes:
        return self.key


class DevelopmentProtector:
    def protect(self, data: bytes) -> bytes:
        return b"ui-validation:" + data

    def unprotect(self, data: bytes) -> bytes:
        return data.removeprefix(b"ui-validation:")


def main() -> int:
    parser = ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmarks/.data/ui-validation"),
    )
    parser.add_argument(
        "--readme-capture",
        type=Path,
        help="Salva também a captura vazia 1280x800 usada no README.",
    )
    args = parser.parse_args()
    output = args.output.resolve()
    local = output / "isolated-local-data"
    if output.exists():
        shutil.rmtree(output)
    local.mkdir(parents=True)
    paths = EnvironmentPaths(
        directory=local,
        hmac_key_path=local / "secret.key",
        vault_key_path=local / "vault_key.dpapi",
        vault_database_path=local / "vault.db",
        profiles_path=local / "profiles.json",
    )
    cipher = VaultCipher(b"V" * 32)
    application = create_application(sys.argv[:1])
    window = MainWindow(
        key_provider=FixedKeyProvider(b"H" * 32),
        vault_repository_factory=lambda: VaultRepository(paths.vault_database_path, cipher),
        profile_service=ProfileService(ProfileRepository(paths.profiles_path)),
        backup_paths=paths,
        vault_key_provider=FixedKeyProvider(b"V" * 32),
        data_protector=DevelopmentProtector(),
    )
    window.show()
    for width, height in CAPTURE_SIZES:
        window.resize(width, height)
        for name in CAPTURE_PAGES:
            captured = capture_page(application, window, name)
            destination = output / f"{width}x{height}-{name}.png"
            if not captured.save(str(destination)):
                raise RuntimeError(f"Não foi possível salvar {destination}.")
            if args.readme_capture and (width, height) == (1280, 800) and name == "anonimizar-csv":
                readme_capture = args.readme_capture.resolve()
                readme_capture.parent.mkdir(parents=True, exist_ok=True)
                readme_image = captured.scaled(
                    1280,
                    800,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                if not readme_image.save(str(readme_capture), "PNG"):
                    raise RuntimeError(f"Não foi possível salvar {readme_capture}.")
    window.close()
    application.processEvents()
    print(f"{len(CAPTURE_SIZES) * len(CAPTURE_PAGES)} capturas salvas em {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
