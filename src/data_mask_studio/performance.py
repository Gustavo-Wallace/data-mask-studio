import time
from collections import Counter, OrderedDict
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from typing import Generic, TypeVar


@dataclass(frozen=True, slots=True)
class PerformanceSettings:
    mapping_batch_size: int = 1_000
    restoration_cache_limit: int = 4_096
    progress_row_interval: int = 250
    progress_time_interval: float = 0.20
    io_buffer_size: int = 1_048_576
    restoration_window_rows: int = 5_000
    sqlite_lookup_batch_size: int = 400


BALANCED_SETTINGS = PerformanceSettings()


@dataclass(frozen=True, slots=True)
class ProcessingMetrics:
    elapsed_seconds: float
    rows_per_second: float
    estimated_remaining_seconds: float | None


@dataclass(slots=True)
class RestorationMetrics:
    cache_hits: int = 0
    cache_misses: int = 0
    sqlite_queries: int = 0
    codes_returned: int = 0
    decryptions: int = 0
    connections_opened: int = 0
    query_seconds: float = 0.0
    decryption_seconds: float = 0.0
    writing_seconds: float = 0.0
    codes_returned_per_query: list[int] = field(default_factory=list)

    @property
    def cache_hit_rate(self) -> float:
        total = self.cache_hits + self.cache_misses
        return self.cache_hits / total if total else 0.0

    def to_safe_dict(self) -> dict[str, object]:
        result_sizes = Counter(self.codes_returned_per_query)
        return {
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "cache_hit_rate": self.cache_hit_rate,
            "sqlite_queries": self.sqlite_queries,
            "codes_returned": self.codes_returned,
            "decryptions": self.decryptions,
            "connections_opened": self.connections_opened,
            "query_seconds": self.query_seconds,
            "decryption_seconds": self.decryption_seconds,
            "writing_seconds": self.writing_seconds,
            "codes_returned_per_query": {
                "minimum": min(self.codes_returned_per_query, default=0),
                "maximum": max(self.codes_returned_per_query, default=0),
                "average": (
                    sum(self.codes_returned_per_query)
                    / len(self.codes_returned_per_query)
                    if self.codes_returned_per_query
                    else 0.0
                ),
                "distribution": dict(sorted(result_sizes.items())),
            },
        }


def calculate_metrics(
    rows: int,
    elapsed_seconds: float,
    total_rows: int | None = None,
) -> ProcessingMetrics:
    elapsed = max(0.0, elapsed_seconds)
    rate = rows / elapsed if rows > 0 and elapsed > 0 else 0.0
    remaining = None
    if (
        total_rows is not None
        and total_rows >= rows
        and rows >= 100
        and elapsed >= 1.0
        and rate > 0
    ):
        remaining = (total_rows - rows) / rate
    return ProcessingMetrics(elapsed, rate, remaining)


class ProgressLimiter:
    def __init__(self, settings: PerformanceSettings = BALANCED_SETTINGS) -> None:
        self._row_interval = settings.progress_row_interval
        self._time_interval = settings.progress_time_interval
        self._last_rows = 0
        self._last_time = time.monotonic()

    def should_emit(self, rows: int, *, force: bool = False) -> bool:
        now = time.monotonic()
        if not force and rows - self._last_rows < self._row_interval:
            return False
        if not force and now - self._last_time < self._time_interval:
            return False
        self._last_rows = rows
        self._last_time = now
        return True


K = TypeVar("K")
V = TypeVar("V")


class BoundedCache(MutableMapping[K, V], Generic[K, V]):
    """Cache LRU efêmero com crescimento estritamente limitado."""

    def __init__(self, limit: int) -> None:
        if limit <= 0:
            raise ValueError("O limite do cache deve ser positivo.")
        self.limit = limit
        self._values: OrderedDict[K, V] = OrderedDict()

    def __getitem__(self, key: K) -> V:
        value = self._values[key]
        self._values.move_to_end(key)
        return value

    def __setitem__(self, key: K, value: V) -> None:
        self._values[key] = value
        self._values.move_to_end(key)
        while len(self._values) > self.limit:
            self._values.popitem(last=False)

    def __delitem__(self, key: K) -> None:
        del self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def clear(self) -> None:
        self._values.clear()
