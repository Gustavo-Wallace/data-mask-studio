"""Ephemeral history of physical column settings, never of executed operations."""

from dataclasses import dataclass

from data_mask_studio.anonymization.models import ColumnAction
from data_mask_studio.normalization import NormalizationRule


@dataclass(frozen=True, slots=True)
class PhysicalColumnState:
    input_index: int
    header: str
    action: ColumnAction
    prefix: str
    normalization_rule: NormalizationRule
    output_name: str


@dataclass(frozen=True, slots=True)
class ConfigurationSnapshot:
    # Reloading even the same CSV creates a different physical-column context.
    generation: int
    columns: tuple[PhysicalColumnState, ...]
    manually_changed_rows: frozenset[int]
    unreviewed_sources: frozenset[int]
    dirty: bool


class ConfigurationHistory:
    LIMIT = 30

    def __init__(self) -> None:
        self._undo: list[tuple[ConfigurationSnapshot, ConfigurationSnapshot]] = []
        self._redo: list[tuple[ConfigurationSnapshot, ConfigurationSnapshot]] = []

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()

    def record(self, before: ConfigurationSnapshot, after: ConfigurationSnapshot) -> None:
        if before.generation != after.generation:
            self.clear()
        elif before != after:
            self._redo.clear()
            self._undo.append((before, after))
            del self._undo[:-self.LIMIT]

    def undo(self, current: ConfigurationSnapshot) -> ConfigurationSnapshot | None:
        if not self._undo:
            return None
        before, after = self._undo[-1]
        if current != after:
            self.clear()  # An untracked mutation must never be overwritten.
            return None
        self._redo.append(self._undo.pop())
        return before

    def redo(self, current: ConfigurationSnapshot) -> ConfigurationSnapshot | None:
        if not self._redo:
            return None
        before, after = self._redo[-1]
        if current != before:
            self.clear()
            return None
        self._undo.append(self._redo.pop())
        return after
