English | [Português (Brasil)](README.pt-BR.md)

<p align="center">
  <img src="assets/branding/dms_icon.svg" alt="Data Mask Studio icon" width="128">
</p>

<h1 align="center">Data Mask Studio (DMS)</h1>

<p align="center">
  Open-source data masking for CSV files on Windows, created by Gustavo Wallace Macedo Santos.
</p>

<p align="center">
  <a href="https://github.com/Gustavo-Wallace/data-mask-studio/actions/workflows/tests.yml"><img src="https://github.com/Gustavo-Wallace/data-mask-studio/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
  <a href="https://github.com/Gustavo-Wallace/data-mask-studio/releases/latest"><img src="https://img.shields.io/github/v/release/Gustavo-Wallace/data-mask-studio" alt="Latest release"></a>
  <img src="https://img.shields.io/badge/Python-3.12%2B-3776AB" alt="Python 3.12+">
  <img src="https://img.shields.io/badge/Windows-10%2F11-0078D4" alt="Windows 10 and 11">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0--only-blue.svg" alt="GPL-3.0-only license"></a>
</p>

## Overview

Data Mask Studio is a desktop application for protecting sensitive data in CSV files without sending it to external services. It provides deterministic data masking with controlled restoration through an encrypted local vault. Individual tokens do not contain or directly expose the original value.

The current version can selectively restore masked CSV files and codes found in local HTML files and dashboards.

Official website: https://gustavo-wallace.github.io/data-mask-studio/

GitHub: https://github.com/Gustavo-Wallace/data-mask-studio

## Download the latest version

[**Download the latest release**](https://github.com/Gustavo-Wallace/data-mask-studio/releases/latest)

Each release provides a per-user Windows installer (`DataMaskStudio-Setup-<version>.exe`) and a portable package (`DataMaskStudio-Portable-<version>.zip`). For the portable edition, extract the entire folder before running `DataMaskStudio.exe`.

## Interface

<p align="center">
  <img src="docs/images/data-mask-studio-main.png" alt="Data Mask Studio main interface" width="900">
</p>

## Main features

- Individual and batch CSV data masking.
- Assisted column detection.
- Deterministic tokens with controlled restoration.
- CSV and HTML restoration.
- Encrypted local vault.
- Password-protected portable backups.
- Integrity auditing, diagnostics, and maintenance.
- Streaming processing for large files.
- Local operation without telemetry.

## Installation

For the installer edition, run the Setup file and follow the wizard. Installation is per user. For the portable edition, extract the ZIP into a new folder and run `DataMaskStudio.exe`.

## Basic workflow

1. Select a CSV and choose the columns to mask.
2. Configure prefixes and normalization rules, then validate the configuration.
3. Generate the masked CSV or use a saved profile for batch processing.
4. Use the “Restore CSV” tab to recover selected tabular data.
5. Use the “Restore HTML” tab for local HTML files and dashboards.
6. Use the local consultant when you need to inspect a specific code.

## Security and privacy

Processing is local; files and data are not sent automatically. Local keys are protected by Windows DPAPI. See the [security policy](SECURITY.md) and [privacy policy](PRIVACY.md). This does not constitute a guarantee of absolute security.

The executables are not digitally signed yet, so Windows may display a SmartScreen warning.

## Architecture overview

The PySide6 desktop interface coordinates separate services for CSV and HTML processing, profiles, backups, and vault maintenance. Sensitive mappings are protected in a local SQLite vault with AES-256-GCM, while local keys are protected by Windows DPAPI. Processing paths use bounded-memory streaming and publish generated files atomically.

## Compatibility and recovery

The 1.0.x series preserves token compatibility, schema 3, and valid backups. Supported older vaults are migrated transactionally. See [COMPATIBILITY.md](COMPATIBILITY.md) for the stability contract and recovery guidance.

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
