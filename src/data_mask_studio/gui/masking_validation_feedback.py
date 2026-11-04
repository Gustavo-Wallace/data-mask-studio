"""Transient presentation for validation explanations produced by the masking GUI."""

from dataclasses import dataclass
from typing import TypeVar

from data_mask_studio.i18n import Translator


_MESSAGE_IDS = frozenset((
    "masking.error.no_csv", "masking.error.missing_headers", "masking.error.unknown_columns",
    "masking.package.error.requires_masking", "masking.package.error.confirmation", "profile.partial",
))


@dataclass(frozen=True, slots=True)
class MaskingValidationFeedback:
    message_id: str
    parameters: tuple[tuple[str, str | int | float], ...] = ()
    column_references: tuple[tuple[str, int], ...] = ()
    details: "MaskingValidationFeedback | None" = None
    literal_suffix: str = ""

    def __post_init__(self) -> None:
        # No mutable mappings, model/widget references, or arbitrary objects.
        if (type(self.message_id) is not str or type(self.parameters) is not tuple
                or any(type(pair) is not tuple or len(pair) != 2 or type(pair[0]) is not str
                       or type(pair[1]) not in (str, int, float) for pair in self.parameters)
                or type(self.column_references) is not tuple
                or any(type(pair) is not tuple or len(pair) != 2 or type(pair[0]) is not str
                       or type(pair[1]) is not int for pair in self.column_references)
                or (self.details is not None and not isinstance(self.details, MaskingValidationFeedback))
                or type(self.literal_suffix) is not str):
            raise TypeError("Invalid GUI validation presentation.")

    @property
    def supported(self) -> bool:
        return self.message_id in _MESSAGE_IDS and (self.details is None or self.details.supported)

    def render(self, translator: Translator) -> str:
        parameters = dict(self.parameters)
        if self.column_references:
            parameters["columns"] = ", ".join(translator.text(
                "masking.column.reference", header=header, number=number,
            ) for header, number in self.column_references)
        if self.details is not None:
            parameters["details"] = self.details.render(translator) + self.literal_suffix
        return translator.text(self.message_id, **parameters)


Error = TypeVar("Error", bound=Exception)


def gui_validation_error(error_type: type[Error], feedback: MaskingValidationFeedback, translator: Translator) -> Error:
    """Keep the exact legacy exception class/args; metadata belongs to the GUI."""
    error = error_type(feedback.render(translator))
    setattr(error, "gui_validation_feedback", feedback)
    return error


def validation_feedback(error: Exception) -> MaskingValidationFeedback | None:
    feedback = getattr(error, "gui_validation_feedback", None)
    return feedback if isinstance(feedback, MaskingValidationFeedback) and feedback.supported else None
