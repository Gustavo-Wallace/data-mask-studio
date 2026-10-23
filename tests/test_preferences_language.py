import ctypes

import pytest

from data_mask_studio.preferences import (
    LanguagePreference, PreferencesService, detect_system_ui_languages,
    normalize_ui_language, resolve_language,
)
from data_mask_studio.preferences import language as module


@pytest.mark.parametrize("raw,expected", [
    ("pt-BR", "pt-BR"), (" PT_br ", "pt-BR"), ("pt_latn_br", "pt-Latn-BR"),
    ("en_us", "en-US"), ("pt_PT", "pt-PT"), ("zh_hant_tw", "zh-Hant-TW"),
    ("pt", "pt"), ("en-001", "en-001"), ("", None), ("BR", "br"),
    ("pt-BR.UTF-8", None), ("Portuguese_Brazil", None), ("pt-PT-BR", None),
    ("pt--BR", None), ("pt-Cyrl-BR", "pt-Cyrl-BR"), (None, None),
])
def test_ui_language_normalization_does_not_infer_a_region(raw, expected):
    assert normalize_ui_language(raw) == expected


@pytest.mark.parametrize("languages,expected", [
    (("pt-BR",), "pt-BR"), (("PT_br",), "pt-BR"), (("pt-Latn-BR", "en-US"), "pt-BR"),
    (("en-US",), "en"), (("pt-PT",), "en"), (("de-DE",), "en"),
    (("pt",), "en"), (("BR",), "en"), (("pt-Cyrl-BR",), "en"),
    (("en-US", "pt-BR"), "en"), (("pt-PT", "pt-BR"), "en"),
    (("invalid", "pt-BR"), "en"), (("", "pt_BR"), "pt-BR"), ((), "en"),
])
def test_system_resolution_uses_primary_ui_language_not_secondary_fallback(languages, expected):
    assert resolve_language(LanguagePreference.SYSTEM, languages) == expected


@pytest.mark.parametrize("preference,expected", [("en", "en"), ("pt-BR", "pt-BR")])
def test_explicit_override_never_queries_system_provider(tmp_path, preference, expected):
    def forbidden():
        pytest.fail("An explicit preference must bypass OS detection")

    service = PreferencesService(tmp_path / "prefs.json", ui_languages_provider=forbidden)
    assert service.resolve_language(preference) == expected
    assert not service.path.exists()


def test_provider_failure_is_safe_english_fallback_without_persisting(tmp_path):
    def failed():
        raise OSError("SYNTHETIC_PRIVATE_OS_DETAILS")

    service = PreferencesService(tmp_path / "prefs.json", ui_languages_provider=failed)
    assert service.resolve_language(service.load().preferences.language) == "en"
    assert not service.path.exists()


class NativeQuery:
    def __init__(self, *, failure=None):
        self.calls = 0
        self.failure = failure

    def __call__(self, flags, count, buffer, length):
        self.calls += 1
        assert flags == 0x8  # MUI_LANGUAGE_NAME; no regional locale API.
        data = "pt-BR\0en-US\0\0"
        if self.failure == "first" or self.failure == "second" and buffer is not None:
            return 0
        count._obj.value = 2
        length._obj.value = len(data)
        if self.failure == "oversized":
            length._obj.value = module._MAX_LANGUAGE_BUFFER + 1
            return 1
        if buffer is not None:
            if self.failure == "count":
                count._obj.value = 3
            if self.failure == "unterminated":
                data = data[:-1] + "X"
            if self.failure == "changed_size":
                length._obj.value = len(data) + 10
            for index, character in enumerate(data):
                buffer[index] = character
        return 1


def native_api(monkeypatch, query):
    class Kernel:
        GetUserPreferredUILanguages = query

    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setattr(module.ctypes, "WinDLL", lambda name, **kw: Kernel(), raising=False)


def test_windows_api_uses_size_query_and_complete_ordered_multistring(monkeypatch):
    query = NativeQuery()
    native_api(monkeypatch, query)
    assert detect_system_ui_languages() == ("pt-BR", "en-US")
    assert query.calls == 2
    assert query.restype is ctypes.wintypes.BOOL
    assert len(query.argtypes) == 4


@pytest.mark.parametrize("failure", ["first", "second", "oversized", "count", "unterminated", "changed_size"])
def test_windows_api_failures_or_invalid_native_output_fall_back_safely(monkeypatch, failure):
    query = NativeQuery(failure=failure)
    native_api(monkeypatch, query)
    assert detect_system_ui_languages() == ()
    assert resolve_language(LanguagePreference.SYSTEM, detect_system_ui_languages()) == "en"


def test_unavailable_native_api_falls_back_without_using_formatting_locale(monkeypatch):
    def denied(*args, **kwargs):
        raise OSError("synthetic denied API")

    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setattr(module.ctypes, "WinDLL", denied, raising=False)
    assert detect_system_ui_languages() == ()
    monkeypatch.setattr(module.sys, "platform", "linux")
    assert detect_system_ui_languages() == ()


def test_native_ui_language_query_smoke():
    # Read-only OS metadata for this execution account, not the interactive
    # user's DMS environment. Policy tests above use synthetic native results.
    languages = detect_system_ui_languages()
    assert isinstance(languages, tuple)
    assert all(isinstance(tag, str) and tag for tag in languages)
    assert resolve_language(LanguagePreference.SYSTEM, languages) in {"en", "pt-BR"}
