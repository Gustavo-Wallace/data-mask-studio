from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4
from typing import TYPE_CHECKING
from pathlib import Path
from data_mask_studio.csv_tools.models import CSVInspectionResult
from data_mask_studio.csv_tools.source_binding import (
    SourceColumnRef, SourceBindingError, bind_source, source_ref_at, may_be_synthetic_name,
)

if TYPE_CHECKING:
    from data_mask_studio.processing.models import ProcessingPlan

from data_mask_studio.anonymization import (
    ColumnAction,
    ColumnConfig,
    validate_configuration,
)
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.profiles.exceptions import ProfileValidationError, ProfileStorageError
from data_mask_studio.profiles.models import (
    PROFILE_FORMAT_VERSION,
    ConfigurationProfile,
    ProfileApplicationResult,
    ProfileColumn,
    UnknownColumnPolicy,
)
from data_mask_studio.processing.models import CompositeColumnConfig
from data_mask_studio.profiles.repository import ProfileRepository
from data_mask_studio.environment import guarded
from data_mask_studio.profiles.validation import profile_name_key, validate_profile_name


class ProfileService:
    def __init__(self, repository: ProfileRepository) -> None:
        self.repository = repository

    def list_profiles(self) -> list[ConfigurationProfile]:
        return sorted(self.repository.load(), key=lambda item: item.name.casefold())

    def build_plan(
        self, profile: ConfigurationProfile, inspection: "CSVInspectionResult",
    ) -> "ProcessingPlan":
        """Resolve políticas físicas e delega todo binding ao planner de domínio."""
        from data_mask_studio.processing.planner import PlanningError

        if profile.unknown_column_policy is not UnknownColumnPolicy.REQUIRE_EXPLICIT:
            raise PlanningError("Política de colunas desconhecidas não suportada.")
        application = self.apply(profile, inspection)
        # A capacidade do consumidor não faz parte da compatibilidade estrutural.
        structural = replace(application, composites=())
        if not structural.is_complete:
            raise PlanningError(structural.compatibility_message or "Perfil incompatível com o CSV.")
        configurations = [ColumnConfig(
            column.header, prefix=column.prefix, action=column.action,
            normalization_rule=column.normalization_rule, output_name=column.output_name,
        ) for column in application.configurations]
        return self.build_configuration_plan(inspection, configurations, profile.composites)

    @staticmethod
    def build_configuration_plan(
        inspection: "CSVInspectionResult", configurations: Sequence[ColumnConfig],
        composites: Sequence[CompositeColumnConfig] = (),
    ) -> "ProcessingPlan":
        """Mesmo planner para estado não salvo da GUI e perfis aplicados."""
        from data_mask_studio.processing.planner import build_processing_plan

        return build_processing_plan(inspection, configurations, composites)

    @guarded(lambda self, *args, **kwargs: self.repository.path.parent, error_type=ProfileStorageError)
    def create(
        self, name: str, configurations: Sequence[ColumnConfig], *,
        composites: Sequence[CompositeColumnConfig] = (),
        unknown_column_policy: UnknownColumnPolicy = UnknownColumnPolicy.REQUIRE_EXPLICIT,
        inspection: CSVInspectionResult | None = None,
    ) -> ConfigurationProfile:
        profiles = self.repository.load()
        normalized_name = validate_profile_name(name)
        self._ensure_unique_name(profiles, normalized_name)
        columns = _selected_profile_columns(configurations, has_composites=bool(composites), inspection=inspection)
        now = datetime.now(timezone.utc)
        profile = ConfigurationProfile(
            identifier=str(uuid4()),
            name=normalized_name,
            format_version=PROFILE_FORMAT_VERSION,
            created_at=now,
            modified_at=now,
            columns=columns,
            composites=tuple(composites),
            unknown_column_policy=unknown_column_policy,
        )
        self.repository.save([*profiles, profile])
        return profile

    @guarded(lambda self, *args, **kwargs: self.repository.path.parent, error_type=ProfileStorageError)
    def update(
        self,
        identifier: str,
        configurations: Sequence[ColumnConfig],
        *,
        composites: Sequence[CompositeColumnConfig] | None = None,
        unknown_column_policy: UnknownColumnPolicy | None = None,
        inspection: CSVInspectionResult | None = None,
    ) -> ConfigurationProfile:
        profiles = self.repository.load()
        index = _profile_index(profiles, identifier)
        current = profiles[index]
        selected_composites = current.composites if composites is None else tuple(composites)
        updated = replace(
            current,
            modified_at=datetime.now(timezone.utc),
            columns=_selected_profile_columns(configurations, has_composites=bool(selected_composites), inspection=inspection),
            composites=selected_composites,
            unknown_column_policy=(current.unknown_column_policy if unknown_column_policy is None else unknown_column_policy),
        )
        profiles[index] = updated
        self.repository.save(profiles)
        return updated

    @guarded(lambda self, *args, **kwargs: self.repository.path.parent, error_type=ProfileStorageError)
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

    @guarded(lambda self, *args, **kwargs: self.repository.path.parent, error_type=ProfileStorageError)
    def delete(self, identifier: str) -> None:
        profiles = self.repository.load()
        index = _profile_index(profiles, identifier)
        del profiles[index]
        self.repository.save(profiles)

    def apply(
        self,
        profile: ConfigurationProfile,
        headers: Sequence[str] | CSVInspectionResult,
    ) -> ProfileApplicationResult:
        inspected = isinstance(headers, CSVInspectionResult)
        inspection = headers if inspected else CSVInspectionResult(Path(), "", "", list(headers))
        headers = inspection.headers
        bound = {}
        missing = []
        for column in profile.columns:
            reference = column.reference
            try:
                # Null references preserve legacy uncertainty; never infer real
                # provenance for generated-looking names or duplicate headers.
                if reference is None:
                    if may_be_synthetic_name(column.header) or headers.count(column.header) != 1:
                        raise SourceBindingError("A origem do perfil exige revisão.")
                    reference = SourceColumnRef(column.header)
                if not inspected and (reference.is_synthetic or may_be_synthetic_name(reference.header)):
                    raise SourceBindingError("A origem exige inspeção do CSV.")
                index = bind_source(reference, inspection)
                if index in bound:
                    raise SourceBindingError("Políticas repetidas para a mesma origem.")
                bound[index] = column
            except SourceBindingError:
                missing.append(column.header)
        matched = tuple(header for index, header in enumerate(headers) if index in bound)
        configurations = tuple(
            bound.get(
                index,
                ProfileColumn(
                    header=header,
                    prefix="",
                    normalization_rule=NormalizationRule.EXACT,
                    action=ColumnAction.PRESERVE,
                ),
            )
            for index, header in enumerate(headers)
        )
        extra = tuple(header for index, header in enumerate(headers) if index not in bound)
        return ProfileApplicationResult(configurations, matched, tuple(missing), extra,
                                        profile.composites, profile.unknown_column_policy)

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
    *, has_composites: bool = False,
    inspection: CSVInspectionResult | None = None,
) -> tuple[ProfileColumn, ...]:
    headers = [config.header for config in configurations]
    if inspection is not None and headers != inspection.headers:
        raise ProfileValidationError("Configuração incompatível com a inspeção de origem.")
    if inspection is None and any(may_be_synthetic_name(h) or headers.count(h) != 1 for h in headers):
        raise ProfileValidationError("Inspecione o CSV para confirmar a proveniência das colunas.")
    if not (has_composites and configurations and all(c.action is ColumnAction.EXCLUDE for c in configurations)):
        validation = validate_configuration(configurations)
        if not validation.is_valid:
            raise ProfileValidationError(
                validation.error_message or "A configuração atual é inválida."
            )
    return tuple(
        ProfileColumn(
            reference=source_ref_at(inspection, index) if inspection is not None else SourceColumnRef(configuration.header),
            header=configuration.header,
            prefix=configuration.prefix,
            normalization_rule=configuration.normalization_rule,
            action=configuration.action,
            output_name=configuration.output_name,
        )
        for index, configuration in enumerate(configurations)
    )


def _profile_index(profiles: Sequence[ConfigurationProfile], identifier: str) -> int:
    for index, profile in enumerate(profiles):
        if profile.identifier == identifier:
            return index
    raise ProfileValidationError("O perfil selecionado não existe mais.")
