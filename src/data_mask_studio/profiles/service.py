from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from data_mask_studio.anonymization import ColumnConfig, validate_configuration
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.profiles.exceptions import ProfileValidationError
from data_mask_studio.profiles.models import (
    PROFILE_FORMAT_VERSION,
    ConfigurationProfile,
    ProfileApplicationResult,
    ProfileColumn,
)
from data_mask_studio.profiles.repository import ProfileRepository
from data_mask_studio.profiles.validation import profile_name_key, validate_profile_name


class ProfileService:
    def __init__(self, repository: ProfileRepository) -> None:
        self.repository = repository

    def list_profiles(self) -> list[ConfigurationProfile]:
        return sorted(self.repository.load(), key=lambda item: item.name.casefold())

    def create(
        self, name: str, configurations: Sequence[ColumnConfig]
    ) -> ConfigurationProfile:
        profiles = self.repository.load()
        normalized_name = validate_profile_name(name)
        self._ensure_unique_name(profiles, normalized_name)
        columns = _selected_profile_columns(configurations)
        now = datetime.now(timezone.utc)
        profile = ConfigurationProfile(
            identifier=str(uuid4()),
            name=normalized_name,
            format_version=PROFILE_FORMAT_VERSION,
            created_at=now,
            modified_at=now,
            columns=columns,
        )
        self.repository.save([*profiles, profile])
        return profile

    def update(
        self,
        identifier: str,
        configurations: Sequence[ColumnConfig],
    ) -> ConfigurationProfile:
        profiles = self.repository.load()
        index = _profile_index(profiles, identifier)
        current = profiles[index]
        updated = replace(
            current,
            modified_at=datetime.now(timezone.utc),
            columns=_selected_profile_columns(configurations),
        )
        profiles[index] = updated
        self.repository.save(profiles)
        return updated

    def rename(self, identifier: str, name: str) -> ConfigurationProfile:
        profiles = self.repository.load()
        index = _profile_index(profiles, identifier)
        normalized_name = validate_profile_name(name)
        self._ensure_unique_name(profiles, normalized_name, excluding=identifier)
        renamed = replace(
            profiles[index],
            name=normalized_name,
            modified_at=datetime.now(timezone.utc),
        )
        profiles[index] = renamed
        self.repository.save(profiles)
        return renamed

    def delete(self, identifier: str) -> None:
        profiles = self.repository.load()
        index = _profile_index(profiles, identifier)
        del profiles[index]
        self.repository.save(profiles)

    def apply(
        self,
        profile: ConfigurationProfile,
        headers: Sequence[str],
    ) -> ProfileApplicationResult:
        profile_columns = {column.header: column for column in profile.columns}
        matched = tuple(header for header in headers if header in profile_columns)
        missing = tuple(
            column.header for column in profile.columns if column.header not in headers
        )
        configurations = tuple(
            profile_columns.get(
                header,
                ProfileColumn(
                    header=header,
                    prefix="",
                    normalization_rule=NormalizationRule.EXACT,
                    anonymize=False,
                ),
            )
            for header in headers
        )
        return ProfileApplicationResult(configurations, matched, missing)

    @staticmethod
    def _ensure_unique_name(
        profiles: Sequence[ConfigurationProfile],
        name: str,
        *,
        excluding: str | None = None,
    ) -> None:
        key = profile_name_key(name)
        if any(
            profile.identifier != excluding and profile_name_key(profile.name) == key
            for profile in profiles
        ):
            raise ProfileValidationError("Já existe um perfil com esse nome.")


def _selected_profile_columns(
    configurations: Sequence[ColumnConfig],
) -> tuple[ProfileColumn, ...]:
    validation = validate_configuration(configurations)
    if not validation.is_valid:
        raise ProfileValidationError(
            validation.error_message or "A configuração atual é inválida."
        )
    return tuple(
        ProfileColumn(
            header=configuration.header,
            prefix=configuration.prefix,
            normalization_rule=configuration.normalization_rule,
            anonymize=True,
        )
        for configuration in configurations
        if configuration.anonymize
    )


def _profile_index(profiles: Sequence[ConfigurationProfile], identifier: str) -> int:
    for index, profile in enumerate(profiles):
        if profile.identifier == identifier:
            return index
    raise ProfileValidationError("O perfil selecionado não existe mais.")
