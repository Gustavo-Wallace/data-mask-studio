"""Application preferences foundation; not yet applied to GUI translations."""

from data_mask_studio.preferences.language import (
    EffectiveLanguage, detect_system_ui_languages, normalize_ui_language, resolve_language,
)
from data_mask_studio.preferences.models import (
    LanguagePreference, Preferences, PreferencesError, PreferencesResult,
)
from data_mask_studio.preferences.service import PreferencesService, default_preferences_path

__all__ = [
    "EffectiveLanguage", "LanguagePreference", "Preferences", "PreferencesError",
    "PreferencesResult", "PreferencesService", "default_preferences_path",
    "detect_system_ui_languages", "normalize_ui_language", "resolve_language",
]
