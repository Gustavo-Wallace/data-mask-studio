"""Gera capturas offscreen para revisão visual; não integra os artefatos finais."""

from argparse import ArgumentParser
from pathlib import Path
import shutil
import sys

from data_mask_studio.app import create_application
from data_mask_studio.backup import EnvironmentPaths
from data_mask_studio.gui.main_window import MainWindow
from data_mask_studio.profiles import ProfileRepository, ProfileService
from data_mask_studio.vault import VaultCipher, VaultRepository


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
        default=Path("benchmarks/.data/ui-validation-0.11.0"),
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
    pages = {
        "anonimizar-csv": 0,
        "anonimizacao-lote": 1,
        "restaurar-csv": 2,
        "backup-recuperacao": 6,
        "cofre-manutencao": 8,
    }
    sizes = ((1100, 760), (1280, 800), (1920, 1080))
    window.show()
    for width, height in sizes:
        window.resize(width, height)
        for name, page in pages.items():
            window.set_current_page(page)
            application.processEvents()
            destination = output / f"{width}x{height}-{name}.png"
            if not window.grab().save(str(destination)):
                raise RuntimeError(f"Não foi possível salvar {destination}.")
    window.close()
    application.processEvents()
    print(f"15 capturas salvas em {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
