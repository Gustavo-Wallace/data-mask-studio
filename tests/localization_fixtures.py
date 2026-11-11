"""Opt-in immutable presentation data; no application/GUI state is shared."""

from types import MappingProxyType

import pytest

from data_mask_studio.i18n import SUPPORTED_LOCALES, Translator


@pytest.fixture(scope="module")
def presentation_translators():
    # Each module loads its own read-only pool through the real catalog API.
    # Fresh constructors and catalog-loading tests are deliberately unaffected.
    return MappingProxyType({locale: Translator(locale) for locale in SUPPORTED_LOCALES})
