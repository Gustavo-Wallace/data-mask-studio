"""Extensão aditiva do schema 4; executada na transação da migração."""
import sqlite3


def create_composite_schema(connection: sqlite3.Connection) -> None:
    connection.execute("""CREATE TABLE composite_mappings (
        code TEXT PRIMARY KEY, prefix TEXT NOT NULL,
        identity_version INTEGER NOT NULL, payload_version INTEGER NOT NULL,
        aad_version INTEGER NOT NULL, component_count INTEGER NOT NULL CHECK(component_count >= 2),
        rules TEXT NOT NULL, encrypted_value BLOB NOT NULL, nonce BLOB NOT NULL,
        first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
        total_occurrences INTEGER NOT NULL CHECK(total_occurrences > 0)
    )""")
    connection.execute("""CREATE TABLE composite_variations (
        identifier TEXT PRIMARY KEY,
        code TEXT NOT NULL REFERENCES composite_mappings(code) ON DELETE CASCADE,
        encrypted_value BLOB NOT NULL, nonce BLOB NOT NULL,
        first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
        occurrence_count INTEGER NOT NULL CHECK(occurrence_count > 0)
    )""")
    connection.execute("CREATE INDEX composite_variations_code_idx ON composite_variations(code)")
    # Defesa global inclusive para writers escalares e SQL fora do repository.
    for table, other in (("vault_mappings", "composite_mappings"), ("composite_mappings", "vault_mappings")):
        for operation in ("INSERT", "UPDATE OF code"):
            suffix = "insert" if operation == "INSERT" else "update"
            connection.execute(f"""CREATE TRIGGER {table}_unique_code_{suffix}
                BEFORE {operation} ON {table}
                WHEN EXISTS(SELECT 1 FROM {other} WHERE code = NEW.code)
                BEGIN SELECT RAISE(ABORT, 'vault code collision'); END""")
