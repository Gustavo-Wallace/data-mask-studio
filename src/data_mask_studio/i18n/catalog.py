"""Strict, explicit UTF-8 resources; English defines the complete key contract."""

from collections.abc import Mapping
from importlib import resources
import json
import re
from string import Formatter
from types import MappingProxyType

FALLBACK_LOCALE = "en"
SUPPORTED_LOCALES = (FALLBACK_LOCALE, "pt-BR")
_MESSAGE_ID = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+", re.ASCII)
_PARAMETER = re.compile(r"[a-z][a-z0-9_]*", re.ASCII)


class LocalizationError(ValueError):
    """Fixed diagnostics must never interpolate raw resources or parameters."""


class CatalogError(LocalizationError):
    pass


class MissingMessageError(LocalizationError):
    pass


class MessageFormatError(LocalizationError):
    pass


def placeholder_names(template: str) -> frozenset[str]:
    """Only {named} fields; no attributes, indexing, conversions or format specs."""
    names = set()
    try:
        for _, name, spec, conversion in Formatter().parse(template):
            if name is None:
                continue
            if not _PARAMETER.fullmatch(name) or spec or conversion is not None:
                raise ValueError
            names.add(name)
    except ValueError:
        raise CatalogError("Invalid message template.") from None
    return frozenset(names)


def validate_catalogs(catalogs: Mapping[str, Mapping[str, str]]) -> None:
    """Reject partial coverage/placeholder drift, rather than hiding bad instructions."""
    if not isinstance(catalogs, Mapping) or set(catalogs) != set(SUPPORTED_LOCALES):
        raise CatalogError("Invalid catalog locales.")
    canonical = catalogs[FALLBACK_LOCALE]
    if not isinstance(canonical, Mapping) or not canonical:
        raise CatalogError("Invalid canonical catalog.")
    fields = {}
    for locale in SUPPORTED_LOCALES:
        messages = catalogs[locale]
        if not isinstance(messages, Mapping) or set(messages) != set(canonical):
            raise CatalogError("Incomplete translation coverage.")
        for identifier, template in messages.items():
            if (not isinstance(identifier, str) or not _MESSAGE_ID.fullmatch(identifier)
                    or not isinstance(template, str) or not template.strip()):
                raise CatalogError("Invalid catalog message.")
            try:
                template.encode("utf-8")
            except UnicodeError:
                raise CatalogError("Invalid catalog text encoding.") from None
            placeholders = placeholder_names(template)
            if locale == FALLBACK_LOCALE:
                fields[identifier] = placeholders
            elif fields[identifier] != placeholders:
                raise CatalogError("Inconsistent message parameters.")
    for identifier, placeholders in fields.items():
        base, category = identifier.rsplit(".", 1)
        if category in {"one", "other"}:
            if (not {f"{base}.one", f"{base}.other"} <= canonical.keys()
                    or "count" not in placeholders
                    or fields[f"{base}.one"] != fields[f"{base}.other"]):
                raise CatalogError("Incomplete count message pair.")


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def load_catalogs() -> Mapping[str, Mapping[str, str]]:
    """Load only bundled resources, independent of cwd, user directories or Qt.

    Missing/malformed installed resources are errors, not partial translations.
    The returned mappings are deeply read-only and never cached mutable state.
    """
    catalogs = {}
    try:
        root = resources.files("data_mask_studio.i18n").joinpath("catalogs")
        for locale in SUPPORTED_LOCALES:
            raw = root.joinpath(f"{locale}.json").read_bytes()
            document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_fields)
            if (not isinstance(document, dict)
                    or set(document) != {"schema_version", "locale", "messages"}
                    or type(document["schema_version"]) is not int
                    or document["schema_version"] != 1 or document["locale"] != locale):
                raise ValueError
            catalogs[locale] = document["messages"]
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        raise CatalogError("Unable to load application message catalogs.") from None
    validate_catalogs(catalogs)
    return MappingProxyType({
        locale: MappingProxyType(dict(messages)) for locale, messages in catalogs.items()
    })
