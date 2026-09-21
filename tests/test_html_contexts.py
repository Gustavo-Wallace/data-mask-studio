"""F02 regressions: parse output structure and semantic values, not just escapes."""

from html.parser import HTMLParser

import pytest

from data_mask_studio.html_restoration import (
    HTMLUnsupportedContextError, analyze_html, inspect_html, restore_html,
)
from data_mask_studio.html_restoration import scanner
from data_mask_studio.performance import HTMLProcessingMetrics
from data_mask_studio.vault import MappingCandidate, VaultCipher, VaultRepository

CODE = "TEXT-DEFGHI234ABC"


class Parsed(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.tags, self.ends, self.data, self.comments = [], [], [], []
        self.feed(source)
        self.close()

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, attrs))

    def handle_endtag(self, tag):
        self.ends.append(tag)

    def handle_data(self, data):
        self.data.append(data)

    def handle_comment(self, data):
        self.comments.append(data)


def repository_for(tmp_path, value):
    repository = VaultRepository(tmp_path / "vault.db", VaultCipher(b"X" * 32))
    with repository.transaction() as transaction:
        transaction.upsert_batch([MappingCandidate(CODE, "TEXT", value, "Synthetic")])
    return repository


@pytest.mark.parametrize("value", [
    "texto comum", "<img src=x onerror=alert(1)>",
    "</script><script>alert(1)</script>", 'x" onclick="alert(1)',
    "' onmouseover='alert(1)", "<!--\n-->", "&amp;", "A & B",
    '\"><svg onload=alert(1)>', "< > & \" ' = \t\n\r\n",
    "ação 😀 e\u0301 𐐀", "https://example.invalid/a?q=1&b=2",
])
def test_same_mapping_round_trips_as_data_in_three_contexts(tmp_path, value):
    repository = repository_for(tmp_path, value)
    source = tmp_path / "source.html"
    source.write_text(
        f'<p>{CODE}</p><div title="{CODE}"></div><i data-name=\'{CODE}\'></i>',
        encoding="utf-8", newline="",
    )
    destination = tmp_path / "output.html"
    before = repository.database_path.read_bytes()
    result = restore_html(inspect_html(source), destination, repository)
    parsed = Parsed(destination.read_bytes().decode("utf-8"))
    assert parsed.tags == [("p", []), ("div", [("title", value)]), ("i", [("data-name", value)])]
    assert parsed.ends == ["p", "div", "i"]
    assert "".join(parsed.data) == value
    assert parsed.comments == []
    assert result.restored_occurrences == 3
    assert repository.database_path.read_bytes() == before
    # No surviving token: a second restoration must neither activate nor double-escape.
    second = tmp_path / "second.html"
    result = restore_html(inspect_html(destination), second, repository)
    assert result.restored_occurrences == 0
    assert second.read_bytes() == destination.read_bytes()


@pytest.mark.parametrize("template", [
    '<script>const x = "TOKEN";</script>',
    '<script type="application/json">{"x":"TOKEN"}</script>',
    '<style>.x {content:"TOKEN"}</style>', '<!-- TOKEN -->',
    '<div data-value=TOKEN></div>', '<TOKEN>text</TOKEN>',
    '<div TOKEN="x"></div>', '<!doctype TOKEN>', '<?test TOKEN?>',
    '<textarea>TOKEN</textarea>', '<title>TOKEN</title>',
    '<xmp>TOKEN</xmp>', '<plaintext>TOKEN', '<noscript>TOKEN</noscript>',
    '<svg><text>TOKEN</text></svg>', '<math><mtext>TOKEN</mtext></math>',
    '<template>TOKEN</template>', '<iframe>TOKEN</iframe>',
    '<div onclick="TOKEN"></div>', '<div style="TOKEN"></div>',
    '<iframe srcdoc="TOKEN"></iframe>', '<script src="TOKEN"></script>',
    '<div data-value="TOKEN', '<div data-value="x"data-other="TOKEN"></div>',
    '<script/>TOKEN', '<script><!--<script></script><p>TOKEN</p>',
    '<script></script title=\">\"><p>TOKEN</p>',
    '<script TOKEN="x"></script>', '<pre>TOKEN</pre>', '<listing>TOKEN</listing>',
])
def test_unsupported_context_fails_analysis_and_restoration_atomically(tmp_path, template):
    repository = repository_for(tmp_path, "<sensitive>")
    source = tmp_path / "source.html"
    source.write_text(template.replace("TOKEN", CODE), encoding="utf-8")
    source_before = source.read_bytes()
    vault_before = repository.database_path.read_bytes()
    destination = tmp_path / "output.html"
    for operation in (
        lambda: analyze_html(inspect_html(source), repository),
        lambda: restore_html(inspect_html(source), destination, repository),
    ):
        with pytest.raises(HTMLUnsupportedContextError) as raised:
            operation()
        assert CODE not in str(raised.value)
        assert "sensitive" not in str(raised.value)
    assert not destination.exists()
    assert not list(tmp_path.glob("*.tmp"))
    assert source.read_bytes() == source_before
    assert repository.database_path.read_bytes() == vault_before


