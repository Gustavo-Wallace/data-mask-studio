import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from data_mask_studio.anonymization.models import ColumnAction
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.profiles.exceptions import (
    ProfileFormatError,
    ProfileStorageError,
    ProfileValidationError,
)
from data_mask_studio.profiles.models import (
    PROFILES_SCHEMA_VERSION,
    PROFILE_FORMAT_VERSION,
    ConfigurationProfile,
    ProfileColumn,
    UnknownColumnPolicy,
)
from data_mask_studio.profiles.composite_format import parse_composite, serialize_composite, require_fields
from data_mask_studio.profiles.validation import validate_unique_names

PROFILES_FILE_NAME = "profiles.json"
LOGGER = logging.getLogger(__name__)


def default_profiles_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise ProfileStorageError(
            "A pasta local de dados do Windows não está disponível."
        )
    return Path(local_app_data) / "DataMaskStudio" / PROFILES_FILE_NAME


class ProfileRepository:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_profiles_path()

    def load(self) -> list[ConfigurationProfile]:
        if not self.path.exists():
            return []
        try:
            with self.path.open("r", encoding="utf-8") as profile_file:
                document = json.load(profile_file)
            return _parse_document(document)
        except ProfileFormatError as error:
            LOGGER.warning(
                "Arquivo de perfis rejeitado (%s).",
                type(error.__cause__ or error).__name__,
            )
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            LOGGER.warning("Falha ao ler perfis (%s).", type(error).__name__)
            raise ProfileFormatError(
                "O arquivo de perfis está inválido ou não pôde ser lido."
            ) from error

    @staticmethod
    def parse_bytes(data: bytes) -> list[ConfigurationProfile]:
        """Valida um documento de perfis recebido sem acessar o perfil local."""
        try:
            return _parse_document(json.loads(data.decode("utf-8")))
        except ProfileFormatError:
            raise
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ProfileFormatError("O arquivo de perfis possui formato inválido.") from error

    def save(self, profiles: list[ConfigurationProfile]) -> None:
        try:
            validate_unique_names(profiles)
            document = _serialize_document(profiles)
        except ProfileValidationError as error:
            raise ProfileStorageError(str(error)) from error

        temporary_path: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
                json.dump(document, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_path, self.path)
            temporary_path = None
        except OSError as error:
            LOGGER.warning("Falha ao gravar perfis (%s).", type(error).__name__)
            raise ProfileStorageError(
                "Não foi possível salvar os perfis de configuração."
            ) from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass


def _serialize_document(profiles: list[ConfigurationProfile]) -> dict[str, Any]:
    return {
        "schema_version": PROFILES_SCHEMA_VERSION,
        "profiles": [
            {
                "identifier": profile.identifier,
                "name": profile.name,
                "format_version": PROFILE_FORMAT_VERSION,
                "unknown_column_policy": profile.unknown_column_policy.value,
                "composites": [serialize_composite(composite) for composite in profile.composites],
                "created_at": profile.created_at.isoformat(),
                "modified_at": profile.modified_at.isoformat(),
                "columns": [
                    {
                        "header": column.header,
                        "prefix": column.prefix,
                        "normalization_rule": column.normalization_rule.value,
                        "action": column.action.value,
                        "output_name": column.output_name,
                    }
                    for column in profile.columns
                ],
            }
            for profile in profiles
        ],
    }


def _parse_document(document: object) -> list[ConfigurationProfile]:
    if not isinstance(document, dict):
        raise ProfileFormatError("O arquivo de perfis possui formato inválido.")
    schema_version = document.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version not in (1, PROFILES_SCHEMA_VERSION)
    ):
        raise ProfileFormatError("A versão do arquivo de perfis não é suportada.")
    raw_profiles = document.get("profiles")
    if not isinstance(raw_profiles, list):
        raise ProfileFormatError("A lista de perfis é inválida.")
    try:
        if schema_version == 2:
            require_fields(document, {"schema_version", "profiles"})
        elif any(isinstance(item, dict) and item.get("format_version") != 1 for item in raw_profiles):
            raise ProfileFormatError("Versão de perfil incompatível com o container legado.")
        profiles = [_parse_profile(item) for item in raw_profiles]
        validate_unique_names(profiles)
        return profiles
    except (KeyError, TypeError, ValueError, ProfileValidationError) as error:
        raise ProfileFormatError("O arquivo de perfis possui dados inválidos.") from None


def _parse_profile(value: object) -> ConfigurationProfile:
    if not isinstance(value, dict):
        raise TypeError
    version = _required_int(value, "format_version")
    if version not in (1, PROFILE_FORMAT_VERSION):
        raise ProfileFormatError("A versão do perfil não é suportada.")
    if version == 2:
        require_fields(value, {"identifier", "name", "format_version", "created_at", "modified_at",
                               "columns", "composites", "unknown_column_policy"})
        if not isinstance(value["composites"], list):
            raise TypeError
        composites = tuple(parse_composite(item) for item in value["composites"])
        policy = UnknownColumnPolicy(value["unknown_column_policy"])
    else:
        if "composites" in value or "unknown_column_policy" in value:
            raise ProfileFormatError("Semântica v2 não permitida em perfil legado.")
        composites = ()
        policy = UnknownColumnPolicy.REQUIRE_EXPLICIT
    columns = value["columns"]
    if not isinstance(columns, list):
        raise TypeError
    return ConfigurationProfile(
        identifier=_required_string(value, "identifier"),
        name=_required_string(value, "name"),
        format_version=PROFILE_FORMAT_VERSION,
        created_at=_required_datetime(value, "created_at"),
        modified_at=_required_datetime(value, "modified_at"),
        columns=tuple(_parse_column(column, version) for column in columns),
        composites=composites,
        unknown_column_policy=policy,
    )


def _parse_column(value: object, version: int = 1) -> ProfileColumn:
    if not isinstance(value, dict):
        raise TypeError
    if version == 2:
        require_fields(value, {"header", "prefix", "normalization_rule", "action", "output_name"})
        if not isinstance(value["action"], str):
            raise TypeError
    anonymize = value["anonymize"] if version == 1 else value["action"] == "mask"
    if not isinstance(anonymize, bool):
        raise TypeError
    raw_action = value.get("action")
    action = (
        ColumnAction(raw_action)
        if raw_action is not None
        else (ColumnAction.MASK if anonymize else ColumnAction.PRESERVE)
    )
    if raw_action is not None and anonymize != (action is ColumnAction.MASK):
        raise ValueError
    try:
        raw_normalization_rule = value["normalization_rule"]
    except KeyError:
        if action is not ColumnAction.PRESERVE:
            raise
        normalization_rule = NormalizationRule.EXACT
    else:
        if not isinstance(raw_normalization_rule, str):
            raise TypeError
        normalization_rule = NormalizationRule(raw_normalization_rule)
    return ProfileColumn(
        header=_required_string(value, "header"),
        prefix=_required_string(value, "prefix"),
        normalization_rule=normalization_rule,
        anonymize=anonymize,
        action=action,
        output_name=(
            _required_string(value, "output_name") if "output_name" in value else ""
        ),
    )


def _required_string(value: dict[str, Any], key: str) -> str:
    result = value[key]
    if not isinstance(result, str):
        raise TypeError
    return result


def _required_int(value: dict[str, Any], key: str) -> int:
    result = value[key]
    if not isinstance(result, int) or isinstance(result, bool):
        raise TypeError
    return result


def _required_datetime(value: dict[str, Any], key: str) -> datetime:
    return datetime.fromisoformat(_required_string(value, key))
