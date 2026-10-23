"""Read UI-language preferences, never regional/number-formatting settings.

GetUserPreferredUILanguages(MUI_LANGUAGE_NAME) returns the Windows user's
ordered display-language list (Windows supplies its own system fallback).
Only the first nonempty entry is the primary UI language for DMS policy.
Unavailable APIs/non-Windows hosts fall back to English; no Qt globals change.
"""

import ctypes
from ctypes import wintypes
import re
import sys
from collections.abc import Sequence
from typing import Literal

from data_mask_studio.preferences.models import LanguagePreference

EffectiveLanguage = Literal["en", "pt-BR"]
_UI_TAG = re.compile(r"([a-z]{2,3})(?:[-_]([a-z]{4}))?(?:[-_]([a-z]{2}|[0-9]{3}))?", re.I | re.ASCII)
_MAX_LANGUAGE_BUFFER = 65536  # Characters; bounded even if a native API misbehaves.
_MUI_LANGUAGE_NAME = 0x8


def normalize_ui_language(value: str) -> str | None:
    """Normalize language[-script][-region], without inventing a missing region.

    Windows UI names and Qt's underscore spelling are accepted. Unrecognized
    variants, encoding suffixes and region-only values do not imply pt-BR.
    """
    if not isinstance(value, str):
        return None
    match = _UI_TAG.fullmatch(value.strip())
    if match is None:
        return None
    language, script, region = match.groups()
    return "-".join(part for part in (
        language.lower(), script.title() if script else None, region.upper() if region else None,
    ) if part)


def resolve_language(
    preference: LanguagePreference | str, ui_languages: Sequence[str] = (),
) -> EffectiveLanguage:
    """Resolve a validated preference; do not scan secondary OS fallback entries."""
    preference = LanguagePreference(preference)
    if preference is LanguagePreference.ENGLISH:
        return "en"
    if preference is LanguagePreference.BRAZILIAN_PORTUGUESE:
        return "pt-BR"
    primary = next((tag for tag in ui_languages if isinstance(tag, str) and tag.strip()), "")
    return "pt-BR" if normalize_ui_language(primary) in {"pt-BR", "pt-Latn-BR"} else "en"


def detect_system_ui_languages() -> tuple[str, ...]:
    """Query only the current process account, not another interactive session."""
    if sys.platform != "win32":
        return ()
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        query = kernel.GetUserPreferredUILanguages
        query.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.ULONG),
                          wintypes.LPWSTR, ctypes.POINTER(wintypes.ULONG)]
        query.restype = wintypes.BOOL
        count, length = wintypes.ULONG(), wintypes.ULONG()
        if not query(_MUI_LANGUAGE_NAME, ctypes.byref(count), None, ctypes.byref(length)):
            return ()
        capacity = length.value
        if not 2 <= capacity <= _MAX_LANGUAGE_BUFFER:
            return ()
        buffer = ctypes.create_unicode_buffer(capacity)
        if not query(_MUI_LANGUAGE_NAME, ctypes.byref(count), buffer, ctypes.byref(length)):
            return ()
        if not 0 < count.value < capacity or not 2 <= length.value <= capacity:
            return ()
        text = buffer[:length.value]
        if not text.endswith("\0\0"):
            return ()
        languages = tuple(tag for tag in text.split("\0") if tag)
        return languages if len(languages) == count.value else ()
    except (OSError, AttributeError, ValueError):
        return ()