@pytest.mark.parametrize("value,prefix", [
    ("javascript:alert(1)", ""), ("data:text/html,test", ""),
    ("script:alert(1)", "java"), ("\tjava\nscript:alert(1)", ""),
])
def test_url_attribute_rejects_active_scheme_in_complete_result(tmp_path, value, prefix):
    repository = repository_for(tmp_path, value)
    source = tmp_path / "source.html"
    # A space permits the unchanged token recognizer to find the token after a prefix.
    # An entity contributes the prefix without an alphanumeric token boundary.
    prefix_source = "&#106;ava&#x09;" if prefix else ""
    source.write_text(f'<a href="{prefix_source}{CODE}">link</a>', encoding="utf-8")
    destination = tmp_path / "output.html"
    with pytest.raises(HTMLUnsupportedContextError):
        restore_html(inspect_html(source), destination, repository)
    assert not destination.exists()
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("url", ["https://example.invalid/?a=1&b=2", "/relative/path", "mailto:a@example.invalid"])
def test_benign_url_attribute(tmp_path, url):
    repository = repository_for(tmp_path, url)
    source = tmp_path / "source.html"
    source.write_text(f'<a href="{CODE}">link</a>', encoding="utf-8")
    destination = tmp_path / "output.html"
    restore_html(inspect_html(source), destination, repository)
    assert Parsed(destination.read_text(encoding="utf-8")).tags == [("a", [("href", url)])]


def test_source_preservation_and_cached_escaping_across_small_chunks(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner, "CHUNK_SIZE", 61)
    monkeypatch.setattr(scanner, "OVERLAP_SIZE", 20)
    value = '"\' &amp; <b>'
    repository = repository_for(tmp_path, value)
    source = tmp_path / "source.html"
    original = (
        "<!DOCTYPE html>\r\n<!-- untouched -->\r\n<p>A &amp; B " + CODE + "</p>"
        + " " * 200 + f'<div  title = \'{CODE}\' data-x="unchanged &amp;">{CODE}</div>'
    )
    source.write_bytes(b"\xef\xbb\xbf" + original.encode())
    destination = tmp_path / "output.html"
    metrics = HTMLProcessingMetrics()
    restore_html(inspect_html(source), destination, repository, metrics=metrics)
    output = destination.read_bytes().decode("utf-8-sig")
    parsed = Parsed(output)
    assert parsed.tags == [("p", []), ("div", [("title", value), ("data-x", "unchanged &")])]
    assert "".join(parsed.data) == "\r\n\r\nA & B " + value + " " * 200 + value
    assert output.startswith("<!DOCTYPE html>\r\n<!-- untouched -->\r\n<p>A &amp; B ")
    assert "<div  title = '" in output
    assert destination.read_bytes().startswith(b"\xef\xbb\xbf")
    assert metrics.vault.cache_hits >= 2


def test_late_unsupported_context_preserves_existing_destination(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner, "CHUNK_SIZE", 128)
    repository = repository_for(tmp_path, "safe")
    source = tmp_path / "source.html"
    source.write_text(f"<p>{CODE}</p>" + " " * 1000 + f"<!-- {CODE} -->", encoding="utf-8")
    destination = tmp_path / "output.html"
    destination.write_bytes(b"previous complete output")
    with pytest.raises(HTMLUnsupportedContextError):
        restore_html(inspect_html(source), destination, repository, overwrite=True)
    assert destination.read_bytes() == b"previous complete output"
    assert not list(tmp_path.glob("*.tmp"))


def test_missing_token_in_unsafe_context_is_not_silently_kept(tmp_path):
    repository = repository_for(tmp_path, "safe")
    source = tmp_path / "source.html"
    source.write_text('<script>"NONE-ABCDEFGHI234"</script>', encoding="utf-8")
    with pytest.raises(HTMLUnsupportedContextError):
        restore_html(inspect_html(source), tmp_path / "output.html", repository)


def test_attribute_entity_cannot_form_across_replacement_boundary(tmp_path):
    repository = repository_for(tmp_path, "amp")
    source = tmp_path / "source.html"
    source.write_text(f'<div title="&{CODE};"></div>', encoding="utf-8")
    with pytest.raises(HTMLUnsupportedContextError):
        restore_html(inspect_html(source), tmp_path / "output.html", repository)
    assert not (tmp_path / "output.html").exists()


