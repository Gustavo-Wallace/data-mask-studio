"""Application messages for explicitly localized presentation components.

Normal GUI startup still uses pt-BR during the incremental migration. Future
activation can pass PreferencesService.resolve_language() to Translator;
this package neither detects a Windows language nor reads/writes preferences.
Instances have an immutable locale; there is no process-wide current language.

Catalogs contain plain text only. Use text() with a plain-text Qt target, or
html() when explicitly rendering HTML (the entire result is escaped). Never
translate user data, protocol values, filenames, tokens or enum values.

Context belongs in stable dotted IDs. Integral operation counts use explicit
.one/.other pairs: exactly 1 is singular, including in pt-BR; 0 is plural.
This is not a general CLDR/decimal plural engine. Additional languages with
other grammatical categories need an explicit rule and corresponding tests.
"""

from data_mask_studio.i18n.catalog import (
    FALLBACK_LOCALE, SUPPORTED_LOCALES, CatalogError, LocalizationError,
    MessageFormatError, MissingMessageError, load_catalogs, validate_catalogs,
)
from data_mask_studio.i18n.translator import Translator

__all__ = [
    "FALLBACK_LOCALE", "SUPPORTED_LOCALES", "CatalogError", "LocalizationError",
    "MessageFormatError", "MissingMessageError", "Translator", "load_catalogs",
    "validate_catalogs",
]
