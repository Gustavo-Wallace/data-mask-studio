"""Source-position HTML contexts; never serialize the document or parse it with regex."""

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from html import escape, unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlsplit

from data_mask_studio.html_restoration.exceptions import HTMLUnsupportedContextError
from data_mask_studio.html_restoration.scanner import (
    CancellationCheck,
    HTMLSegment,
    is_valid_candidate,
    iter_candidates,
    iter_html_segments,
)

MAX_PENDING_CHARACTERS = 4 * 1024 * 1024
VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
# pre/listing discard an initial LF in browsers, which cannot be preserved by
# ordinary character escaping. Foreign content has different parsing rules.
SPECIAL = frozenset(
    "script style textarea title xmp iframe noembed noframes noscript "
    "plaintext svg math template pre listing".split()
)
SAFE_ATTRIBUTES = frozenset({"title", "alt", "placeholder", "aria-label", "aria-description"})
SPACE = " \t\n\r\f"


def unsupported() -> NoReturn:
    raise HTMLUnsupportedContextError(
        "A restauração encontrou um token em um contexto HTML que não pode ser restaurado com segurança."
    )


@dataclass(frozen=True)
class ContextSpan:
    start: int
    end: int
    quote: str = ""
    url: bool = False


@dataclass(frozen=True)
class ContextSegment(HTMLSegment):
    spans: tuple[ContextSpan, ...] = ()

    def context(self, start: int, end: int) -> ContextSpan:
        # Spans and token matches both remain in SOURCE coordinates.
        for span in self.spans:
            if span.start <= start and end <= span.end:
                return span
        unsupported()


class _Contexts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.position = 0
        self.spans: list[ContextSpan] = []
        self.stack: list[str] = []
        self.uncertain = False
        self.plaintext = False

    def updatepos(self, i, j):
        self.position += j - i
        return super().updatepos(i, j)

    def blocked(self):
        return self.uncertain or self.plaintext or any(tag in SPECIAL for tag in self.stack)

    def handle_data(self, data):
        # HTMLParser can report malformed markup as data. Do not grant that
        # fallback permission to insert restored text into a browser context.
        if "<" in data:
            self.uncertain = True
        if not self.blocked():
            self.spans.append(ContextSpan(self.position, self.position + len(data)))

    def handle_starttag(self, tag, attrs):
        self._start(tag)

    def handle_startendtag(self, tag, attrs):
        # In HTML (not XML), '/>' does not close non-void HTML elements.
        self._start(tag)

    def _start(self, tag):
        raw = self.get_starttag_text()
        spans = _quoted_values(raw, tag, self.position)
        if spans is None:
            self.uncertain = True
        elif not self.blocked() and tag not in SPECIAL:
            self.spans.extend(spans)
        if tag == "plaintext":
            self.plaintext = True
        if tag not in VOID:
            self.stack.append(tag)
            if len(self.stack) > 4096:
                unsupported()

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        elif tag in self.stack:
            if self.blocked():
                self.uncertain = True
            self.stack = self.stack[:self.stack.index(tag)]

    def parse_endtag(self, i):
        # Reject parser recovery for malformed end tags. Python versions differ
        # in how they consume bogus end-tag attributes, especially quoted '>'.
        end = self.rawdata.find(">", i + 2)
        if end >= 0:
            name = self.rawdata[i + 2:end].rstrip(SPACE)
            if not name or not all(
                char.isascii() and (char.isalnum() or char in "-:") for char in name
            ):
                self.uncertain = True
        return super().parse_endtag(i)


def _quoted_values(raw: str, tag: str, offset: int) -> list[ContextSpan] | None:
    """Strict lexical walk of a COMPLETE start tag supplied by HTMLParser.

    Only quoted values receive spans; names, unquoted values, declarations
    and parser recovery paths never do. This is not a second HTML parser.
    """
    i = 1
    while i < len(raw) and raw[i] not in SPACE + "/>":
        i += 1
    spans = []
    names = set()
    while i < len(raw):
        if raw[i:] in (">", "/>"):
            return spans
        if raw[i] not in SPACE:
            return None
        while i < len(raw) and raw[i] in SPACE:
            i += 1
        if raw[i:] in (">", "/>"):
            return spans
        start = i
        while i < len(raw) and raw[i] not in SPACE + "=/>":
            if not (raw[i].isascii() and (raw[i].isalnum() or raw[i] in "_-:")):
                return None
            i += 1
        name = raw[start:i].lower()
        if not name or name in names:
            return None
        # '=' intentionally excludes matches from the ordinary text token
        # recognizer. Attribute names are structural and must still fail closed.
        if is_valid_candidate(name.upper()):
            unsupported()
        names.add(name)
        after_name = i
        while i < len(raw) and raw[i] in SPACE:
            i += 1
        if i >= len(raw) or raw[i] != "=":
            i = after_name
            continue
        i += 1
        while i < len(raw) and raw[i] in SPACE:
            i += 1
        if i >= len(raw):
            return None
        if raw[i] not in "\"'":
            while i < len(raw) and raw[i] not in SPACE + ">":
                if raw[i] in "\"'`<=":
                    return None
                i += 1
            continue
        quote = raw[i]
        start = i + 1
        i = raw.find(quote, start)
        if i < 0:
            return None
        safe = name in SAFE_ATTRIBUTES or name.startswith("data-") or name.startswith("aria-")
        safe |= name == "value" and tag in {"input", "option", "button", "li", "meter", "progress"}
        url = (tag in {"a", "area"} and name == "href") or (tag == "img" and name == "src")
        if safe or url:
            spans.append(ContextSpan(offset + start, offset + i, quote, url))
        i += 1
    return None


