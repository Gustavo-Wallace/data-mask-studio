import unicodedata
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from data_mask_studio.anonymization.models import ColumnAction
from data_mask_studio.anonymization.prefix_rules import validate_prefix
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.profiles.exceptions import ProfileValidationError
from data_mask_studio.profiles.models import (
    PROFILE_FORMAT_VERSION,
    ConfigurationProfile,
    ProfileColumn,
)


def validate_profile_name(name: str) -> str:
    if not isinstance(name, str):
        raise ProfileValidationError("O nome do perfil é inválido.")
    if any(unicodedata.category(character) == "Cc" for character in name):
        raise ProfileValidationError(
            "O nome do perfil não pode conter caracteres de controle."
        )
    normalized = name.strip()
    if len(normalized) < 3:
        raise ProfileValidationError(
            "O nome do perfil deve ter entre 3 e 60 caracteres."
        )
    if len(normalized) > 60:
        raise ProfileValidationError(
            "O nome do perfil deve ter entre 3 e 60 caracteres."
        )
    return normalized


def profile_name_key(name: str) -> str:
    return name.casefold()


def validate_profile(profile: ConfigurationProfile) -> None:
    try:
        UUID(profile.identifier)
    except (ValueError, TypeError, AttributeError) as error:
        raise ProfileValidationError("O identificador do perfil é inválido.") from error
    if validate_profile_name(profile.name) != profile.name:
        raise ProfileValidationError("O nome do perfil possui espaços nas extremidades.")
    if profile.format_version != PROFILE_FORMAT_VERSION:
        raise ProfileValidationError("A versão do perfil não é suportada.")
    if not isinstance(profile.created_at, datetime) or not isinstance(
        profile.modified_at, datetime
    ):
        raise ProfileValidationError("As datas do perfil são inválidas.")
    if not profile.columns:
        raise ProfileValidationError("O perfil não possui colunas configuradas.")
    if all(column.action is ColumnAction.EXCLUDE for column in profile.columns):
        raise ProfileValidationError(
            "O perfil precisa manter ao menos uma coluna no arquivo de saída."
        )

    seen_headers: set[str] = set()
    selected_prefixes: set[str] = set()
    for column in profile.columns:
        validate_profile_column(column)
        if column.header in seen_headers:
            raise ProfileValidationError(
                "O perfil possui cabeçalhos duplicados."
            )
        seen_headers.add(column.header)
        if (
            column.action is ColumnAction.MASK
            and column.prefix in selected_prefixes
        ):
            raise ProfileValidationError("O perfil possui prefixos duplicados.")
        if column.action is ColumnAction.MASK:
            selected_prefixes.add(column.prefix)


def validate_profile_column(column: ProfileColumn) -> None:
    if not isinstance(column.header, str) or not column.header:
        raise ProfileValidationError("Um cabeçalho do perfil é inválido.")
    if not isinstance(column.prefix, str):
        raise ProfileValidationError("Um prefixo do perfil é inválido.")
    if column.action is ColumnAction.MASK:
        prefix_error = validate_prefix(column.prefix)
        if prefix_error is not None:
            raise ProfileValidationError(f"Prefixo inválido no perfil: {prefix_error}")
    if not isinstance(column.normalization_rule, NormalizationRule):
        raise ProfileValidationError(
            "Uma regra de normalização do perfil é inválida."
        )
    if not isinstance(column.action, ColumnAction):
        raise ProfileValidationError("A ação de uma coluna do perfil é inválida.")


def validate_unique_names(profiles: Sequence[ConfigurationProfile]) -> None:
    names: set[str] = set()
    identifiers: set[str] = set()
    for profile in profiles:
        validate_profile(profile)
        name_key = profile_name_key(profile.name)
        if name_key in names:
            raise ProfileValidationError("Existem perfis com nomes duplicados.")
        if profile.identifier in identifiers:
            raise ProfileValidationError("Existem perfis com identificadores duplicados.")
        names.add(name_key)
        identifiers.add(profile.identifier)
