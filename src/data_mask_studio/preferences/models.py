"""Only explicit, non-sensitive preferences may enter the persisted document."""

from dataclasses import dataclass
from enum import StrEnum


class LanguagePreference(StrEnum):
    SYSTEM = "system"
    ENGLISH = "en"
    BRAZILIAN_PORTUGUESE = "pt-BR"


@dataclass(frozen=True, slots=True)
class Preferences:
    language: LanguagePreference = LanguagePreference.SYSTEM


class PreferencesError(StrEnum):
    STORAGE_UNAVAILABLE = "storage_unavailable"
    READ_FAILED = "read_failed"
    INVALID_FILE = "invalid_file"
    INVALID_LANGUAGE = "invalid_language"
    WRITE_FAILED = "write_failed"

    @property
    def message(self) -> str:
        # Never include paths, raw JSON, OS exception text or rejected values.
        return {
            self.STORAGE_UNAVAILABLE: "A pasta local de preferências não está disponível.",
            self.READ_FAILED: "Não foi possível ler as preferências do aplicativo.",
            self.INVALID_FILE: "O arquivo de preferências está inválido ou é incompatível. Ele foi preservado.",
            self.INVALID_LANGUAGE: "A preferência de idioma não é suportada.",
            self.WRITE_FAILED: "Não foi possível salvar as preferências do aplicativo.",
        }[self]


@dataclass(frozen=True, slots=True)
class PreferencesResult:
    preferences: Preferences = Preferences()
    error: PreferencesError | None = None
