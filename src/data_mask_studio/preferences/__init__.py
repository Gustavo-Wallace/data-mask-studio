"""Typed local preferences; theme storage does not apply an appearance."""

from data_mask_studio.preferences.language import (
    EffectiveLanguage, detect_system_ui_languages, normalize_ui_language, resolve_language,
)
from data_mask_studio.preferences.models import (
    LanguagePreference, ThemePreference, Preferences, PreferencesError, PreferencesResult,
)
from data_mask_studio.preferences.service import PreferencesService, default_preferences_path

__all__ = [
    "EffectiveLanguage", "LanguagePreference", "ThemePreference", "Preferences", "PreferencesError",
    "PreferencesResult", "PreferencesService", "default_preferences_path",
    "detect_system_ui_languages", "normalize_ui_language", "resolve_language",
]
