"""Small preferences store, independent of vault keys, leases and backups.

UTF-8 format v1: {"schema_version": 1, "language": "system"|"en"|"pt-BR"}.
Reads never write defaults. Invalid/unknown documents also block saving, so
they are not silently repaired or downgraded. Callers receive immutable values
and safe error codes, never the mutable JSON document or raw IO exceptions.

Like profile persistence, write/fsync a unique same-directory temporary and
os.replace it. Concurrent instances use whole-document last-replacement-wins
for this single setting, with no cache or environment-wide lock. This is not
a multi-setting read/modify/write transaction or a power-loss recovery journal;
revisit concurrency before adding independent writable preferences.
"""

import json
import os
from pathlib import Path
import tempfile
from collections.abc import Callable, Sequence

from data_mask_studio.preferences.language import (
    EffectiveLanguage, detect_system_ui_languages, resolve_language,
)
from data_mask_studio.preferences.models import (
    LanguagePreference, Preferences, PreferencesError, PreferencesResult,
)

PREFERENCES_FILE_NAME = "preferences.json"
PREFERENCES_SCHEMA_VERSION = 1
MAX_PREFERENCES_BYTES = 16 * 1024  # Two tiny fields; reject oversized/corrupt input before JSON parsing.


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
            if (not isinstance(document, dict) or set(document) != {"schema_version", "language"}
                    or type(document["schema_version"]) is not int
                    or document["schema_version"] != PREFERENCES_SCHEMA_VERSION):
                raise ValueError
            language = LanguagePreference(document["language"])
        except (UnicodeError, ValueError, TypeError, RecursionError):
            return PreferencesResult(error=PreferencesError.INVALID_FILE)
        return PreferencesResult(Preferences(language))

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
        try:
            language = LanguagePreference(language)
        except (ValueError, TypeError):
            return PreferencesResult(error=PreferencesError.INVALID_LANGUAGE)
        current = self.load()
        if current.error is not None:
            return current
        assert self.path is not None
        data = json.dumps({
            "schema_version": PREFERENCES_SCHEMA_VERSION,
            "language": language.value,
        }, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
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
        except (OSError, ValueError):
            return PreferencesResult(current.preferences, PreferencesError.WRITE_FAILED)
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
        return PreferencesResult(Preferences(language))
