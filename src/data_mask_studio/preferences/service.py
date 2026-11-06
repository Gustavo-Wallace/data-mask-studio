"""Small preferences store, independent of vault keys, leases and backups.

UTF-8 v1 requires schema_version/language, with optional theme (default system).
Reads never write defaults. Invalid/unknown documents also block saving, so
they are not silently repaired or downgraded. Callers receive immutable values
and safe error codes, never the mutable JSON document or raw IO exceptions.

All writes lock one stable sidecar, reload the authoritative document, merge
only the requested fields, validate, then write/fsync/replace atomically.
Busy/unavailable locks fail safely without waiting or touching vault state.
This is not a power-loss recovery journal or protection from non-DMS writers.
"""

import json
import os
from pathlib import Path
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import replace

from data_mask_studio.preferences.language import (
    EffectiveLanguage, detect_system_ui_languages, resolve_language,
)
from data_mask_studio.preferences.models import (
    LanguagePreference, ThemePreference, Preferences, PreferencesError, PreferencesResult,
)
from data_mask_studio.preferences.locking import preferences_write_lock

PREFERENCES_FILE_NAME = "preferences.json"
PREFERENCES_SCHEMA_VERSION = 1
MAX_PREFERENCES_BYTES = 16 * 1024  # Reject oversized/corrupt input before JSON parsing.

# The closed set of enum fields is shared by parsing, patch validation and
# serialization. Adding a typed default/field here does not rewrite save paths.
_FIELDS = {
    "language": (LanguagePreference, PreferencesError.INVALID_LANGUAGE),
    "theme": (ThemePreference, PreferencesError.INVALID_THEME),
}


def _parse_document(document: object) -> Preferences:
    if (not isinstance(document, dict) or not {"schema_version", "language"} <= document.keys()
            or document.keys() - {"schema_version"} - _FIELDS.keys()
            or type(document["schema_version"]) is not int
            or document["schema_version"] != PREFERENCES_SCHEMA_VERSION):
        raise ValueError("Invalid preference document")
    defaults = Preferences()
    return Preferences(**{
        name: kind(document.get(name, getattr(defaults, name)))
        for name, (kind, _) in _FIELDS.items()
    })


def _document(preferences: Preferences) -> dict:
    return {"schema_version": PREFERENCES_SCHEMA_VERSION, **{
        name: getattr(preferences, name).value for name in _FIELDS
    }}


def default_preferences_path() -> Path | None:
    """Same per-user DMS directory in installed/portable mode; no cwd fallback."""
    local = os.environ.get("LOCALAPPDATA")
    if not local or not Path(local).is_absolute():
        return None
    return Path(local) / "DataMaskStudio" / PREFERENCES_FILE_NAME


def _unique_fields(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("Duplicate preference field")
        document[key] = value
    return document


class PreferencesService:
    def __init__(
        self, path: str | Path | None = None, *,
        ui_languages_provider: Callable[[], Sequence[str]] = detect_system_ui_languages,
    ) -> None:
        self._path = Path(path).absolute() if path is not None else default_preferences_path()
        self._ui_languages_provider = ui_languages_provider

    @property
    def path(self) -> Path | None:
        return self._path

    def load(self) -> PreferencesResult:
        if self.path is None:
            return PreferencesResult(error=PreferencesError.STORAGE_UNAVAILABLE)
        try:
            if self.path.is_symlink() or self.path.is_junction():
                return PreferencesResult(error=PreferencesError.READ_FAILED)
            with self.path.open("rb") as stream:
                raw = stream.read(MAX_PREFERENCES_BYTES + 1)
        except FileNotFoundError:
            return PreferencesResult()
        except (OSError, ValueError):
            return PreferencesResult(error=PreferencesError.READ_FAILED)
        try:
            if len(raw) > MAX_PREFERENCES_BYTES:
                raise ValueError
            document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_fields)
            preferences = _parse_document(document)
        except (UnicodeError, ValueError, TypeError, RecursionError):
            return PreferencesResult(error=PreferencesError.INVALID_FILE)
        return PreferencesResult(preferences)

    def resolve_language(self, preference: LanguagePreference | str) -> EffectiveLanguage:
        """Resolution is separate from storage and never saves OS detection."""
        preference = LanguagePreference(preference)
        if preference is not LanguagePreference.SYSTEM:
            return resolve_language(preference)
        try:
            languages = self._ui_languages_provider()
            return resolve_language(preference, languages)
        except Exception:
            # OS detection is best-effort, non-sensitive presentation metadata.
            # A provider failure must not prevent use of the application.
            return "en"

    def save_language(self, language: LanguagePreference | str) -> PreferencesResult:
        return self._update(language=language)

    def save_theme(self, theme: ThemePreference | str) -> PreferencesResult:
        """Persist a choice only; never resolve/apply a theme or emit GUI signals."""
        return self._update(theme=theme)

    def _update(self, **changes: LanguagePreference | ThemePreference | str) -> PreferencesResult:
        typed = {}
        for name, value in changes.items():
            kind, error = _FIELDS[name]
            try:
                typed[name] = kind(value)
            except (ValueError, TypeError):
                return PreferencesResult(error=error)
        # Preserve read-error semantics without creating a sidecar for a known
        # invalid/unreadable document. The authoritative read is repeated under
        # the lock; this preflight snapshot never authorizes a successful save.
        current = self.load()
        if current.error is not None:
            return current
        assert self.path is not None
        try:
            with preferences_write_lock(self.path):
                current = self.load()
                if current.error is not None:
                    return current
                merged = replace(current.preferences, **typed)
                document = _document(merged)
                _parse_document(document)  # Validate the entire merged document.
                data = json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
                return self._persist(merged, data)
        except (OSError, ValueError):
            return PreferencesResult(current.preferences, PreferencesError.WRITE_FAILED)

    def _persist(self, preferences: Preferences, data: bytes) -> PreferencesResult:
        assert self.path is not None
        temporary: Path | None = None
        descriptor: int | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent,
            )
            temporary = Path(name)
            stream = os.fdopen(descriptor, "wb")
            descriptor = None
            with stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass  # Only our own temporary; never touch another file.
        return PreferencesResult(preferences)
