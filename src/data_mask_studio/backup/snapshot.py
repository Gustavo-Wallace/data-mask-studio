import sqlite3
from pathlib import Path
from data_mask_studio.environment import guarded

from data_mask_studio.backup.exceptions import BackupError
from data_mask_studio.backup.models import CancellationRequest


@guarded(lambda source_path, destination_path, cancellation=None: Path(source_path).parent)
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
        if not source.is_file():
            raise BackupError("O cofre local não foi encontrado.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        # A live writable vault can acquire WAL state after this connection opens.
        # Keep SQLite locking/change detection so paged backup restarts safely
        # when a concurrent commit or checkpoint changes the source.
        source_connection = sqlite3.connect(
            f"{source.resolve().as_uri()}?mode=ro",
            uri=True,
        )
        source_connection.execute("PRAGMA busy_timeout = 30000")
        destination_connection = sqlite3.connect(destination)

        def progress(_status: int, _remaining: int, _total: int) -> None:
            cancellation.raise_if_requested()

        source_connection.backup(destination_connection, pages=128, progress=progress)
        destination_connection.commit()
    except BackupError:
        raise
    except (OSError, sqlite3.Error) as error:
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
            # integrity_check validates SQLite structure, not this cross-table
            # invariant enforced by vault decryption during staged restoration.
            # Check counters only: no plaintext, per-record decryption or extra
            # in-memory collection. Schema 1 has no separate variations table.
            pairs = []
            if schema_version in (2, 3, 4):
                pairs.append(("vault_mappings", "vault_variations"))
            if schema_version == 4:
                pairs.append(("composite_mappings", "composite_variations"))
            for mappings, variations in pairs:
                inconsistent = connection.execute(f"""
                    SELECT 1 FROM {mappings} AS m
                    WHERE m.total_occurrences != COALESCE((
                        SELECT SUM(v.occurrence_count) FROM {variations} AS v
                        WHERE v.code = m.code
                    ), 0)
                    LIMIT 1
                """).fetchone()
                if inconsistent is not None:
                    raise BackupError("O snapshot do cofre contém contagens inconsistentes.")
            return schema_version, mapping_count
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise BackupError("O snapshot do cofre é inválido.") from error
