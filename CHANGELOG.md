# Changelog

## 1.4.0 — Internationalization and User Experience Update

- English and Brazilian Portuguese interface with saved language preferences.
- System, Light and Dark themes applied on the next application launch.
- Unified CSV and HTML restoration in Restore file.
- Drag-and-drop file selection, including files and folders in batch workflows.
- Column search, action filters and contextual help in Mask file.
- Read-only preview of the final CSV column structure.
- Configuration undo/redo for column actions and normalization.
- Navigation icons, keyboard shortcuts and improved focus, tables and feedback.

## 1.3.0 — Selective Transfer Update

- Password-protected `.dmspackage` files bound to a CSV for selective restoration.
- Package-based CSV restoration without the recipient's local vault or keys.
- Improved navigation, controls, tables, dialogs, progress feedback and output actions.
- More reliable backups during concurrent writes, with stronger validation and special-path support.
- Safer profile updates and revalidation during batch processing.
- Safety snapshots preserved when compaction cannot recover the original vault.

## 1.2.0 — Data Preparation Update

- Preserve, Mask and Exclude columns, rename output headers and normalize preserved values.
- Composite columns with Preserve/Mask actions and normalization in batch and restoration workflows.
- Profile Format v2 improves column matching; supported vaults migrate to schema 4.
- Integrity checks for composite mappings, stronger key protection and environment recovery.
- Recoverable output publication and context-aware HTML restoration.
- Consistent vault lookups and restoration, with stricter unknown-column and temporary-file checks.

## 1.1.0

- Person-name normalization handles case, diacritics and whitespace variations.
- Empty CSV headers receive consistent, collision-free names.
- Support for UTF-16 LE/BE and UTF-32 LE/BE CSV input with BOM.
- New CSV capabilities integrated across individual, batch and restoration workflows.

## 1.0.4

- Faster HTML analysis and restoration with more efficient vault lookups.
- English main README with an official Brazilian Portuguese version.
- Improved screenshot scaling on the website.

## 1.0.3

- Consistent dark appearance regardless of the Windows theme.
- Fixed public links in About.
- Clearer GPL-3.0-only licensing and author attribution.

## 1.0.2

- Correct inspection and masking of single-column CSVs.
- Safe diagnostics for CSV rows with missing or extra columns.
- Consistent progress reset in HTML restoration.

## 1.0.1

- README presentation refresh and safe interface capture.
- Corrected About dialog links.
- Bug-report template guides users to provide safe diagnostic information.

## 1.0.0

- First stable Windows release with official branding and public information in About.
- Individual and batch CSV masking, with CSV and HTML restoration.
- Encrypted local vault, password-protected backups, integrity auditing and maintenance.
- Compatibility contract for existing tokens, schema 3 vaults and backups.
- Portable ZIP and per-user Windows installer.

<a id="marcos-anteriores"></a>

## Earlier milestones

- 0.11.x: clearer navigation, responsive layout and improved accessibility.
- 0.10.x: assisted detection and improved handling of files with many unique values.
- 0.9.x: integrity, maintenance and safe vault migration to schema 3.
- 0.8.x: batch restoration and safe environment recovery.
- 0.5.x–0.7.x: Windows distribution, restoration, backup and auditing.
- 0.1.x–0.4.x: CSV reading, column configuration, masking and local vault.
