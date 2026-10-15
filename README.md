English | [Português (Brasil)](README.pt-BR.md)

<p align="center">
  <img src="assets/branding/dms_icon.svg" alt="Data Mask Studio icon" width="128">
</p>

<h1 align="center">Data Mask Studio (DMS)</h1>

<p align="center">
  Open-source data preparation and masking for Windows, created by Gustavo Wallace Macedo Santos.
</p>

<p align="center">
  <a href="https://github.com/Gustavo-Wallace/data-mask-studio/actions/workflows/tests.yml"><img src="https://github.com/Gustavo-Wallace/data-mask-studio/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
  <a href="https://github.com/Gustavo-Wallace/data-mask-studio/releases/latest"><img src="https://img.shields.io/github/v/release/Gustavo-Wallace/data-mask-studio" alt="Latest release"></a>
  <img src="https://img.shields.io/badge/Python-3.12%2B-3776AB" alt="Python 3.12+">
  <img src="https://img.shields.io/badge/Windows-10%2F11-0078D4" alt="Windows 10 and 11">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0--only-blue.svg" alt="GPL-3.0-only license"></a>
</p>

## Overview

Data Mask Studio is a local-first desktop application for data preparation, anonymization and deterministic data masking, with controlled restoration through an encrypted local vault or a selective encrypted transfer package. Data stays local; individual tokens do not contain or directly expose the original value.

The current version can selectively restore masked CSV files and codes found in local HTML files and dashboards.

Version 1.3.0 is in preparation and has not been published. The download link below points to the latest published release.

Official website: https://gustavo-wallace.github.io/data-mask-studio/

## Download the latest version

[**Download the latest release**](https://github.com/Gustavo-Wallace/data-mask-studio/releases/latest)

Each release provides a per-user Windows installer (`DataMaskStudio-Setup-<version>.exe`) and a portable package (`DataMaskStudio-Portable-<version>.zip`). For the portable edition, extract the entire folder before running `DataMaskStudio.exe`.

## Interface

<p align="center">
  <a href="docs/images/data-mask-studio-main.png"><img src="docs/images/data-mask-studio-main.png" alt="Data Mask Studio 1.3.0 preview: CSV column actions, output headers, prefixes and normalization, with synthetic data" width="900"></a>
</p>

Preview of the upcoming 1.3.0, using isolated synthetic data. The Windows interface is shown in Portuguese. Open an image for full-resolution detail.

<details>
<summary>Batch processing and transfer-package restoration</summary>

Three synthetic CSVs successfully processed with a saved profile:

<a href="docs/images/data-mask-studio-batch.png"><img src="docs/images/data-mask-studio-batch.png" alt="Data Mask Studio batch processing: three completed CSVs and a final summary of 360 processed rows" width="900"></a>

Selective restoration from a matching encrypted `.dmspackage`, with the password cleared after completion:

<a href="docs/images/data-mask-studio-package.png"><img src="docs/images/data-mask-studio-package.png" alt="Data Mask Studio 1.3.0 preview: successful CSV restoration from a transfer package with synthetic data" width="900"></a>

</details>

## Main features

- CSV preparation and masking, individually or in batches with saved profiles.
- Preserve, mask or exclude columns; normalize values, rename output headers and compose columns.
- Assisted column detection.
- Deterministic masking with vault-controlled or selective package-based restoration.
- Optional encrypted transfer packages (`.dmspackage`) generated alongside an individual masked CSV.
- CSV and HTML restoration.
- Encrypted local vault.
- Password-protected full environment backups (`.dmsbackup`).
- Integrity auditing, diagnostics, and maintenance.
- Streaming processing for large files.
- Local operation without telemetry.

## Installation

For the installer edition, run the Setup file and follow the wizard. Installation is per user. For the portable edition, extract the ZIP into a new folder and run `DataMaskStudio.exe`.

## Basic workflow

1. Select a CSV and choose the columns to mask.
2. Configure prefixes and normalization rules, then validate the configuration.
3. Generate the masked CSV, optionally with a password-protected transfer package, or use a saved profile for batch processing.
4. Use the “Restore CSV” tab to recover selected tabular data from the local vault or from the matching package and its password.
5. Use the “Restore HTML” tab for local HTML files and dashboards, using the local vault.
6. Use the local consultant when you need to inspect a specific code.

## Security and privacy

Processing is local; files and data are not sent automatically. Local keys are protected by Windows DPAPI. See the [security policy](SECURITY.md) and [privacy policy](PRIVACY.md). This does not constitute a guarantee of absolute security.

Deterministic masking is pseudonymization, not irreversible anonymization. Treat transfer packages and backups as sensitive: their passwords allow recovery of the included data. Package and backup encryption uses AES-256-GCM with password-derived keys.

The executables are not digitally signed yet, so Windows may display a SmartScreen warning.

## Architecture overview

The PySide6 desktop interface coordinates separate services for CSV and HTML processing, profiles, backups, and vault maintenance. Sensitive mappings are protected in a local SQLite vault with AES-256-GCM, while local keys are protected by Windows DPAPI. Processing paths use bounded-memory streaming and publish generated files atomically.

## Compatibility and recovery

The 1.3.0 preparation retains vault schema 4 and the existing token formats. Supported older vaults are migrated using the original keys. See [COMPATIBILITY.md](COMPATIBILITY.md) for format limits and recovery guidance.

- **Local vault:** restoration needs the vault and its original local keys, protected by Windows DPAPI.
- **Selective transfer package:** contains only the restoration mappings for its linked CSV, not the full environment. The recipient needs the unchanged masked CSV, the `.dmspackage`, its password and a DMS version supporting package format v1; no sender vault or DPAPI keys are needed. Creation requires at least one Mask action and an 8-character minimum password. Missing mappings abort restoration; there is no fallback to the local vault.
- **Full environment backup:** `.dmsbackup` contains the local keys, vault and saved profiles when present, not CSV or HTML files. It uses a separate password policy (minimum 12 characters) and restores the local environment rather than selected CSV values.

## Run from source

In Windows PowerShell:

```powershell
git clone https://github.com/Gustavo-Wallace/data-mask-studio.git
cd data-mask-studio
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python -m data_mask_studio
```

## Limitations

- The current distribution targets Windows because it relies on Windows DPAPI.
- Transfer-package generation and restoration are available for individual CSVs, not HTML or batch operations. Packages must match the exact CSV bytes and have a default decrypted-payload limit of 64 MiB.
- There is no synchronization, telemetry, automatic updating, or remote key recovery.
- Executables are not digitally signed.

## Documentation

- [Changelog](CHANGELOG.md)
- [Security policy](SECURITY.md)
- [Privacy policy](PRIVACY.md)
- [Compatibility and recovery](COMPATIBILITY.md)

## License

Data Mask Studio is distributed under the [GNU General Public License v3.0 only](LICENSE) (`GPL-3.0-only`). Third-party components remain subject to their own licenses.

Created by Gustavo Wallace Macedo Santos.

Copyright © 2026 Gustavo Wallace Macedo Santos
