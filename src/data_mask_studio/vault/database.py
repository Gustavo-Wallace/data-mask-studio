import os
import sqlite3
from pathlib import Path

from data_mask_studio.vault.exceptions import VaultError

SCHEMA_VERSION = 1
DATABASE_FILE_NAME = "vault.db"
SQLITE_TIMEOUT_SECONDS = 30.0

CREATE_SCHEMA_SQL = """
CREATE TABLE vault_mappings (
    code TEXT PRIMARY KEY,
    prefix TEXT NOT NULL,
    encrypted_value BLOB NOT NULL,
    nonce BLOB NOT NULL,
    source_header TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL CHECK (occurrence_count > 0)
);
"""


def default_vault_directory() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise VaultError("A pasta local de dados do Windows não está disponível.")
    return Path(local_app_data) / "DataMaskStudio"


def default_database_path() -> Path:
    return default_vault_directory() / DATABASE_FILE_NAME


def connect(database_path: Path) -> sqlite3.Connection:
    try:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            database_path,
            timeout=SQLITE_TIMEOUT_SECONDS,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection
    except sqlite3.Error as error:
        raise VaultError("Não foi possível abrir o cofre local.") from error


def initialize_schema(database_path: Path) -> None:
    connection = connect(database_path)
    try:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version == 0:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(CREATE_SCHEMA_SQL)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        elif version != SCHEMA_VERSION:
            raise VaultError("A versão do cofre local não é compatível.")
    except sqlite3.Error as error:
        raise VaultError("Não foi possível preparar o cofre local.") from error
    finally:
        connection.close()
