from dataclasses import dataclass
from datetime import datetime

from data_mask_studio.normalization import NormalizationRule

PROFILE_FORMAT_VERSION = 1
PROFILES_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class ProfileColumn:
    header: str
    prefix: str
    normalization_rule: NormalizationRule
    anonymize: bool = True


@dataclass(frozen=True, slots=True)
class ConfigurationProfile:
    identifier: str
    name: str
    format_version: int
    created_at: datetime
    modified_at: datetime
    columns: tuple[ProfileColumn, ...]


@dataclass(frozen=True, slots=True)
class ProfileApplicationResult:
    configurations: tuple[ProfileColumn, ...]
    matched_headers: tuple[str, ...]
    missing_headers: tuple[str, ...]

    @property
    def has_matches(self) -> bool:
        return bool(self.matched_headers)

    @property
    def is_complete(self) -> bool:
        return self.has_matches and not self.missing_headers