def test_bounded_pending_construct_fails_closed(tmp_path, monkeypatch):
    from data_mask_studio.html_restoration import context
    monkeypatch.setattr(context, "MAX_PENDING_CHARACTERS", 200)
    monkeypatch.setattr(scanner, "CHUNK_SIZE", 128)
    repository = repository_for(tmp_path, "safe")
    source = tmp_path / "source.html"
    source.write_text('<div title="' + "x" * 1000 + CODE, encoding="utf-8")
    with pytest.raises(HTMLUnsupportedContextError):
        restore_html(inspect_html(source), tmp_path / "output.html", repository)
    assert not list(tmp_path.glob("*.tmp"))


def test_cancel_while_parser_buffers_incomplete_tag(tmp_path, monkeypatch):
    from data_mask_studio.html_restoration import HTMLRestorationCancelled
    monkeypatch.setattr(scanner, "CHUNK_SIZE", 128)
    repository = repository_for(tmp_path, "safe")
    source = tmp_path / "source.html"
    source.write_text('<div title="' + "x" * 1000 + CODE + '"></div>', encoding="utf-8")
    calls = 0

    def cancel():
        nonlocal calls
        calls += 1
        return calls > 3

    with pytest.raises(HTMLRestorationCancelled):
        restore_html(inspect_html(source), tmp_path / "output.html", repository, should_cancel=cancel)
    assert not (tmp_path / "output.html").exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_html_operations_obey_environment_generation_and_recovery_fence(tmp_path):
    from data_mask_studio.environment import EnvironmentError, GENERATION_FILE, RESTORE_JOURNAL
    repository = repository_for(tmp_path, "safe")
    source = tmp_path / "source.html"
    source.write_text(f'<p>{CODE}</p>', encoding="utf-8")
    destination = tmp_path / "output.html"
    (tmp_path / RESTORE_JOURNAL).write_bytes(b"pending recovery")
    for operation in (
        lambda: analyze_html(inspect_html(source), repository),
        lambda: restore_html(inspect_html(source), destination, repository),
    ):
        with pytest.raises(EnvironmentError):
            operation()
    (tmp_path / RESTORE_JOURNAL).unlink()
    (tmp_path / GENERATION_FILE).write_bytes(b"new-generation")
    for operation in (
        lambda: analyze_html(inspect_html(source), repository),
        lambda: restore_html(inspect_html(source), destination, repository),
    ):
        with pytest.raises(EnvironmentError):
            operation()
    assert not destination.exists()
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("chunk_size", [10, 16, 31, 64, 128, 257])
def test_contexts_survive_chunk_splits_inside_tags_quotes_and_codes(tmp_path, monkeypatch, chunk_size):
    monkeypatch.setattr(scanner, "CHUNK_SIZE", chunk_size)
    monkeypatch.setattr(scanner, "OVERLAP_SIZE", 8)
    value = '\"><img src=x onerror=alert(1)> &amp;'
    repository = repository_for(tmp_path, value)
    source = tmp_path / "source.html"
    original = f'<p title="{CODE}">{CODE}</p>' * 5
    source.write_text(original, encoding="utf-8")
    destination = tmp_path / "output.html"
    restore_html(inspect_html(source), destination, repository)
    parsed = Parsed(destination.read_text(encoding="utf-8"))
    assert parsed.tags == [("p", [("title", value)])] * 5
    assert "".join(parsed.data) == value * 5


def test_unrepresentable_nul_fails_without_lossy_sanitization(tmp_path):
    repository = repository_for(tmp_path, "a\x00b")
    source = tmp_path / "source.html"
    source.write_text(f'<p>{CODE}</p>', encoding="utf-8")
    with pytest.raises(HTMLUnsupportedContextError):
        restore_html(inspect_html(source), tmp_path / "output.html", repository)
    assert not (tmp_path / "output.html").exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_html_holds_shared_environment_lease_through_processing(tmp_path):
    from data_mask_studio.environment import EnvironmentError, environment_lease
    repository = repository_for(tmp_path, "safe")
    source = tmp_path / "source.html"
    source.write_text(f'<p>{CODE}</p>', encoding="utf-8")
    checks = []

    def progress(_):
        with pytest.raises(EnvironmentError):
            with environment_lease(tmp_path, exclusive=True):
                pass
        checks.append(True)

    restore_html(inspect_html(source), tmp_path / "output.html", repository, progress_callback=progress)
    assert checks
    with environment_lease(tmp_path, exclusive=True):
        pass
