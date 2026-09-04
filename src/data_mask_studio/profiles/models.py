from dataclasses import dataclass
from datetime import datetime

from data_mask_studio.anonymization.models import ColumnAction
from data_mask_studio.normalization import NormalizationRule

PROFILE_FORMAT_VERSION = 1
PROFILES_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True, init=False)
class ProfileColumn:
    header: str
    prefix: str
    normalization_rule: NormalizationRule
    action: ColumnAction

    def __init__(
        self,
        header: str,
        prefix: str,
        normalization_rule: NormalizationRule,
        anonymize: bool = True,
        *,
        action: ColumnAction | None = None,
    ) -> None:
        object.__setattr__(self, "header", header)
        object.__setattr__(self, "prefix", prefix)
        object.__setattr__(self, "normalization_rule", normalization_rule)
        object.__setattr__(
            self,
            "action",
            action
            or (ColumnAction.MASK if anonymize else ColumnAction.PRESERVE),
        )

    @property
    def anonymize(self) -> bool:
        """Compatibilidade de leitura com o formato anterior dos perfis."""
        return self.action is ColumnAction.MASK


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
