"""No Qt, OS detection, persistence or mutable global locale."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from html import escape

from data_mask_studio.i18n.catalog import (
    FALLBACK_LOCALE, SUPPORTED_LOCALES, MessageFormatError, MissingMessageError,
    load_catalogs, placeholder_names,
)


@dataclass(frozen=True, slots=True, init=False)
class Translator:
    locale: str
    _catalogs: Mapping[str, Mapping[str, str]] = field(repr=False)

    def __init__(self, locale: str = FALLBACK_LOCALE) -> None:
        # Receives an effective locale, not a preference such as "system".
        # Exact unsupported values fall back to English; never query the OS.
        effective = locale if isinstance(locale, str) and locale in SUPPORTED_LOCALES else FALLBACK_LOCALE
        object.__setattr__(self, "locale", effective)
        object.__setattr__(self, "_catalogs", load_catalogs())

    def text(self, message_id: str, /, **parameters: str | int | float) -> str:
        messages = self._catalogs[self.locale]
        if not isinstance(message_id, str) or message_id not in messages:
            raise MissingMessageError("Unknown application message identifier.")
        template = messages[message_id]
        if (set(parameters) != placeholder_names(template)
                or any(type(value) not in (str, int, float) for value in parameters.values())):
            raise MessageFormatError("Invalid application message parameters.")
        try:
            return template.format_map(parameters)
        except (ValueError, TypeError, KeyError, OverflowError):
            raise MessageFormatError("Unable to format application message.") from None

    def html(self, message_id: str, /, **parameters: str | int | float) -> str:
        """Safe HTML text fragment, not markup from the catalog or parameters."""
        return escape(self.text(message_id, **parameters), quote=True)

    def plural(self, message_id: str, count: int, /, **parameters: str | int | float) -> str:
        """Singular only for 1; nonnegative integral operation counts only."""
        if type(count) is not int or count < 0 or "count" in parameters:
            raise MessageFormatError("Invalid application message count.")
        if not isinstance(message_id, str):
            raise MissingMessageError("Unknown application message identifier.")
        category = "one" if count == 1 else "other"
        return self.text(f"{message_id}.{category}", count=count, **parameters)
