import sqlite3
from pathlib import Path

from data_mask_studio.backup.exceptions import BackupError
from data_mask_studio.backup.models import CancellationRequest


def create_sqlite_snapshot(
    source_path: str | Path,
    destination_path: str | Path,
    cancellation: CancellationRequest | None = None,
) -> None:
    source = Path(source_path)
    destination = Path(destination_path)
    cancellation = cancellation or CancellationRequest()
    source_connection: sqlite3.Connection | None = None
    destination_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        destination_connection = sqlite3.connect(destination)

        def progress(_status: int, _remaining: int, _total: int) -> None:
            cancellation.raise_if_requested()

        source_connection.backup(destination_connection, pages=128, progress=progress)
        destination_connection.commit()
    except sqlite3.Error as error:
        raise BackupError("Não foi possível criar o snapshot consistente do cofre.") from error
    finally:
        if destination_connection is not None:
            destination_connection.close()
        if source_connection is not None:
            source_connection.close()


def inspect_snapshot(path: str | Path) -> tuple[int, int]:
    try:
        connection = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
        try:
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            if integrity.lower() != "ok":
                raise BackupError("O snapshot do cofre não passou na verificação.")
            schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            mapping_count = int(
                connection.execute("SELECT COUNT(*) FROM vault_mappings").fetchone()[0]
            )
            return schema_version, mapping_count
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise BackupError("O snapshot do cofre é inválido.") from error
