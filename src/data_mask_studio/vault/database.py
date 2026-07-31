import os
import sqlite3
from pathlib import Path

from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.vault.encryption import VaultCipher
from data_mask_studio.vault.exceptions import VaultError

SCHEMA_VERSION = 2
DATABASE_FILE_NAME = "vault.db"
SQLITE_TIMEOUT_SECONDS = 30.0

CREATE_MAPPINGS_SQL = """
CREATE TABLE vault_mappings (
    code TEXT PRIMARY KEY,
    prefix TEXT NOT NULL,
    canonical_encrypted_value BLOB NOT NULL,
    canonical_nonce BLOB NOT NULL,
    source_header TEXT NOT NULL,
    normalization_rule TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    total_occurrences INTEGER NOT NULL CHECK (total_occurrences > 0)
)
"""

CREATE_VARIATIONS_SQL = """
CREATE TABLE vault_variations (
    identifier INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL REFERENCES vault_mappings(code) ON DELETE CASCADE,
    encrypted_value BLOB NOT NULL,
    nonce BLOB NOT NULL,
    normalization_rule TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL CHECK (occurrence_count > 0)
)
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


def connect_read_only(database_path: Path) -> sqlite3.Connection:
    """Abre um cofre existente sem permitir qualquer alteracao."""
    try:
        if not database_path.is_file():
            raise VaultError("O cofre local nao foi encontrado.")
        connection = sqlite3.connect(
            f"{database_path.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=SQLITE_TIMEOUT_SECONDS,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection
    except VaultError:
        raise
    except (OSError, sqlite3.Error) as error:
        raise VaultError("Nao foi possivel abrir o cofre local.") from error


def initialize_schema(database_path: Path, cipher: VaultCipher) -> None:
    connection = connect(database_path)
    try:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version == 0:
            connection.execute("BEGIN IMMEDIATE")
            try:
                _create_schema_v2(connection)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        elif version == 1:
            _migrate_v1_to_v2(connection, cipher)
        elif version != SCHEMA_VERSION:
            raise VaultError("A versão do cofre local não é compatível.")
    except VaultError:
        raise
    except Exception as error:
        raise VaultError("Não foi possível preparar o cofre local.") from error
    finally:
        connection.close()


def _create_schema_v2(connection: sqlite3.Connection) -> None:
    connection.execute(CREATE_MAPPINGS_SQL)
    connection.execute(CREATE_VARIATIONS_SQL)
    connection.execute(
        "CREATE INDEX vault_variations_code_idx ON vault_variations(code)"
    )


def _migrate_v1_to_v2(
    connection: sqlite3.Connection,
    cipher: VaultCipher,
) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        rows = connection.execute(
            "SELECT code, prefix, encrypted_value, nonce, source_header, "
            "first_seen, last_seen, occurrence_count FROM vault_mappings"
        ).fetchall()
        connection.execute("ALTER TABLE vault_mappings RENAME TO vault_mappings_v1")
        _create_schema_v2(connection)

        for row in rows:
            code = str(row["code"])
            prefix = str(row["prefix"])
            original_value = cipher.decrypt(
                code,
                prefix,
                bytes(row["encrypted_value"]),
                bytes(row["nonce"]),
            )
            variation = cipher.encrypt(code, prefix, original_value)
            connection.execute(
                "INSERT INTO vault_mappings "
                "(code, prefix, canonical_encrypted_value, canonical_nonce, "
                "source_header, normalization_rule, first_seen, last_seen, "
                "total_occurrences) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    code,
                    prefix,
                    bytes(row["encrypted_value"]),
                    bytes(row["nonce"]),
                    str(row["source_header"]),
                    NormalizationRule.EXACT.value,
                    str(row["first_seen"]),
                    str(row["last_seen"]),
                    int(row["occurrence_count"]),
                ),
            )
            connection.execute(
                "INSERT INTO vault_variations "
                "(code, encrypted_value, nonce, first_seen, last_seen, "
                "normalization_rule, occurrence_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    code,
                    variation.ciphertext,
                    variation.nonce,
                    str(row["first_seen"]),
                    str(row["last_seen"]),
                    NormalizationRule.EXACT.value,
                    int(row["occurrence_count"]),
                ),
            )

        connection.execute("DROP TABLE vault_mappings_v1")
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
