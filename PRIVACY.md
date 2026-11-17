<a id="privacidade"></a>

# Privacy

Data Mask Studio processes files locally on the user's computer. The application has no telemetry, user accounts, synchronization or automatic uploads of files, codes, metrics or diagnostics.

The vault, Windows DPAPI-protected keys, profiles and settings remain in `%LOCALAPPDATA%\DataMaskStudio`. Masked and restored files, backups and transfer packages are written only to destinations chosen by the user.

`.dmspackage` files contain sensitive mappings selected for the bound CSV, protected by a password; they are not uploaded automatically. Unlike a full backup, they do not include local keys or every vault mapping.

Integrity and maintenance reports avoid displaying original values, complete codes, keys and passwords. Before sharing any diagnostic, check that external tools have not combined it with real data.
