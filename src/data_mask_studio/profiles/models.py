from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from data_mask_studio.anonymization.models import ColumnAction
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.csv_tools.source_binding import SourceColumnRef
if TYPE_CHECKING:
    from data_mask_studio.processing.models import CompositeColumnConfig

PROFILE_FORMAT_VERSION = 2
# O container também evolui para impedir leitura parcial por clientes v1.
PROFILES_SCHEMA_VERSION = 2


class UnknownColumnPolicy(StrEnum):
    REQUIRE_EXPLICIT = "require_explicit"


@dataclass(frozen=True, slots=True, init=False)
class ProfileColumn:
    header: str
    prefix: str
    normalization_rule: NormalizationRule
    action: ColumnAction
    output_name: str
    reference: SourceColumnRef | None

    def __init__(
        self,
        header: str,
        prefix: str,
        normalization_rule: NormalizationRule,
        anonymize: bool = True,
        *,
        action: ColumnAction | None = None,
        output_name: str = "",
        reference: SourceColumnRef | None = None,
    ) -> None:
        object.__setattr__(self, "header", header)
        object.__setattr__(self, "reference", reference)
        object.__setattr__(self, "output_name", output_name)
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
    composites: tuple["CompositeColumnConfig", ...] = ()
    unknown_column_policy: UnknownColumnPolicy = UnknownColumnPolicy.REQUIRE_EXPLICIT


@dataclass(frozen=True, slots=True)
class ProfileApplicationResult:
    configurations: tuple[ProfileColumn, ...]
    matched_headers: tuple[str, ...]
    missing_headers: tuple[str, ...]
    extra_headers: tuple[str, ...] = ()
    composites: tuple["CompositeColumnConfig", ...] = ()
    unknown_column_policy: UnknownColumnPolicy = UnknownColumnPolicy.REQUIRE_EXPLICIT

    @property
    def has_matches(self) -> bool:
        return bool(self.matched_headers)

    @property
    def is_complete(self) -> bool:
        return self.has_matches and not self.missing_headers and not self.extra_headers and not self.composites

    @property
    def compatibility_message(self) -> str:
        messages = []
        if self.composites:
            messages.append("Este perfil contém composites ainda não suportadas por este fluxo.")
        if self.missing_headers:
            messages.append(f"Cabeçalhos não encontrados: {', '.join(self.missing_headers)}.")
        if self.extra_headers:
            messages.append(f"Colunas adicionais não conhecidas pelo perfil: {', '.join(self.extra_headers)}. Revise a configuração.")
        return " ".join(messages)
