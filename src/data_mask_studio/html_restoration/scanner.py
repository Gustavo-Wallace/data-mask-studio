import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from data_mask_studio.consultant.code_parser import is_valid_code
from data_mask_studio.html_restoration.exceptions import (
    HTMLRestorationCancelled,
    HTMLRestorationError,
)
from data_mask_studio.html_restoration.inspector import python_encoding

CHUNK_SIZE = 64 * 1024
OVERLAP_SIZE = 96
MAX_CANDIDATE_LENGTH = 64
TOKEN_CHARACTER_PATTERN = re.compile(r"[A-Za-z0-9_+/=-]")
BOUNDARY_CHARACTER_PATTERN = re.compile(r"[A-Za-z0-9_]")
CODE_CANDIDATE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"[A-Za-z][A-Za-z0-9_]{1,23}-[A-Za-z0-9_]{1,32}"
    r"(?![A-Za-z0-9_=])"
)

CancellationCheck = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class HTMLSegment:
    text: str
    left_context: str
    right_context: str
    processed_bytes: int
    total_bytes: int

    def guarded_text(self) -> str:
        left = "A" if _is_boundary_character(self.left_context) else " "
        right = "A" if _is_boundary_character(self.right_context) else " "
        return f"{left}{self.text}{right}"


def iter_html_segments(
    path: Path,
    encoding: str,
    *,
    should_cancel: CancellationCheck | None = None,
) -> Iterator[HTMLSegment]:
    total_bytes = path.stat().st_size
    buffer = ""
    left_context = ""
    try:
        with path.open(
            "r", encoding=python_encoding(encoding), newline=""
        ) as html_file:
            while True:
                _raise_if_cancelled(should_cancel)
                chunk = html_file.read(CHUNK_SIZE)
                if not chunk:
                    if buffer:
                        yield HTMLSegment(
                            buffer,
                            left_context,
                            "",
                            total_bytes,
                            total_bytes,
                        )
                    break
                buffer += chunk
                if len(buffer) <= OVERLAP_SIZE:
                    continue
                nominal_cut = len(buffer) - OVERLAP_SIZE
                cut = _safe_cut(buffer, nominal_cut)
                if cut <= 0:
                    continue
                segment_text = buffer[:cut]
                right_context = buffer[cut] if cut < len(buffer) else ""
                try:
                    processed_bytes = min(int(html_file.tell()), total_bytes)
                except OSError:
                    processed_bytes = 0
                yield HTMLSegment(
                    segment_text,
                    left_context,
                    right_context,
                    processed_bytes,
                    total_bytes,
                )
                left_context = segment_text[-1]
                buffer = buffer[cut:]
    except HTMLRestorationCancelled:
        raise
    except (OSError, UnicodeError) as error:
        raise HTMLRestorationError(
            "Não foi possível processar a codificação do arquivo HTML."
        ) from error


def iter_candidates(segment: HTMLSegment) -> Iterator[tuple[re.Match[str], str]]:
    guarded = segment.guarded_text()
    for match in CODE_CANDIDATE_PATTERN.finditer(guarded):
        normalized = match.group(0).upper()
        yield match, normalized


def replace_candidates(
    segment: HTMLSegment,
    replacement: Callable[[str, str], str],
) -> str:
    guarded = segment.guarded_text()

    def replace(match: re.Match[str]) -> str:
        original = match.group(0)
        return replacement(original, original.upper())

    return CODE_CANDIDATE_PATTERN.sub(replace, guarded)[1:-1]


def is_valid_candidate(normalized_code: str) -> bool:
    return is_valid_code(normalized_code)


def _safe_cut(buffer: str, nominal_cut: int) -> int:
    start = nominal_cut
    while start > 0 and _is_token_character(buffer[start - 1]):
        start -= 1
    end = nominal_cut
    while end < len(buffer) and _is_token_character(buffer[end]):
        end += 1
    if end - start <= MAX_CANDIDATE_LENGTH:
        return start
    return nominal_cut


def _is_token_character(value: str) -> bool:
    return bool(value) and TOKEN_CHARACTER_PATTERN.fullmatch(value) is not None


def _is_boundary_character(value: str) -> bool:
    return bool(value) and BOUNDARY_CHARACTER_PATTERN.fullmatch(value) is not None


def _raise_if_cancelled(should_cancel: CancellationCheck | None) -> None:
    if should_cancel is not None and should_cancel():
        raise HTMLRestorationCancelled("A restauração de HTML foi cancelada.")