def contextual_segments(
    path: Path,
    encoding: str,
    *,
    should_cancel: CancellationCheck | None = None,
) -> Iterator[ContextSegment]:
    parser = _Contexts()
    pending = ""
    base = 0
    left = ""
    last = None

    def emitted(count, segment):
        nonlocal pending, base, left
        text = pending[:count]
        spans = tuple(
            ContextSpan(
                max(span.start, base) - base,
                min(span.end, base + count) - base,
                span.quote, span.url,
            )
            for span in parser.spans
            if span.start < base + count and span.end > base
        )
        result = ContextSegment(
            text, left, pending[count:count + 1] or segment.right_context,
            segment.processed_bytes, segment.total_bytes, spans,
        )
        for match, code in iter_candidates(result):
            if is_valid_candidate(code):
                result.context(match.start() - 1, match.end() - 1)
        parser.spans = [span for span in parser.spans if span.end > base + count]
        pending = pending[count:]
        base += count
        left = text[-1:]
        return result

    for segment in iter_html_segments(path, encoding, should_cancel=should_cancel):
        last = segment
        pending += segment.text
        try:
            parser.feed(segment.text)
        except (AssertionError, ValueError):
            unsupported()
        if len(parser.rawdata) > MAX_PENDING_CHARACTERS:
            unsupported()
        # Retain a segment ending in an incomplete construct until the parser
        # knows its whole context. Existing scanner boundaries keep codes whole.
        if not parser.rawdata:
            yield emitted(len(pending), segment)
        elif len(pending) > MAX_PENDING_CHARACTERS:
            unsupported()
    try:
        parser.close()
    except (AssertionError, ValueError):
        unsupported()
    if pending and last is not None:
        yield emitted(len(pending), last)


def escaped_value(value: str, span: ContextSpan) -> str:
    if "\x00" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        unsupported()
    result = escape(value, quote=False).replace("\r", "&#13;")
    if span.quote == '"':
        result = result.replace('"', "&quot;")
    elif span.quote == "'":
        result = result.replace("'", "&#39;")
    return result


def validate_url(value: str) -> None:
    # Validate the ENTIRE resulting attribute, including existing prefix/suffix
    # and entities, not just an individual replacement.
    decoded = unescape(value).strip("".join(chr(i) for i in range(33)))
    decoded = decoded.replace("\t", "").replace("\n", "").replace("\r", "")
    try:
        if urlsplit(decoded).scheme.lower() not in {"", "http", "https", "mailto"}:
            unsupported()
    except ValueError:
        unsupported()


def replace_candidates(
    segment: ContextSegment,
    replacement: Callable[[str, str], str],
    candidates: list[tuple[re.Match[str], str]] | None = None,
) -> str:
    """Escape each occurrence, not its cached mapping; splice original offsets.

    Complete quoted attributes are kept in one segment by contextual_segments.
    This lets URL checks account for existing prefixes/suffixes and lets entity
    boundary checks reject accidental interpretation across replacement edges.
    """
    edits: list[tuple[int, int, str]] = []
    attributes: dict[ContextSpan, list[tuple[int, int, str, str]]] = {}
    matches = candidates if candidates is not None else iter_candidates(segment)
    for match, normalized in matches:
        original = match.group(0)
        value = replacement(original, normalized)
        if value == original:
            continue
        start, end = match.start() - 1, match.end() - 1
        span = segment.context(start, end)
        escaped = escaped_value(value, span)
        edits.append((start, end, escaped))
        if span.quote:
            attributes.setdefault(span, []).append((start, end, escaped, value))

    for span, changes in attributes.items():
        encoded, expected = [], []
        cursor = span.start
        for start, end, escaped, value in changes:
            unchanged = segment.text[cursor:start]
            encoded.extend((unchanged, escaped))
            expected.extend((unescape(unchanged), value))
            cursor = end
        unchanged = segment.text[cursor:span.end]
        encoded.append(unchanged)
        expected.append(unescape(unchanged))
        result = "".join(encoded)
        if unescape(result) != "".join(expected):
            unsupported()
        if span.url:
            validate_url(result)

    pieces, cursor = [], 0
    for start, end, escaped in edits:
        pieces.extend((segment.text[cursor:start], escaped))
        cursor = end
    pieces.append(segment.text[cursor:])
    return "".join(pieces)
