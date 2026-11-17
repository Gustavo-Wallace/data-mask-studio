<a id="compatibilidade-e-recuperação"></a>

# Compatibility and recovery

<a id="versão-130"></a>

## Version 1.4.0

Version 1.4.0 retains vault schema 4, Profile Format v2 (including support for valid v1 profiles), backup format v1 for `.dmsbackup` and the current Base32-12 token format. `.dmspackage` uses its own format v1, independent of the vault schema and backup format.

Vault schemas 1, 2 and 3 still have supported migrations to schema 4, provided the original keys are available. This does not guarantee that older application versions can open an already migrated vault. Backups with supported schemas are verified and migrated in a staging environment before installation; backups produced by application versions newer than the installed version are incompatible.

## Transfer-package identifier compatibility

Version 1.4 rejects unsupported HEX-24 identifiers during `.dmspackage` export and import, including authenticated packages accepted by the earlier codec. The entire package is rejected before restoration, without filtering, converting or truncating entries. Package restoration of HEX-24 identifiers is not supported.

Base32-12 tokens remain unchanged. This validation does not migrate or rewrite existing vaults or change supported migrations, integrity auditing or local restoration behavior. Package format v1 and its encryption remain unchanged.

<a id="restauração-local-transferência-e-backup"></a>

## Local restoration, transfer and backup

- **Local vault:** requires the database and original Windows DPAPI-protected keys. Copying only the database or DPAPI files does not replace a portable backup.
- **Selective transfer (`.dmspackage`):** exports only scalar and composite mappings needed for the generated CSV, not local keys, every vault mapping or profiles. The recipient uses the bound CSV, package, password and a version supporting format v1. There is no import into the recipient's vault or fallback to it.
- **Environment backup (`.dmsbackup`):** password-protects local keys, the vault and profiles when present. It does not include CSVs, HTML files or transfer packages. Recovery protects the keys again through DPAPI in the destination environment. Minimum creation password: 12 characters, compared with 8 for packages.

Packages and backups are sensitive. Without their passwords there is no remote recovery; do not share them publicly as examples or diagnostics.

<a id="limites-dos-pacotes-de-transferência"></a>

## Transfer-package limits

The interface optionally generates packages during individual CSV masking with at least one Mask action. In Restore file, select a CSV to use the local vault or its package; HTML and batch processing still use the local vault. A package cannot restore another CSV: binding verifies the size and SHA-256 of its bytes, including headers, encoding and line endings. Do not edit the CSV between generation and restoration. An incorrect password, invalid binding or missing required mapping prevents restoration; the source is never silently substituted.

Individual files use atomic creation or replacement. Publication of the CSV and `.dmspackage` pair is recoverable, not a single atomic transaction: interruption can leave an operation pending recovery. Preserve its artifacts and evidence; one published file does not prove that both were published.

The format preserves the original and canonical values needed by existing restoration policies, not the vault's complete history. Normalization and composition do not guarantee byte-for-byte reconstruction of every input representation; excluded columns cannot be recovered through the package.

The default limit is 64 MiB of decrypted payload, not CSV size. Package processing occurs in memory, and large sets of unique or long values can reach this limit. API callers can set a larger budget, up to 1 GiB; the interface does not offer this adjustment. These limits are not negotiated by the file and do not make the format unlimited.

<a id="contrato-da-versão-100"></a>

## Version 1.0.0 contract

Compatible updates in the 1.0.x series must not change previously generated tokens or codes, or prevent valid existing vaults and backups from opening. HMAC-SHA256, truncated Base32, AES-256-GCM, versioned AAD, Windows DPAPI, schema 3, normalization and exact fallback remain stable.

Schema 2 vaults are automatically migrated to schema 3 by operations that allow writing. Migration uses an immediate transaction, preserves codes, values, variations, occurrences and dates, and fully rolls back on failure. Migration is idempotent.

Maintenance and backup snapshots use SQLite's backup API to incorporate committed WAL changes. Read-only diagnostics do not classify a pending migration as tampering.

<a id="recuperação"></a>

## Recovery

1. Close all Data Mask Studio instances.
2. Open Backup and recovery.
3. Select a `.dmsbackup` file created by the application and enter its password.
4. Review the summary before confirming restoration.
5. After restoration, run Vault and maintenance → Overview → Update diagnostic.

Restoration uses staging, verification and recovery of the previous environment on failure. A recovery failure requires manual review; do not discard evidence or assume the environment is ready for use. Backup validation does not replace the record authentication performed before installing the restored environment. Uninstallation does not remove `%LOCALAPPDATA%\DataMaskStudio` by default.

Consistent snapshots use SQLite's backup API, including committed WAL changes. If compaction and rollback both fail, the safety snapshot remains in a `dms-vault-compaction-*` subdirectory of the local environment for manual review. This snapshot does not replace a `.dmsbackup` and still requires the original local keys.
